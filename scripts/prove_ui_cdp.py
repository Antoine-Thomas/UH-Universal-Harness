"""Measure the cockpit in a real browser: polish, not impressions (P5-UI, 22/11).

What it proves, and why each check is a measurement rather than a reading of the
source:

* **every ``var(--x)`` resolves** - a custom property used without a definition or a
  fallback silently renders as nothing (``var(--danger)`` retombait sur du noir);
* **a modal closes** by Escape, by the backdrop, and by its own cancel button - and
  Escape does not also collapse the right column (a rendered width, not a class);
* **the tab bar wraps without clipping** at 1280 px and at 1024 px - measured on
  ``scrollWidth`` against ``clientWidth``, because a wrapped-but-truncated label
  looks fine in the DOM and unreadable on screen;
* **contrast** >= 4.5:1 for running text and >= 3:1 for control borders, computed
  from the colours Chromium actually painted, in both themes;
* **screenshots** before/after are written next to the report, never only claimed.

Usage (starts its own throwaway ``uh web``):

    python scripts/prove_ui_cdp.py --screenshots "$TMPDIR/uh-ui-proof"

Attach to a server already running instead:

    python scripts/prove_ui_cdp.py --url "http://127.0.0.1:8765/?token=..."
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "uh" / "web" / "static"


# --------------------------------------------------------------------------- #
# custom properties, checked on the sheet that is served
# --------------------------------------------------------------------------- #
def undefined_vars(css: str) -> list[str]:
    """``var(--x)`` names that are neither defined nor given a fallback anywhere.

    ``--sidebar-w``/``--right-w``/``--splitter`` are written at runtime by
    ``layoutPrefs``; they are legitimate only because every use carries a fallback,
    which is exactly what this check encodes.
    """
    defined = set(re.findall(r"^\s*(--[A-Za-z0-9_-]+)\s*:", css, re.M))
    missing: list[str] = []
    for match in re.finditer(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,?)", css):
        name, comma = match.group(1), match.group(2)
        if name in defined or comma == ",":
            continue
        if name not in missing:
            missing.append(name)
    return missing


# --------------------------------------------------------------------------- #
# colour maths
# --------------------------------------------------------------------------- #
def _rgb(text: str) -> tuple[float, float, float]:
    numbers = [float(value) for value in re.findall(r"[\d.]+", text or "")][:3]
    if len(numbers) < 3:
        raise ValueError(f"not a colour: {text!r}")
    return numbers[0], numbers[1], numbers[2]


def _luminance(rgb: tuple[float, float, float]) -> float:
    channels = []
    for value in rgb:
        channel = value / 255.0
        channels.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    first, second = _luminance(_rgb(foreground)), _luminance(_rgb(background))
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


# --------------------------------------------------------------------------- #
# the server under test
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    def __init__(self) -> None:
        self.port = _free_port()
        self.home = Path(tempfile.mkdtemp(prefix="uh-ui-proof-"))
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Server":
        env = {**os.environ}
        env.update(
            {
                "UH_HOME": str(self.home),
                # Nothing here needs a second brain, a model, or the operator's own
                # services: the page must paint without any of them.
                "UH_UHB_URL": "",
                "UH_OLLAMA_URL": "http://127.0.0.1:1",
                "UH_SCHEDULE_DISABLE_SYSTEM": "1",
                "UH_SCHEDULE_STARTUP_DIR": str(self.home / "startup"),
                "PYTHONPATH": str(ROOT),
            }
        )
        (self.home / "startup").mkdir(parents=True, exist_ok=True)
        # A pipe nobody drains fills up and freezes the child; the log is also what
        # makes a failed start diagnosable instead of a 30 s shrug.
        self.log = self.home / "uh-web.log"
        handle = self.log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(  # noqa: S603 - this checkout, this interpreter
            [
                sys.executable,
                "-m",
                "uh",
                "web",
                "--port",
                str(self.port),
                "--no-uhb",
                "--no-open",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        import httpx

        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/healthz", timeout=1.0).status_code == 200:
                    return self
            except Exception:  # noqa: BLE001 - not up yet is the normal case
                time.sleep(0.2)
        tail = self.log.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"uh web did not answer /healthz within 30 s:\n{tail}")

    def __exit__(self, *_exc) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - a stuck server
                self.proc.kill()

    @property
    def url(self) -> str:
        token = (self.home / "web-token").read_text(encoding="utf-8").strip()
        return f"http://127.0.0.1:{self.port}/?token={token}"


# --------------------------------------------------------------------------- #
# in-page measurements
# --------------------------------------------------------------------------- #
CONTRAST_JS = """
(selector) => {
  const node = document.querySelector(selector);
  if (!node) return null;
  const parse = (value) => {
    const match = (value || "").match(/rgba?\\(([^)]+)\\)/);
    if (!match) return null;
    const parts = match[1].split(",").map((item) => parseFloat(item));
    return { rgb: `rgb(${parts[0]}, ${parts[1]}, ${parts[2]})`, alpha: parts.length > 3 ? parts[3] : 1 };
  };
  const background = (element) => {
    let current = element;
    while (current) {
      const colour = parse(getComputedStyle(current).backgroundColor);
      if (colour && colour.alpha > 0.9) return colour.rgb;
      current = current.parentElement;
    }
    // The page paints a gradient (`--app-bg`), so backgroundColor is transparent
    // everywhere above the frame: the theme's own ink/panel pair is the honest
    // answer, and guessing white would manufacture a 1:1 contrast failure.
    const root = getComputedStyle(document.documentElement);
    const panel = parse(root.getPropertyValue("--panel"));
    if (panel) return panel.rgb;
    return "rgb(255, 255, 255)";
  };
  const style = getComputedStyle(node);
  return {
    fg: parse(style.color) ? parse(style.color).rgb : null,
    bg: background(node),
    border: parse(style.borderTopColor) ? parse(style.borderTopColor).rgb : null,
    borderBg: background(node.parentElement || node),
    fontSize: parseFloat(style.fontSize),
  };
}
"""

TABS_JS = """
() => {
  const tabs = [...document.querySelectorAll('.panel-tab')].filter(
    (node) => node.offsetParent !== null
  );
  return {
    count: tabs.length,
    clipped: tabs
      .filter((node) => node.scrollWidth > node.clientWidth + 1)
      .map((node) => node.textContent.trim()),
    rows: new Set(tabs.map((node) => Math.round(node.getBoundingClientRect().top))).size,
    navHeight: Math.round(document.querySelector('.panel-tabs').getBoundingClientRect().height),
  };
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="attach to a running `uh web` (with its token)")
    parser.add_argument(
        "--screenshots",
        default=str(Path(tempfile.gettempdir()) / "uh-ui-proof"),
        help="directory for the before/after captures",
    )
    parser.add_argument("--headed", action="store_true", help="watch the browser work")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    shots = Path(args.screenshots)
    shots.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    missing = undefined_vars((STATIC / "style.css").read_text(encoding="utf-8"))
    print(f"css variables undefined without fallback: {missing or 'none'}")
    if missing:
        failures.append(f"undefined css variables: {missing}")

    server = None
    url = args.url
    if not url:
        server = Server().__enter__()
        url = server.url
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            # "networkidle" never happens here: the page keeps a lease beat every 10 s
            # and a mode badge every 20 s, by design. Wait for the paint instead.
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("#messages", timeout=30000)
            # The first bubble is painted by boot (first-run hint or the last session).
            page.wait_for_selector("#messages .msg", timeout=30000)
            page.wait_for_timeout(500)

            for theme in ("dark", "light"):
                page.evaluate("(theme) => { document.documentElement.dataset.theme = theme; }", theme)
                # `.msg` transitions its background over 200 ms: measuring during the
                # fade reads the OLD panel colour against the NEW text colour, which
                # is a transition artefact, not a contrast defect.
                page.wait_for_timeout(400)

                sizes = CONTRAST_JS
                text = page.evaluate(sizes, ".msg")
                if text and text["fg"]:
                    ratio = contrast(text["fg"], text["bg"])
                    print(
                        f"[{theme}] text contrast {ratio:.2f}:1 (need 4.5) "
                        f"[{text['fg']} on {text['bg']}]"
                    )
                    if ratio < 4.5:
                        failures.append(f"[{theme}] text contrast {ratio:.2f} < 4.5")
                border = page.evaluate(sizes, "#input")
                if border and border["border"]:
                    ratio = contrast(border["border"], border["borderBg"])
                    print(f"[{theme}] control border contrast {ratio:.2f}:1 (need 3.0)")
                    if ratio < 3.0:
                        failures.append(f"[{theme}] border contrast {ratio:.2f} < 3.0")
                if text:
                    print(f"[{theme}] message font-size {text['fontSize']}px (need >= 14)")
                    if text["fontSize"] < 14:
                        failures.append(f"[{theme}] message text {text['fontSize']}px < 14px")

                for width in (1280, 1024):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.wait_for_timeout(250)
                    if width == 1024:
                        # At 1200 px and below the right column is an overlay and the
                        # product closes it on purpose. Measure what the user sees when
                        # they open it from the ⚙ handle, not a state boot never makes.
                        collapsed = page.evaluate(
                            "() => document.querySelector('#layout').classList.contains('right-collapsed')"
                        )
                        if collapsed:
                            page.click("#panel-toggle")
                            page.wait_for_timeout(400)
                    tabs = page.evaluate(TABS_JS)
                    print(
                        f"[{theme}] {width}px tabs: {tabs['count']} visible, "
                        f"{tabs['rows']} row(s), clipped={tabs['clipped'] or 'none'}"
                    )
                    if tabs["clipped"]:
                        failures.append(f"[{theme}] {width}px clipped tabs: {tabs['clipped']}")
                    if tabs["count"] and width == 1024 and tabs["rows"] < 2:
                        failures.append(
                            f"[{theme}] 1024px: tabs stayed on one row ({tabs['navHeight']}px)"
                        )
                    page.screenshot(path=str(shots / f"cockpit-{theme}-{width}.png"), full_page=False)

                page.set_viewport_size({"width": 1280, "height": 900})

                # The quit confirmation is the modal every destructive gesture uses.
                page.click("#quit-uh")
                page.wait_for_selector(".modal-backdrop .modal", timeout=10000)
                page.screenshot(path=str(shots / f"modal-{theme}.png"))
                width_before = page.evaluate(
                    "() => Math.round(document.querySelector('#layout').getBoundingClientRect().width)"
                )
                page.keyboard.press("Escape")
                page.wait_for_function(
                    "() => !document.querySelector('.modal-backdrop')", timeout=5000
                )
                width_after = page.evaluate(
                    "() => Math.round(document.querySelector('#layout').getBoundingClientRect().width)"
                )
                print(f"[{theme}] Escape closed the modal; layout {width_before} -> {width_after}px")
                if width_after != width_before:
                    failures.append(
                        f"[{theme}] Escape also resized the layout ({width_before} -> {width_after})"
                    )

                page.click("#quit-uh")
                page.wait_for_selector(".modal-backdrop .modal", timeout=10000)
                page.mouse.click(8, 8)
                page.wait_for_function(
                    "() => !document.querySelector('.modal-backdrop')", timeout=5000
                )
                print(f"[{theme}] backdrop click closed the modal")

                page.click("#quit-uh")
                page.wait_for_selector(".modal-backdrop .modal", timeout=10000)
                page.click(".modal-actions .data-button:not(.danger)")
                page.wait_for_function(
                    "() => !document.querySelector('.modal-backdrop')", timeout=5000
                )
                print(f"[{theme}] cancel button closed the modal")

            browser.close()
    finally:
        if server is not None:
            server.__exit__()

    print(f"screenshots: {shots}")
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
