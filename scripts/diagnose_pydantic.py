#!/usr/bin/env python
"""Read-only Pydantic diagnostic for the interpreter that actually runs UH.

Answers three questions with evidence instead of opinion:

1. who, in THIS environment, constrains pydantic - and whether those constraints
   are *active* (marker evaluated) or belong to an extra nobody installed;
2. what interval of pydantic versions satisfies every active constraint at once,
   which is the only useful answer to "is there a version that works?";
3. what a clean install resolves to (``--clean``), so a conflict can be told
   apart from a stale environment.

Nothing is installed, uninstalled or pinned: no pip install in the current
environment, no file written. Exit code 0 = coherent, 1 = conflicts found,
which makes it usable as a gate.

    python diagnose_pydantic.py                # the current interpreter
    python diagnose_pydantic.py --clean        # + two throwaway venvs
    python diagnose_pydantic.py --json         # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import venv
from importlib.metadata import distributions
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # packaging ships with pip, so it is there in practice
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except Exception:  # pragma: no cover - degraded mode, still useful
    Requirement = None  # type: ignore[assignment]
    SpecifierSet = None  # type: ignore[assignment]
    Version = None  # type: ignore[assignment]

SUBJECT = "pydantic"
#: Packages whose presence on the interpreter is worth naming in the report:
#: the ones a shared AI stack installs, and the ones that pull the subject.
INTERESTING = (
    "uh-harness", "uhb", "fastapi", "starlette", "uvicorn", "pydantic",
    "pydantic-core", "pydantic-settings", "gradio", "litellm", "openai",
    "mcp", "mcp-types", "google-genai", "google-antigravity", "mixpanel",
    "posthog", "transformers", "albumentations",
)


def installed() -> Dict[str, str]:
    found: Dict[str, str] = {}
    for dist in distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if name:
            found[name.lower()] = dist.version
    return found


def dist(name: str) -> Optional[Any]:
    for candidate in distributions():
        if (candidate.metadata.get("Name") or "").lower() == name.lower():
            return candidate
    return None


def constraints(found: Dict[str, str]) -> List[Dict[str, Any]]:
    """Every requirement on the subject, with its marker evaluated here.

    A constraint behind an extra (``extra == "mcp"``) is NOT active unless that
    extra was installed - pip does not record extras, so the honest answer is
    "reported, not enforced", which is exactly the trap that makes people blame
    a package that is not constraining anything.
    """
    rows: List[Dict[str, Any]] = []
    for name, package_version in sorted(found.items()):
        candidate = dist(name)
        if candidate is None:
            continue
        for raw in candidate.requires or []:
            if not re.match(rf"\s*{SUBJECT}\b", raw, re.I):
                continue
            active, reason = True, ""
            if Requirement is not None:
                try:
                    parsed = Requirement(raw)
                except Exception:
                    continue
                # `pydantic-core`, `pydantic-settings`, `pydantic-extra-types` are
                # different packages: comparing the parsed NAME is the only safe
                # filter, a regex on the prefix catches them all.
                if parsed.name.lower() != SUBJECT:
                    continue
                marker = str(parsed.marker or "")
                if "extra" in marker:
                    active = False
                    reason = f"extra non installé ({marker})"
                elif marker and not parsed.marker.evaluate():
                    active = False
                    reason = f"marqueur faux ici ({marker})"
            rows.append(
                {
                    "package": name,
                    "version": package_version,
                    "requirement": raw.strip(),
                    "active": active,
                    "reason": reason,
                }
            )
    return rows


def feasible_interval(rows: List[Dict[str, Any]], current: str, universe: List[str]) -> Dict[str, Any]:
    """Versions of the subject that satisfy every ACTIVE constraint at once."""
    if SpecifierSet is None or Version is None:
        return {"error": "packaging absent: interval not computable"}
    combined = SpecifierSet()
    for row in rows:
        if not row["active"]:
            continue
        try:
            combined &= Requirement(row["requirement"]).specifier
        except Exception:
            continue
    allowed = [v for v in universe if combined.contains(v, prereleases=False)]
    return {
        "combined": str(combined) or "(aucune borne)",
        "current_satisfies": combined.contains(current, prereleases=False) if current else None,
        "allowed_versions": allowed[:12],
        "allowed_count": len(allowed),
        "nearest_above_current": allowed[0] if allowed else "",
    }


def pip_check() -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=180
    )
    lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
    return {"code": proc.returncode, "lines": lines, "clean": proc.returncode == 0}


def pypi_versions(package: str = SUBJECT) -> List[str]:
    """Published stable versions, newest last. Empty when offline."""
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{package}/json", timeout=15
        ) as response:
            payload = json.load(response)
    except Exception:
        return []
    if Version is None:
        return sorted(payload.get("releases") or {})
    stable = []
    for raw in payload.get("releases") or {}:
        try:
            parsed = Version(raw)
        except Exception:
            continue
        if not parsed.is_prerelease:
            stable.append(str(parsed))
    return sorted(stable, key=Version)


def clean_venvs() -> List[Dict[str, Any]]:
    """Two throwaway venvs: the core, then [web]. Installs nothing here."""
    results: List[Dict[str, Any]] = []
    for label, spec in (("core", "uh-harness"), ("web", "uh-harness[web]")):
        target = Path(tempfile.mkdtemp(prefix=f"uh-diag-{label}-"))
        venv.create(target, with_pip=True)
        python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "-q", spec],
            capture_output=True,
            text=True,
            timeout=900,
        )
        check = subprocess.run(
            [str(python), "-m", "pip", "check"], capture_output=True, text=True, timeout=180
        )
        freeze = subprocess.run(
            [str(python), "-m", "pip", "freeze"], capture_output=True, text=True, timeout=180
        )
        picked = {}
        for line in freeze.stdout.splitlines():
            name, _, value = line.partition("==")
            if name.lower() in {s.lower() for s in INTERESTING}:
                picked[name.lower()] = value
        probe = subprocess.run(
            [
                str(python),
                "-c",
                "import sys\n"
                "print('python', sys.version.split()[0])\n"
                "try:\n"
                "    import pydantic; print('pydantic', pydantic.VERSION)\n"
                "except ModuleNotFoundError:\n"
                "    print('pydantic absent')\n"
                "try:\n"
                "    from uh.web.app import create_app; create_app(token='t'); print('uh.web.app OK')\n"
                "except ModuleNotFoundError as exc:\n"
                "    print('uh.web.app absent (extra non installe):', exc)\n"
                "except Exception as exc:\n"
                "    print('uh.web.app FAIL', type(exc).__name__, str(exc)[:120])",
            ],
            capture_output=True,
            text=True,
            timeout=240,
        )
        results.append(
            {
                "venv": label,
                "spec": spec,
                "install_ok": install.returncode == 0,
                "install_tail": [
                    line for line in (install.stdout + install.stderr).splitlines()
                    if re.search(r"error|conflict|incompatible", line, re.I)
                ][-4:],
                "pip_check_clean": check.returncode == 0,
                "pip_check": check.stdout.strip().splitlines()[:6],
                "versions": picked,
                "probe": probe.stdout.strip().splitlines(),
            }
        )
    return results


def report(as_json: bool, clean: bool) -> int:
    found = installed()
    current = found.get(SUBJECT, "")
    rows = constraints(found)
    universe = pypi_versions()
    interval = feasible_interval(rows, current, universe)
    check = pip_check()

    interesting = {name: found[name] for name in INTERESTING if name in found}
    payload: Dict[str, Any] = {
        "interpreter": sys.executable,
        "python": sys.version.split()[0],
        "pip": subprocess.run(
            [sys.executable, "-m", "pip", "--version"], capture_output=True, text=True
        ).stdout.split()[1:3],
        "subject": SUBJECT,
        "subject_installed": current or "(absent)",
        "interesting_installed": interesting,
        "constraints": rows,
        "active_constraints": [r for r in rows if r["active"]],
        "inactive_constraints": [r for r in rows if not r["active"]],
        "feasible": interval,
        "pip_check": check,
    }
    if clean:
        payload["clean"] = clean_venvs()

    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"interpréteur : {payload['interpreter']}  (Python {payload['python']}, pip {payload['pip']})")
        print(f"{SUBJECT:<14} : {payload['subject_installed']}")
        print("installés      : " + ", ".join(f"{k} {v}" for k, v in interesting.items()))
        print()
        print("Contraintes ACTIVES sur pydantic :")
        if not payload["active_constraints"]:
            print("  (aucune)")
        for row in payload["active_constraints"]:
            print(f"  - {row['package']:<22} {row['requirement']}")
        if payload["inactive_constraints"]:
            print("Contraintes INACTIVES (extra non installé, donc non appliquées) :")
            for row in payload["inactive_constraints"]:
                print(f"  - {row['package']:<22} {row['requirement']}   [{row['reason']}]")
        print()
        print(f"Intervalle satisfaisant TOUTES les contraintes actives : {interval.get('combined')}")
        print(f"  version installée satisfait : {interval.get('current_satisfies')}")
        print(f"  versions publiées compatibles : {interval.get('allowed_count')}  ex. {interval.get('allowed_versions')}")
        print()
        print(f"pip check : {'OK' if check['clean'] else 'CONFLITS'}")
        for line in check["lines"]:
            print(f"  {line}")
        if clean:
            print()
            for entry in payload["clean"]:
                print(f"venv neuf [{entry['venv']}] {entry['spec']} -> install {'OK' if entry['install_ok'] else 'ECHEC'}, "
                      f"pip check {'propre' if entry['pip_check_clean'] else 'conflits'}")
                for line in entry["probe"]:
                    print(f"    {line}")
                for line in entry["pip_check"]:
                    print(f"    ! {line}")

    return 0 if check["clean"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="créer deux venvs jetables et les tester")
    parser.add_argument("--json", action="store_true", help="sortie lisible par une machine")
    args = parser.parse_args()
    return report(args.json, args.clean)


if __name__ == "__main__":
    sys.exit(main())
