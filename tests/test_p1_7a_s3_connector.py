"""P1-7a: the cloud connector, proven end to end against a local S3 stand-in.

P1-7 was ``NOT STARTED`` while the code already published templates, routes and
an upload plan: nothing had ever started a server, listed its tools, written an
object or watched a link expire. These tests do exactly that, with
``tests/fake_s3_mcp.py`` in place of ``npx -y mcp-server-s3`` - no AWS account,
no network beyond loopback, nothing to clean up on the runner.

The connection goes through the **real ``s3`` template** (``mcp_cloud.build``):
its field validation, its secret/public split and its ``allow`` list are the
ones under test. Only the executable is swapped, because the package it names
cannot be installed offline - and the fake deliberately publishes one tool the
template does not allow (``delete_bucket``), so "filtered" is observed rather
than assumed: the same fake exposes eight tools when no allow list applies.

What each one pins:

* the secrets path - ``config.toml`` is what people paste into issues, so the
  keys must live in ``mcp-secrets.toml`` and still reach the child process;
* the two credentials refusals, which are different failures: no key at all
  (``InvalidAccessKeyId``) and a key too short to sign with
  (``SignatureDoesNotMatch``);
* the template's ``allow`` list removes a tool the server really offers, and the
  control test shows the same server without an allow list hands it over;
* a write, a read and a listing of the same object;
* the expiry of a presigned link, observed as a 403 from a loopback URL;
* a refused upload that leaves the local artefact untouched;
* a reconnect that finds what the previous process wrote.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Tuple

import httpx
import pytest

from uh import mcp, mcp_cloud
from uh._compat import tomllib

FAKE = Path(__file__).with_name("fake_s3_mcp.py")
NAME = "s3local"
ACCESS = "AKIAFAKECLOUD0001"
SECRET = "abcd1234efgh5678"
BUCKET = "uh-test"

#: What the ``s3`` template allows (``mcp_cloud.TEMPLATES["s3"].allow``).
ALLOWED = (
    "bucket_info",
    "delete_object",
    "get_object",
    "list_buckets",
    "list_objects",
    "presigned_url",
    "put_object",
)
#: Published by the fake, absent from the template's allow list.
FORBIDDEN = "delete_bucket"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """An isolated ``$UH_HOME``: config, secrets and the fake store inside it."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    monkeypatch.setenv("FAKE_S3_STORE", str(tmp_path / "fake-s3-store.json"))
    yield tmp_path
    mcp.shutdown()  # the registry caches clients and failures across tests


def connect_template(*, readonly: bool = False, secret: str = SECRET) -> Dict[str, Any]:
    """Connect through the real ``s3`` template; only the executable is replaced.

    ``mcp_cloud.build`` is what validates the fields, decides which values are
    secrets and attaches ``allow``; the entry it returns is then pointed at the
    fake. Skipping the template (``connect_custom``) would test a connector that
    nobody gets from the panel, which is why the filter test uses this path.
    """
    entry, values = mcp_cloud.build(
        "s3",
        NAME,
        {
            "AWS_ACCESS_KEY_ID": ACCESS,
            "AWS_SECRET_ACCESS_KEY": secret,
            "AWS_REGION": "eu-west-3",
            "S3_BUCKET": BUCKET,
        },
    )
    entry["command"] = sys.executable
    entry["args"] = [str(FAKE)]
    if readonly:
        entry.setdefault("env", {})["FAKE_S3_READONLY"] = "1"
    mcp_cloud.add_server(entry)
    mcp_cloud.write_secrets(NAME, values)
    return entry


def connect_custom(env: Dict[str, str]) -> Dict[str, Any]:
    """Register the same fake with no template, therefore no ``allow`` list."""
    return mcp_cloud.connect_custom(NAME, sys.executable, [str(FAKE)], env)


@contextlib.contextmanager
def toolset() -> Generator[Tuple[mcp.Client, mcp.Toolset], None, None]:
    """The entry as UH reads it back: parse, spawn, handshake, tools listed."""
    servers, problems = mcp.servers_from_config()
    assert not problems, problems
    server = next(item for item in servers if item.name == NAME)
    client = mcp.client_for(server)
    try:
        yield client, mcp.Toolset(client)
    finally:
        client.close()


def payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """The structured half of a tool answer, with the error spelled out if any."""
    assert "error" not in result, result.get("error")
    structured = result.get("structured")
    assert isinstance(structured, dict), result
    return structured


def test_the_secrets_stay_out_of_config_toml(home: Path) -> None:
    connect_template()

    config_text = (home / "config.toml").read_text(encoding="utf-8")
    assert ACCESS not in config_text, "the access key is a secret: it cannot live here"
    assert SECRET not in config_text
    assert 'AWS_REGION = "eu-west-3"' in config_text, "non-secrets stay readable"
    assert f'S3_BUCKET = "{BUCKET}"' in config_text

    secrets_text = (home / mcp_cloud.SECRETS_FILE).read_text(encoding="utf-8")
    assert ACCESS in secrets_text and SECRET in secrets_text

    parsed = tomllib.loads(config_text)
    entry = parsed["mcp"]["servers"][0]
    assert "AWS_ACCESS_KEY_ID" not in entry.get("env", {})


def test_a_server_with_no_credentials_at_all_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No AWS_* in the child's environment: the server names the missing key."""
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    connect_custom({"AWS_REGION": "eu-west-3", "S3_BUCKET": BUCKET})

    with toolset() as (_client, tools):
        result = tools.call(
            f"mcp__{NAME}__put_object", {"bucket": BUCKET, "key": "a.md", "content": "x"}
        )

    assert result.get("kind") == "tool", "a refused call must surface as a tool error"
    assert "InvalidAccessKeyId" in str(result.get("error"))


