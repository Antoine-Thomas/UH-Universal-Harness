"""The tests must run the tree they live in, not a wheel that shadows it.

The trap this pins: `pytest.exe` puts its own Scripts folder on `sys.path` first, so
a non-editable `uh-harness` installed in the venv wins over `./uh`. The push gate then
tests the *installed* copy - it can stay green while the commit is broken, and it can
go red while the commit is fine. An editable install (`pip install -e .`) is the fix;
this test is the alarm.
"""

from __future__ import annotations

from pathlib import Path

import uh

ROOT = Path(__file__).resolve().parents[1]


def test_the_imported_package_is_this_repository() -> None:
    imported = Path(uh.__file__).resolve()

    assert imported.is_relative_to(ROOT), (
        f"uh comes from {imported}, not from {ROOT}: run "
        "`.venv/Scripts/python -m pip install -e . --no-deps`"
    )
    assert (ROOT / "pyproject.toml").is_file()
