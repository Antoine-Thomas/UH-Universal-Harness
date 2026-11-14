"""Cloud templates: a form becomes a server, and a token never lands in config.toml."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from uh import mcp, mcp_cloud


def test_the_catalogue_names_the_packages_it_installs() -> None:
    templates = {item["id"]: item for item in mcp_cloud.template_list()}

    assert "s3" in templates
    assert templates["s3"]["can_upload"] is True, "S3 publishes put_object"
    assert any(field["secret"] for field in templates["s3"]["fields"])
    assert templates["gdrive"]["can_upload"] is False, (
        "the Google Drive server reads; promising an upload would be a lie"
    )


def test_a_field_left_empty_is_refused_by_name() -> None:
    with pytest.raises(mcp_cloud.CloudError) as caught:
        mcp_cloud.build("s3", "cloud", {"AWS_ACCESS_KEY_ID": "AKIA"})

    assert "AWS_SECRET_ACCESS_KEY" in str(caught.value)


def test_an_unknown_template_says_what_exists() -> None:
    with pytest.raises(mcp_cloud.CloudError) as caught:
        mcp_cloud.template("dropbox")

    assert "s3" in str(caught.value)


def test_secrets_are_not_in_config_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """config.toml is the file people paste into issues."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))

    created = mcp_cloud.connect(
        "s3",
        "cloud",
        {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "shhh-secret",
            "AWS_REGION": "eu-west-3",
            "S3_BUCKET": "mes-docs",
        },
    )

    config_text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "shhh-secret" not in config_text
    assert "AKIAEXAMPLE" not in config_text
    assert 'AWS_REGION = "eu-west-3"' in config_text, "non-secrets stay readable"
    assert created["can_upload"] is True

    secrets = (tmp_path / mcp_cloud.SECRETS_FILE).read_text(encoding="utf-8")
    assert "shhh-secret" in secrets


def test_the_entry_is_read_back_by_the_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A panel that says "connected" on unparsable TOML is a lie: read it back."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    mcp_cloud.connect(
        "s3",
        "cloud",
        {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "shhh-secret",
            "AWS_REGION": "eu-west-3",
        },
    )

    servers, problems = mcp.servers_from_config()

    assert not problems
    entry = next(server for server in servers if server.name == "cloud")
    assert entry.env["AWS_SECRET_ACCESS_KEY"] == "shhh-secret", "merged at spawn"
    assert entry.env["AWS_REGION"] == "eu-west-3"
    assert "put_object" in (entry.allow or ())


def test_adding_a_cloud_keeps_the_servers_already_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text(
        "# my file, hand-written\n"
        'workspace = "C:/work"\n'
        "\n"
        "[[mcp.servers]]\n"
        'name = "atelier"\n'
        'command = "npx"\n'
        'args = ["-y", "@modelcontextprotocol/server-filesystem", "C:/docs"]\n',
        encoding="utf-8",
    )

    mcp_cloud.connect(
        "s3",
        "cloud",
        {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "shhh",
            "AWS_REGION": "eu-west-3",
        },
    )

    text = config.read_text(encoding="utf-8")
    assert "# my file, hand-written" in text, "comments survive"
    assert 'workspace = "C:/work"' in text
    assert text.count("[[mcp.servers]]") == 2
    parsed = tomllib.loads(text)
    assert [entry["name"] for entry in parsed["mcp"]["servers"]] == ["atelier", "cloud"]


def test_connecting_twice_replaces_the_same_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    fields = {
        "AWS_ACCESS_KEY_ID": "AKIA1",
        "AWS_SECRET_ACCESS_KEY": "s1",
        "AWS_REGION": "eu-west-3",
    }
    mcp_cloud.connect("s3", "cloud", fields)
    mcp_cloud.connect("s3", "cloud", {**fields, "AWS_REGION": "us-east-1"})

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert text.count("[[mcp.servers]]") == 1
    assert "us-east-1" in text


def test_only_a_server_that_can_write_is_an_upload_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    mcp_cloud.connect(
        "gdrive",
        "drive",
        {"GDRIVE_OAUTH_PATH": "a.json", "GDRIVE_CREDENTIALS_PATH": "b.json"},
    )

    assert mcp_cloud.upload_targets() == [], "Drive reads; it must not be offered"

    mcp_cloud.connect(
        "s3",
        "cloud",
        {"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "B", "AWS_REGION": "eu-west-3"},
    )
    targets = {item["name"]: item["tool"] for item in mcp_cloud.upload_targets()}
    assert targets == {"cloud": "put_object"}


def test_a_bad_name_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))

    with pytest.raises(mcp_cloud.CloudError):
        mcp_cloud.connect(
            "s3",
            "Mon Cloud!",
            {"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "B", "AWS_REGION": "r"},
        )

    assert not (tmp_path / "config.toml").exists()

def test_the_self_repair_template_cannot_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server that can read our source must not be able to rewrite it."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()

    mcp_cloud.connect("self-repair", "self-repair", {"REPO_PATH": str(repo)})

    servers, problems = mcp.servers_from_config()
    assert not problems
    entry = next(server for server in servers if server.name == "self-repair")
    assert str(repo) in entry.args, "the path is an argument of the filesystem server"
    assert entry.allow, "an allowlist is mandatory"
    forbidden = {"write_file", "edit_file", "create_directory", "move_file"}
    assert not (forbidden & set(entry.allow or ())), "no write tool in a read-only server"


def test_the_self_repair_server_cannot_see_the_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It reads the checkout; ~/.uh (keys, mcp-secrets) is not in that folder."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    mcp_cloud.connect("self-repair", "self-repair", {"REPO_PATH": str(repo)})

    entry = mcp_cloud.read_secrets()
    assert "self-repair" not in entry, "a read-only server needs no secret"

    config = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert str(tmp_path / mcp_cloud.SECRETS_FILE) not in config
    assert "keys.toml" not in config