def test_a_secret_too_short_to_sign_with_is_refused(home: Path) -> None:
    """A key that exists but cannot sign is a different refusal, by design."""
    connect_template(secret="court")

    with toolset() as (_client, tools):
        result = tools.call(
            f"mcp__{NAME}__put_object", {"bucket": BUCKET, "key": "a.md", "content": "x"}
        )

    assert result.get("kind") == "tool"
    assert "SignatureDoesNotMatch" in str(result.get("error"))


def test_the_template_allow_list_hides_a_tool_the_server_offers(home: Path) -> None:
    """The fake publishes eight tools; the template's list must only pass seven."""
    entry = connect_template()
    assert FORBIDDEN not in entry["allow"], "the template must not allow it"
    assert set(ALLOWED) == set(entry["allow"])

    with toolset() as (_client, tools):
        names = sorted(spec["function"]["name"] for spec in tools.tools)
        refused = tools.call(f"mcp__{NAME}__{FORBIDDEN}", {"bucket": BUCKET})

    assert names == sorted(f"mcp__{NAME}__{tool}" for tool in ALLOWED), names
    assert f"mcp__{NAME}__{FORBIDDEN}" not in names
    assert refused.get("kind") == "invalid_request"
    assert "unknown MCP tool" in str(refused.get("error"))


def test_without_an_allow_list_the_same_server_hands_over_eight(home: Path) -> None:
    """Control: the tool exists, so the test above proves a filter and not a typo."""
    connect_custom(
        {
            "AWS_ACCESS_KEY_ID": ACCESS,
            "AWS_SECRET_ACCESS_KEY": SECRET,
            "AWS_REGION": "eu-west-3",
            "S3_BUCKET": BUCKET,
        }
    )

    with toolset() as (_client, tools):
        names = sorted(spec["function"]["name"] for spec in tools.tools)

    assert len(names) == len(ALLOWED) + 1, names
    assert f"mcp__{NAME}__{FORBIDDEN}" in names


def test_an_object_is_written_then_read_back(home: Path) -> None:
    connect_template()

    with toolset() as (_client, tools):
        written = payload(
            tools.call(
                f"mcp__{NAME}__put_object",
                {"bucket": BUCKET, "key": "rapport.md", "content": "# titre\n"},
            )
        )
        read = payload(
            tools.call(f"mcp__{NAME}__get_object", {"bucket": BUCKET, "key": "rapport.md"})
        )
        listed = payload(tools.call(f"mcp__{NAME}__list_objects", {"bucket": BUCKET}))

    assert written["bytes"] == len("# titre\n".encode("utf-8"))
    assert read["content"] == "# titre\n"
    assert listed["keys"] == ["rapport.md"]


def test_a_presigned_link_expires(home: Path) -> None:
    """One link serves the object, another is already dead: no clock to race.

    The first version asked for a one-second link, fetched it, slept, fetched it
    again - and on a loaded CI runner the first fetch arrived after the deadline.
    Two links, each with its own lifetime, make the same proof without depending
    on how fast the machine is.
    """
    connect_template()

    with toolset() as (_client, tools):
        payload(
            tools.call(
                f"mcp__{NAME}__put_object",
                {"bucket": BUCKET, "key": "lien.md", "content": "contenu"},
            )
        )
        live = payload(
            tools.call(
                f"mcp__{NAME}__presigned_url",
                {"bucket": BUCKET, "key": "lien.md", "expires_in": 60},
            )
        )
        # A second link that is already dead when it is handed out: the refusal is
        # observed without racing a clock, which is what broke on a loaded runner
        # when this test used `expires_in: 1` and a sleep.
        expired = payload(
            tools.call(
                f"mcp__{NAME}__presigned_url",
                {"bucket": BUCKET, "key": "lien.md", "expires_in": 0},
            )
        )

        assert live["url"].startswith("http://127.0.0.1:")
        fresh = httpx.get(live["url"], timeout=5.0)
        stale = httpx.get(expired["url"], timeout=5.0)

        # The object itself is untouched: the link expired, the data did not.
        still = payload(
            tools.call(f"mcp__{NAME}__get_object", {"bucket": BUCKET, "key": "lien.md"})
        )

    assert fresh.status_code == 200
    assert fresh.text == "contenu"
    assert stale.status_code == 403, "an expired link must not serve the object"
    assert "expire" in stale.text
    assert still["content"] == "contenu"


def test_a_refused_upload_leaves_the_local_result_alone(home: Path) -> None:
    """A refused remote write must not erase what UH already produced."""
    connect_template(readonly=True)
    document = home / "plan.md"
    document.write_text("# plan local\n", encoding="utf-8")

    plan = mcp_cloud.upload_plan(NAME, "put_object", document)

    assert plan["ok"] is False
    assert "AccessDenied" in str(plan["error"])
    assert document.is_file(), "the local result survives a refused upload"
    assert document.read_text(encoding="utf-8") == "# plan local\n"


def test_the_connector_reconnects_and_finds_its_objects(home: Path) -> None:
    connect_template()
    with toolset() as (_client, tools):
        payload(
            tools.call(
                f"mcp__{NAME}__put_object",
                {"bucket": BUCKET, "key": "avant.md", "content": "écrit avant la coupure"},
            )
        )

    mcp.shutdown()  # the process is gone, exactly like a crash or a restart

    with toolset() as (client, tools):
        after = payload(
            tools.call(f"mcp__{NAME}__get_object", {"bucket": BUCKET, "key": "avant.md"})
        )
        buckets: List[str] = payload(tools.call(f"mcp__{NAME}__list_buckets"))["buckets"]
        assert client.alive, "the second connection is a new, live process"

    assert after["content"] == "écrit avant la coupure"
    assert BUCKET in buckets
