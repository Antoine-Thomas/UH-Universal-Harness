"""Cloning a provider: a copy of what works, without ever handing back the key.

The point is a second variant - another model, another key - of an entry the user
already got working. What must not happen: the key leaving the answer, the name
colliding, or a clone that silently loses the URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import keys
from uh.providers_admin import AdminError, clone_provider, save_provider

BASE = "https://api.example.com/v1"
KEY = "sk-alpha-0123456789abcdef"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    save_provider("alpha", BASE, "model-a", KEY)
    return tmp_path


def test_a_clone_carries_the_url_the_model_and_the_key(home: Path) -> None:
    result = clone_provider("alpha", "alpha-2")

    assert result["name"] == "alpha-2"
    assert result["base_url"] == BASE
    assert result["model"] == "model-a"
    assert result["keys"] == 1
    assert result["cloned_from"] == "alpha"
    copied = keys.get_provider("alpha-2")
    assert copied is not None
    assert copied["base_url"] == BASE
    assert copied["keys"] == [KEY]


def test_the_answer_never_contains_the_key(home: Path) -> None:
    result = clone_provider("alpha", "alpha-2")
    assert KEY not in str(result)
    assert "key" not in {part for part in result if part != "keys"}


def test_the_original_still_exists(home: Path) -> None:
    clone_provider("alpha", "alpha-2")
    assert keys.get_provider("alpha") is not None


def test_a_taken_name_is_refused(home: Path) -> None:
    with pytest.raises(AdminError) as caught:
        clone_provider("alpha", "alpha")
    assert "autre nom" in str(caught.value) or "existe" in str(caught.value)


def test_an_existing_provider_name_is_refused(home: Path) -> None:
    save_provider("beta", BASE, "model-b", KEY)
    with pytest.raises(AdminError) as caught:
        clone_provider("alpha", "beta")
    assert "existe déjà" in str(caught.value)


def test_an_unknown_source_is_a_404_kind(home: Path) -> None:
    with pytest.raises(AdminError) as caught:
        clone_provider("ghost", "ghost-2")
    assert caught.value.kind == "unknown"


def test_a_catalogue_name_cannot_be_a_clone_name(home: Path) -> None:
    with pytest.raises(AdminError):
        clone_provider("alpha", "deepseek")


def test_an_invalid_name_is_refused(home: Path) -> None:
    with pytest.raises(AdminError):
        clone_provider("alpha", "Pas Un Nom !")
