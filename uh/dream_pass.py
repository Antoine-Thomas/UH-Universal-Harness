"""One dream pass: read the sources, think once, write a report - never mutate.

P4.2. The pass is deliberately narrow, because it runs unattended:

* **it reads** SiYuan blocks and the RAG state through UHB (loopback, read-only).
  There is no ``POST /siyuan/blocks`` and no reindex during a pass: the sources
  are somebody's notes, and a night job that rewrites them without a human is a
  data-loss story;
* **it writes** exactly one directory under ``$UH_HOME/dreams/passes/``: a
  markdown report, a JSONL journal, nothing else;
* **it stops** on a token budget or a wall-clock budget, whichever comes first,
  and says where it stopped. A budget that can be talked out of is not a budget.

What it can and cannot see, said plainly rather than papered over: UHB caps a
block listing at 500 per call, and the RAG service exposes no chunk listing -
``GET /rag/status`` gives counts and dates only. So RAG is inventoried, not read,
unless ``$UH_RAG_CHUNKS`` points at a ``chunks.jsonl`` the operator allows.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import httpx

from .dream import DreamJournal, dreams_dir

#: What a pass may read. Order matters only for the report.
DEFAULT_SOURCES = ("siyuan", "rag")
#: UHB refuses to list more than this per call; asking for more is a lie in code.
BLOCK_CAP = 500
#: Enough to characterise a source without reading somebody's night of notes.
SAMPLE_BLOCKS = 50
SAMPLE_CHARS = 300


@dataclass
class DreamConfig:
    """Everything a pass may spend, and where it writes."""

    sources: Sequence[str] = DEFAULT_SOURCES
    budget_tokens: int = 50_000
    budget_wall_time_s: int = 1800
    output_dir: Optional[Path] = None
    mode: str = "chat"
    sample_blocks: int = SAMPLE_BLOCKS
    write: bool = True

    def directory(self) -> Path:
        return Path(self.output_dir) if self.output_dir is not None else dreams_dir() / "passes"


@dataclass
class DreamReport:
    """What happened, in a shape the CLI can print and the file can keep."""

    started: str
    finished: str = ""
    status: str = "ok"  # ok | partial | skipped | dry-run
    reason: str = ""
    sources: Dict[str, dict] = field(default_factory=dict)
    classification: Dict[str, Any] = field(default_factory=dict)
    findings: Dict[str, list] = field(default_factory=dict)
    ideas: List[dict] = field(default_factory=list)
    tokens: Dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0})
    wall_time_s: float = 0.0
    notes: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"# Dream pass - {self.started}", ""]
        lines.append(f"- status: **{self.status}**")
        if self.reason:
            lines.append(f"- reason: {self.reason}")
        lines.append(f"- wall time: {self.wall_time_s:.1f}s")
        lines.append(
            f"- tokens: {self.tokens.get('total', 0)} "
            f"(in {self.tokens.get('prompt', 0)} / out {self.tokens.get('completion', 0)})"
        )
        lines.append("")

        lines.append("## Sources")
        for name, state in self.sources.items():
            available = state.get("available")
            detail = state.get("detail") or state.get("error") or ""
            lines.append(f"- **{name}**: {'read' if available else 'not read'}" + (f" - {detail}" if detail else ""))
        lines.append("")

        if self.classification:
            plan = self.classification
            lines.append("## Classification (UHB /route, no token spent)")
            lines.append(
                f"- complexity: {plan.get('complexity') or '?'} | mode: {plan.get('mode') or '?'}"
            )
            if plan.get("reason"):
                lines.append(f"- reason: {plan['reason']}")
            chain = plan.get("chain") or []
            if chain:
                lines.append("- chain: " + " > ".join(str(item.get("ref")) for item in chain))
            lines.append("")

        if self.findings:
            lines.append("## Findings")
            for kind, items in self.findings.items():
                lines.append(f"### {kind}")
                if not items:
                    lines.append("- none reported")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

        if self.ideas:
            lines.append("## Ideas")
            for index, idea in enumerate(self.ideas, start=1):
                title = idea.get("title") or idea.get("titre") or f"idea {index}"
                why = idea.get("why") or idea.get("description") or ""
                source = idea.get("source") or "?"
                lines.append(f"{index}. **{title}** - {why} _({source})_")
            lines.append("")

        if self.notes:
            lines.append("## Notes")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")

        if self.status == "partial":
            lines.append("## Partial pass")
            lines.append(self.reason or "the pass stopped before finishing")
            lines.append("")
        return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")


class DreamScheduler:
    """Runs one pass. The scheduler that calls it lives in the CLI, not here."""

    def __init__(
        self,
        config: Optional[DreamConfig] = None,
        provider: Any = None,
        uhb_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        clock=time.monotonic,
    ) -> None:
        self.config = config or DreamConfig()
        self.provider = provider
        self._uhb_url = uhb_url
        self._client = client
        self._clock = clock
        self._started_at = 0.0
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        self._last_usage = {"prompt": 0, "completion": 0, "total": 0}

    # -- helpers ----------------------------------------------------------- #
    def _url(self) -> Optional[str]:
        if self._uhb_url is not None:
            return self._uhb_url.rstrip("/") or None
        from .toolsets.uhb import resolve_url

        return resolve_url()

    @contextmanager
    def _http(self) -> Iterator[httpx.Client]:
        """A client for one call. An injected one is borrowed, never closed."""
        if self._client is not None:
            yield self._client
            return
        with httpx.Client(timeout=10.0) as client:
            yield client

    def _usage(self) -> dict:
        snapshot = getattr(self.provider, "usage_snapshot", None)
        if not callable(snapshot):
            return {"prompt": 0, "completion": 0, "total": 0}
        try:
            raw = snapshot()
        except Exception:  # noqa: BLE001 - a counter that fails is a zero
            return {"prompt": 0, "completion": 0, "total": 0}
        return {key: int((raw or {}).get(key) or 0) for key in ("prompt", "completion", "total")}

    def _elapsed(self) -> float:
        return float(self._clock() - self._started_at)

    def _exhausted(self) -> Optional[str]:
        """Which budget ran out, if any. Checked before and after every call."""
        if self._tokens["total"] > self.config.budget_tokens:
            return f"token budget exhausted ({self._tokens['total']} > {self.config.budget_tokens})"
        if self._elapsed() > self.config.budget_wall_time_s:
            return f"wall-clock budget exhausted ({self._elapsed():.0f}s > {self.config.budget_wall_time_s}s)"
        return None

    def _ask(self, system: str, user: str, report: DreamReport) -> Optional[str]:
        """One model call, counted against both budgets."""
        if self.provider is None:
            report.notes.append("no provider configured: the analysis step was skipped")
            return None
        before = self._usage()
        try:
            answer = self.provider.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}], None
            )
        except Exception as exc:  # noqa: BLE001 - a failed call is a note, not a crash
            report.notes.append(f"model call failed: {type(exc).__name__}: {exc}")
            return None
        after = self._usage()
        self._last_usage = after
        for key in self._tokens:
            self._tokens[key] += max(0, after[key] - before[key])
        if isinstance(answer, dict):
            return str(answer.get("content") or "")
        return str(answer or "")

    # -- the pass ---------------------------------------------------------- #
    def run_once(self, dry_run: bool = False) -> DreamReport:
        self._started_at = self._clock()
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        report = DreamReport(started=_now_iso())
        url = self._url()
        if url is None:
            report.status = "skipped"
            report.reason = "UHB offline: the sources are read through UHB, so the pass has nothing to read"
            return self._finish(report)

        inventory = self._inventory(url, report)
        if dry_run:
            report.status = "dry-run"
            report.reason = "inventory only: no model was called, nothing was written"
            return self._finish(report, write=False)

        # One model call for analysis, and only if the prompt is worth it: UHB's
        # /route costs nothing and tells us whether to spend anything at all.
        report.classification = self._classify(url, inventory)
        complexity = str(report.classification.get("complexity") or "").lower()
        if complexity in ("standard", "complex"):
            self._review(inventory, report)
        elif complexity:
            report.notes.append(f"analysed by UHB as '{complexity}': no model call, nothing to review")

        self._write(report)
        return self._finish(report)

    def _inventory(self, url: str, report: DreamReport) -> Dict[str, dict]:
        inventory: Dict[str, dict] = {}
        for source in self.config.sources:
            if source == "siyuan":
                inventory[source] = self._inventory_siyuan(url, report)
            elif source == "rag":
                inventory[source] = self._inventory_rag(url, report)
            else:
                report.notes.append(f"unknown source '{source}' ignored")
                inventory[source] = {"available": False, "error": "unknown source"}
            report.sources[source] = {
                "available": bool(inventory[source].get("available")),
                "detail": inventory[source].get("detail", ""),
            }
        return inventory

    def _inventory_siyuan(self, url: str, report: DreamReport) -> dict:
        try:
            with self._http() as http:
                response = http.get(f"{url}/siyuan/blocks", params={"path": "/", "limit": BLOCK_CAP})
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        if response.status_code != 200:
            body = response.text[:200]
            return {"available": False, "error": f"HTTP {response.status_code}: {body}"}
        payload = response.json()
        blocks = payload.get("blocks") or []
        detail = f"{len(blocks)} block(s), capped at {BLOCK_CAP} per call by UHB"
        if len(blocks) >= BLOCK_CAP:
            detail += " - the cap was reached, so this is a sample of the notebook, not a total"
        return {"available": True, "blocks": blocks, "detail": detail}

    def _inventory_rag(self, url: str, report: DreamReport) -> dict:
        try:
            with self._http() as http:
                response = http.get(f"{url}/rag/status")
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        if response.status_code != 200:
            return {"available": False, "error": f"HTTP {response.status_code}"}
        payload = response.json()
        chunks = payload.get("chunks")
        detail = f"{chunks} chunk(s), source {payload.get('source') or '?'}"
        # A count is not content: say so, and offer the one honest way in.
        sample = self._rag_sample()
        if sample:
            detail += f"; {len(sample)} chunk(s) sampled from the chunks file"
        else:
            detail += "; no chunk listing exposed, so the RAG is inventoried, not read"
        return {"available": True, "status": payload, "chunks": sample, "detail": detail}

    def _rag_sample(self) -> List[dict]:
        """Chunks from ``$UH_RAG_CHUNKS`` if the operator points at the file."""
        import os

        raw = (os.environ.get("UH_RAG_CHUNKS") or "").strip()
        if not raw:
            return []
        path = Path(raw)
        if not path.is_file():
            return []
        found: List[dict] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if len(found) >= 5:
                        break
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        found.append(item)
        except OSError:
            return []
        return found

    def _classify(self, url: str, inventory: Dict[str, dict]) -> Dict[str, Any]:
        # The classifier reads what the model would read, not a receipt: a prompt
        # made only of counts comes back "trivial" and skips the pass entirely.
        sample = self._sample_text(inventory)[:1200]
        prompt = (
            "Pass de nuit: reviser la memoire, reperer doublons et incoherences, proposer des idees.\n"
            + ", ".join(f"{name}: {state.get('detail', '')}" for name, state in inventory.items())
            + ("\n\n" + sample if sample else "")
        )
        try:
            with self._http() as http:
                response = http.post(f"{url}/route", json={"prompt": prompt, "mode": self.config.mode})
        except Exception:  # noqa: BLE001 - no plan is not a failure of the pass
            return {}
        if response.status_code != 200:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _review(self, inventory: Dict[str, dict], report: DreamReport) -> None:
        sample = self._sample_text(inventory)
        if not sample:
            report.notes.append("nothing to review: the samples were empty")
            return
        exhausted = self._exhausted()
        if exhausted:
            report.status = "partial"
            report.reason = exhausted
            return
        system = (
            "You review a personal knowledge base. Answer with JSON only, no prose: "
            '{"duplicates": [...], "orphans": [...], "inconsistencies": [...], '
            '"ideas": [{"title": "...", "why": "...", "source": "siyuan|rag"}]}. '
            "Be concrete and short. If a list is empty, use []."
        )
        answer = self._ask(system, sample, report)
        exhausted = self._exhausted()
        if exhausted:
            report.status = "partial"
            report.reason = exhausted
            return
        if not answer:
            report.notes.append("the model returned nothing usable")
            return
        parsed = _parse_json(answer)
        if parsed is None:
            report.notes.append("the model answered something that is not JSON; kept as text")
            report.findings["raw"] = [answer[:2000]]
            return
        for key in ("duplicates", "orphans", "inconsistencies"):
            items = parsed.get(key)
            if isinstance(items, list):
                report.findings[key] = [str(item)[:300] for item in items]
        ideas = parsed.get("ideas")
        if isinstance(ideas, list):
            report.ideas = [
                {
                    "title": str(idea.get("title") or "")[:120],
                    "why": str(idea.get("why") or "")[:300],
                    "source": str(idea.get("source") or "?")[:20],
                }
                for idea in ideas
                if isinstance(idea, dict)
            ][:10]

    def _sample_text(self, inventory: Dict[str, dict]) -> str:
        parts: List[str] = []
        blocks = (inventory.get("siyuan") or {}).get("blocks") or []
        for block in blocks[: self.config.sample_blocks]:
            content = str(block.get("content") or "").strip()
            if not content:
                continue
            parts.append(f"[siyuan {block.get('type')}] {content[:SAMPLE_CHARS]}")
        for chunk in (inventory.get("rag") or {}).get("chunks") or []:
            text = str(chunk.get("texte") or chunk.get("text") or "").strip()
            if text:
                parts.append(f"[rag {chunk.get('source')}] {text[:SAMPLE_CHARS]}")
        return "\n".join(parts)

    def _write(self, report: DreamReport) -> None:
        if not self.config.write:
            return
        folder = self.config.directory() / _stamp()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            report_text = report.render()
            (folder / "report.md").write_text(report_text, encoding="utf-8")
            journal = DreamJournal(folder)
            journal.append(
                {
                    "type": "create",
                    "path": str(folder / "report.md"),
                    "bytes_delta": len(report_text.encode("utf-8")),
                    "reason": f"dream pass ({report.status})",
                }
            )
            for idea in report.ideas:
                journal.append(
                    {
                        "type": "create",
                        "path": str(folder / "report.md"),
                        "reason": f"idea: {idea.get('title', '')}"[:200],
                    }
                )
            report.artifacts = [str(folder / "report.md")] + [str(path) for path in folder.glob("journal-*.jsonl")]
        except OSError as exc:
            report.notes.append(f"cannot write the pass folder: {exc}")

    def _finish(self, report: DreamReport, write: Optional[bool] = None) -> DreamReport:
        report.finished = _now_iso()
        report.tokens = dict(self._tokens)
        report.wall_time_s = self._elapsed()
        if write is False:
            report.artifacts = []
        return report


def _parse_json(text: str) -> Optional[dict]:
    """The first JSON object in an answer, tolerating a fenced block."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(stripped[start : end + 1])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "BLOCK_CAP",
    "DEFAULT_SOURCES",
    "DreamConfig",
    "DreamReport",
    "DreamScheduler",
    "SAMPLE_BLOCKS",
]
