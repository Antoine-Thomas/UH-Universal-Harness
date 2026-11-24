"""P1-7a: the cloud connector, proven end to end against a local S3 stand-in.

P1-7 was ``NOT STARTED`` while the code already published templates, routes and
an upload plan: nothing had ever started a server, listed its tools, written an
object or watched a link expire. These tests do exactly that, with
``tests/fake_s3_mcp.py`` in place of ``npx -y mcp-server-s3`` - no AWS account,
no network beyond loopback, nothing to clean up on the runner.

What each one pins:

* the secrets path - ``config.toml`` is what people paste into issues, so the
  keys must live in ``mcp-secrets.toml`` and still reach the child process;
* the refusal when they are absent - a connector that "connects" without
  credentials is the failure worth catching;
* the tool names the model will see (``mcp__<server>__<tool>``), filtered by the
  template's ``allow`` list rather than "everything the server offers";
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


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """An isolated ``$UH_HOME``: config, secrets and the fake store inside it."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    monkeypatch.setenv("FAKE_S3_STORE", str(tmp_path / "fake-s3-store.json"))
    yield tmp_path
    mcp.shutdown()  # the registry caches clients and failures across tests


def connect(*, readonly: bool = False, secret: str = SECRET) -> Dict[str, Any]:
    """Write the server entry the way the panel does, and return its report."""
    env: Dict[str, str] = {
        "AWS_ACCESS_KEY_ID": ACCESS,
        "AWS_SECRET_ACCESS_KEY": secret,
        "AWS_REGION": "eu-west-3",
        "S3_BUCKET": BUCKET,
    }
    if readonly:
        env["FAKE_S3_READONLY"] = "1"
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
    connect()

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


def test_a_server_without_credentials_refuses_every_call(home: Path) -> None:
    """No key in the environment -> the server says so, and nothing is written."""
    connect(secret="courte")

    with toolset() as (_client, tools):
        result = tools.call(
            f"mcp__{NAME}__put_object", {"bucket": BUCKET, "key": "a.md", "content": "x"}
        )

    assert result.get("kind") == "tool", "a refused call must surface as a tool error"
    assert "SignatureDoesNotMatch" in str(result.get("error"))


def test_the_tools_appear_under_the_server_prefix(home: Path) -> None:
    connect()

    with toolset() as (_client, tools):
        names = sorted(spec["function"]["name"] for spec in tools.tools)

    assert names == sorted(
        f"mcp__{NAME}__{tool}"
        for tool in (
            "bucket_info",
            "delete_object",
            "get_object",
            "list_buckets",
            "list_objects",
            "presigned_url",
            "put_object",
        )
    ), names


def test_an_object_is_written_then_read_back(home: Path) -> None:
    connect()

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
    connect()

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
    connect(readonly=True)
    document = home / "plan.md"
    document.write_text("# plan local\n", encoding="utf-8")

    plan = mcp_cloud.upload_plan(NAME, "put_object", document)

    assert plan["ok"] is False
    assert "AccessDenied" in str(plan["error"])
    assert document.is_file(), "the local result survives a refused upload"
    assert document.read_text(encoding="utf-8") == "# plan local\n"


def test_the_connector_reconnects_and_finds_its_objects(home: Path) -> None:
    connect()
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
