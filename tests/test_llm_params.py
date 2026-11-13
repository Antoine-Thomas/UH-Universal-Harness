"""Conversation settings: the table, the clamping, and the honest support matrix.

No provider and no browser: these are the rules that keep a slider from breaking a
turn - unknown keys dropped, values clamped, nothing sent while the panel is off.
"""

from __future__ import annotations

import pytest

from uh import llm_params


def test_the_panel_is_off_by_default() -> None:
    blank = llm_params.blank()

    assert blank["enabled"] is False
    assert set(blank["values"]) == set(llm_params.SPEC)


def test_nothing_is_sent_while_the_panel_is_off() -> None:
    """An untouched slider must not change an answer that used to come from the
    provider's own defaults."""
    assert llm_params.sanitise({"enabled": False, "values": {"temperature": 0.1}}) == {}
    assert llm_params.sanitise(None) == {}
    assert llm_params.sanitise({}) == {}


def test_values_are_clamped_into_their_range() -> None:
    clean = llm_params.sanitise(
        {
            "enabled": True,
            "values": {
                "temperature": 9,
                "top_p": -3,
                "max_tokens": 3,
                "frequency_penalty": 5,
                "presence_penalty": -9,
            },
        }
    )

    assert clean["temperature"] == 2.0
    assert clean["top_p"] == 0.0
    assert clean["max_tokens"] == 16
    assert clean["frequency_penalty"] == 2.0
    assert clean["presence_penalty"] == -2.0


def test_unknown_keys_are_dropped_not_forwarded() -> None:
    """A provider that receives a field it does not know answers 400."""
    clean = llm_params.sanitise(
        {"enabled": True, "values": {"temperature": 0.5, "top_k": 40, "seed": 7}}
    )

    assert "top_k" not in clean and "seed" not in clean
    assert clean["temperature"] == 0.5


def test_a_missing_control_takes_its_default() -> None:
    clean = llm_params.sanitise({"enabled": True, "values": {}})

    assert clean["temperature"] == llm_params.SPEC["temperature"]["default"]
    assert clean["max_tokens"] == llm_params.SPEC["max_tokens"]["default"]


def test_an_empty_reasoning_effort_is_not_sent() -> None:
    clean = llm_params.sanitise({"enabled": True, "values": {"reasoning_effort": ""}})

    assert "reasoning_effort" not in clean


def test_a_bogus_choice_falls_back_to_the_default() -> None:
    clean = llm_params.sanitise({"enabled": True, "values": {"reasoning_effort": "maximum"}})

    assert "reasoning_effort" not in clean  # the default is "" -> not sent


def test_the_web_search_switch_is_not_a_provider_parameter() -> None:
    clean = llm_params.sanitise({"enabled": True, "values": {"web_search": False}})
    assert "web_search" not in clean

    assert llm_params.web_search_enabled({"enabled": True, "values": {"web_search": False}}) is False
    assert llm_params.web_search_enabled({"enabled": True, "values": {"web_search": True}}) is True
    assert llm_params.web_search_enabled(None) is True
    assert llm_params.web_search_enabled({"enabled": False, "values": {"web_search": False}}) is True


def test_ollama_applies_every_parameter_but_reasoning() -> None:
    described = llm_params.describe("ollama", "qwen3:8b")

    assert "temperature" in described["applied"]
    assert "max_tokens" in described["applied"]
    assert described["supports_reasoning"] is False
    assert [entry["name"] for entry in described["ignored"]] == ["reasoning_effort"]
    assert "Ollama" in described["ignored"][0]["reason"]


def test_an_openai_compatible_model_reports_reasoning_honestly() -> None:
    plain = llm_params.describe("deepseek", "deepseek-flash")
    reasoning = llm_params.describe("deepseek", "o3-mini")

    assert "reasoning_effort" not in plain["applied"]
    assert [entry["name"] for entry in plain["ignored"]] == ["reasoning_effort"]
    assert "reasoning_effort" in reasoning["applied"]
    assert reasoning["ignored"] == []


@pytest.mark.parametrize("model", ["o3-mini", "o1", "gpt-5", "GPT-5-mini"])
def test_the_reasoning_families_are_recognised(model: str) -> None:
    assert llm_params.REASONING_MODELS.match(model)


def test_public_payload_carries_the_spec_and_the_defaults() -> None:
    payload = llm_params.public()

    assert set(payload) == {"spec", "defaults", "provider_keys"}
    assert "temperature" in payload["spec"]
    assert payload["defaults"]["enabled"] is False
    assert "web_search" not in payload["provider_keys"]
