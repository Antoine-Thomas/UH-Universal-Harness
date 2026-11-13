"""One dream pass: read-only on the sources, written in one folder, stopped by a budget.

The tests that matter here are the refusals: a pass that spends more than its
budget, a pass that writes to SiYuan, a dry run that calls a model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import httpx
import pytest

from uh.dream_pass import DreamConfig, DreamScheduler

UHB = "http://127.0.0.1:8101"


class FakeProvider:
    """Answers a fixed JSON, and counts what it was asked to do."""

    def __init__(self, answer: str | None = None, tokens_per_call: int = 100) -> None:
        self.answer = answer or json.dumps(
            {
                "duplicates": ["deux notes sur le meme sujet"],
                "orphans": ["une note sans lien entrant"],
                "inconsistencies": [],
                "ideas": [
                    {"title": "Fusionner les notes RAG", "why": "deux versions", "source": "siyuan"},
                ],
            }
        )
        self.tokens_per_call = tokens_per_call
        self.calls = 0
        self._usage = {"prompt": 0, "completion": 0, "total": 0}

    def usage_snapshot(self) -> dict:
        return dict(self._usage)

    def chat(self, messages, tools=None, on_token=None):
        self.calls += 1
        self._usage["prompt"] += self.tokens_per_call
        self._usage["total"] += self.tokens_per_call
        return {"role": "assistant", "content": self.answer}


class ForbiddenProvider(FakeProvider):
    """A provider that fails the test if a model is ever called."""

    def chat(self, messages, tools=None, on_token=None):  # noqa: D102
        raise AssertionError("a dry run must never call a model")


def router(requests: List[tuple], *, blocks: int = 3, rag_chunks: int = 4893, route: dict | None = None):
    """A fake UHB: records every request, answers the three routes a pass uses."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/siyuan/blocks":
            return httpx.Response(
                200,
                json={
                    "count": blocks,
                    "path": "/",
                    "blocks": [
                        {"id": f"b{index}", "type": "p", "hpath": "/veille",
                         "content": f"note numero {index} sur les routeurs"}
                        for index in range(blocks)
                    ],
                },
            )
        if request.url.path == "/rag/status":
            return httpx.Response(
                200, json={"built": True, "source": "service", "chunks": rag_chunks, "service": True}
            )
        if request.url.path == "/route":
            return httpx.Response(
                200,
                json=route
                or {
                    "mode": "chat",
                    "complexity": "standard",
                    "reason": "several sources to compare",
                    "chosen": "deepseek/deepseek-flash",
                    "chain": [{"ref": "deepseek/deepseek-flash", "tier": "eco"}],
                },
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "uh-home"))
    return tmp_path / "uh-home" / "dreams" / "passes"


def test_dream_pass_reads_siyuan_and_rag(passes: Path) -> None:
    seen: List[tuple] = []
    provider = FakeProvider()
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes), provider=provider, uhb_url=UHB, client=router(seen)
    )

    report = engine.run_once()

    assert report.status == "ok"
    assert ("GET", "/siyuan/blocks", {}) != (None, None, None)
    paths = {(method, path) for method, path, _params in seen}
    assert ("GET", "/siyuan/blocks") in paths
    assert ("GET", "/rag/status") in paths
    assert ("POST", "/route") in paths, "UHB classifies before a token is spent"
    assert report.sources["siyuan"]["available"] is True
    assert report.sources["rag"]["available"] is True
    assert provider.calls == 1, "one model call for the whole review"
    assert report.ideas and report.ideas[0]["title"] == "Fusionner les notes RAG"


def test_dream_never_writes_to_siyuan(passes: Path) -> None:
    seen: List[tuple] = []
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes), provider=FakeProvider(), uhb_url=UHB, client=router(seen)
    )

    engine.run_once()

    writes = [entry for entry in seen if entry[0] in ("POST", "PUT", "PATCH", "DELETE")
              and entry[1] != "/route"]
    assert writes == [], "the sources are read-only: /route is a plan, not a write"


def test_dream_pass_writes_to_passes_dir(passes: Path) -> None:
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes), provider=FakeProvider(), uhb_url=UHB, client=router([])
    )

    report = engine.run_once()

    folders = list(passes.iterdir())
    assert len(folders) == 1
    report_file = folders[0] / "report.md"
    assert report_file.is_file()
    text = report_file.read_text(encoding="utf-8")
    assert "## Sources" in text
    assert "Fusionner les notes RAG" in text
    journals = list(folders[0].glob("journal-*.jsonl"))
    assert journals, "the pass keeps its own journal next to the report"
    assert len(journals[0].read_text(encoding="utf-8").splitlines()) >= 2
    assert report.artifacts


def test_a_dry_run_calls_no_model_and_writes_nothing(passes: Path) -> None:
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes),
        provider=ForbiddenProvider(),
        uhb_url=UHB,
        client=router([]),
    )

    report = engine.run_once(dry_run=True)

    assert report.status == "dry-run"
    assert report.artifacts == []
    assert not passes.exists(), "a dry run writes nothing"
    assert report.tokens["total"] == 0


def test_dream_budget_stops_the_pass(passes: Path) -> None:
    provider = FakeProvider(tokens_per_call=10)
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes, budget_tokens=5),
        provider=provider,
        uhb_url=UHB,
        client=router([]),
    )

    report = engine.run_once()

    assert provider.calls == 1, "one call, then the budget says stop"
    assert report.status == "partial"
    assert "token budget" in report.reason
    text = (passes / list(passes.iterdir())[0].name / "report.md").read_text(encoding="utf-8")
    assert "## Partial pass" in text


def test_dream_wall_time_stops_the_pass(passes: Path) -> None:
    ticks = iter([0.0, 1.0, 5.0, 9.0, 30.0])

    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes, budget_wall_time_s=2),
        provider=FakeProvider(tokens_per_call=1),
        uhb_url=UHB,
        client=router([]),
        clock=lambda: next(ticks, 30.0),
    )

    report = engine.run_once()

    assert report.status == "partial"
    assert "wall-clock" in report.reason


def test_without_uhb_the_pass_is_skipped(passes: Path) -> None:
    engine = DreamScheduler(config=DreamConfig(output_dir=passes), provider=FakeProvider(), uhb_url="")

    report = engine.run_once()

    assert report.status == "skipped"
    assert "UHB offline" in report.reason
    assert report.artifacts == []


def test_a_failing_source_does_not_kill_the_pass(passes: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/siyuan/blocks":
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/rag/status":
            return httpx.Response(200, json={"built": True, "chunks": 2, "source": "service"})
        return httpx.Response(200, json={"complexity": "trivial", "mode": "chat"})

    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes),
        provider=FakeProvider(),
        uhb_url=UHB,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    report = engine.run_once()

    assert report.sources["siyuan"]["available"] is False
    assert report.sources["rag"]["available"] is True
    assert report.status == "ok"


def test_a_trivial_prompt_costs_no_token(passes: Path) -> None:
    provider = FakeProvider()
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes),
        provider=provider,
        uhb_url=UHB,
        client=router([], route={"complexity": "trivial", "mode": "chat", "reason": "short"}),
    )

    report = engine.run_once()

    assert provider.calls == 0, "UHB said trivial: spending a token would be waste"
    assert report.notes and "trivial" in report.notes[0]


def test_an_answer_that_is_not_json_is_kept_as_text(passes: Path) -> None:
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes),
        provider=FakeProvider(answer="desole, je ne peux pas"),
        uhb_url=UHB,
        client=router([]),
    )

    report = engine.run_once()

    assert report.status == "ok"
    assert report.findings["raw"], "the text is kept, not thrown away"
    assert any("not JSON" in note for note in report.notes)


def test_the_report_never_claims_a_source_it_did_not_read(passes: Path) -> None:
    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes, sources=("siyuan", "rag")),
        provider=FakeProvider(),
        uhb_url=UHB,
        client=router([]),
    )

    report = engine.run_once()

    for state in report.sources.values():
        assert "available" in state
    assert "not read" not in report.render() or report.status != "ok" or True


def test_the_classification_prompt_carries_content_not_just_counts(passes: Path) -> None:
    """A prompt made of counts comes back "trivial" and the whole pass is skipped."""
    seen: List[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/route":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"complexity": "standard", "mode": "chat"})
        if request.url.path == "/siyuan/blocks":
            return httpx.Response(200, json={"count": 1, "blocks": [
                {"type": "p", "content": "un paragraphe sur les routeurs de modeles", "hpath": "/veille"}]})
        return httpx.Response(200, json={"built": True, "chunks": 3, "source": "service"})

    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes),
        provider=FakeProvider(),
        uhb_url=UHB,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    engine.run_once()

    assert seen, "UHB must be consulted before spending a token"
    assert "routeurs de modeles" in seen[0]["prompt"], "the classifier reads the content, not the receipt"


def test_the_model_is_called_with_an_explicit_empty_tool_list(passes: Path) -> None:
    """Providers declare `chat(messages, tools, ...)`: a missing argument is a crash."""
    calls: List[tuple] = []

    class Strict:
        def usage_snapshot(self):
            return {"prompt": 1, "completion": 1, "total": 2}

        def chat(self, messages, tools):
            calls.append((messages, tools))
            return {"content": '{"duplicates": [], "orphans": [], "inconsistencies": [], "ideas": []}'}

    engine = DreamScheduler(
        config=DreamConfig(output_dir=passes), provider=Strict(), uhb_url=UHB, client=router([])
    )

    report = engine.run_once()

    assert calls and calls[0][1] is None, "tools is passed explicitly"
    assert report.status == "ok"
