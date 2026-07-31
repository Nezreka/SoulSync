"""Every name the vanilla scripts reference must actually be declared.

Written after a real breakage: the library-list cleanup deleted 14 functions by
computing each one's span as "from `function X(` to the start of the next
function". Four top-level declarations sat BETWEEN two of those functions and
were swallowed with them --

    _ARTIST_DETAIL_BACK_LABELS   _artistDetailLabelStack
    _artistDetailGoingBack       artistDetailPageState

-- which left 177 references to `artistDetailPageState` in library.js pointing
at nothing. Artist detail threw ReferenceError on open, and it shipped: the
file still PARSED, so `node --check` passed; the Python suites only read these
files as text; and the vitest suite never loads them at all. Nothing in the
repo could see it.

The check: lint each static script for `no-undef` with every OTHER script's
top-level declarations supplied as globals (they share one scope through
<script> tags, so oxlint linting them in isolation is meaningless on its own).
The file's own declarations are deliberately EXCLUDED -- including them is what
would let a file mask the deletion of its own state.

Baselines are per file and pinned. They are pre-existing findings, not a
target: several are genuine latent bugs worth fixing on their own. The test
guards the DELTA -- it fails when a change adds a new unresolved name.

Widened 2026-07-30 to cover EVERY static script, after it missed a second
breakage of exactly the kind it was written for. The Downloads-page port
deleted the only definition of `_updateDlNavBadge`, which core.js:664 and
shared-helpers.js:4040 both still call from the websocket status handler --
a ReferenceError on every push, aborting the rest of both handlers. The test
could not see it because it was parametrized over `_BASELINE`, and that dict
held one entry: library.js. The other 42 scripts were never linted at all.

A guard that covers one file is a guard against one incident. Files with no
recorded baseline default to zero, so a newly-orphaned reference in any script
now fails.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_WEBUI = _ROOT / "webui"
_STATIC = _WEBUI / "static"

# Pre-existing unresolved names per file, re-measured across every script on
# 2026-07-30. Lowering a number is always fine; raising one means a change
# introduced a new ReferenceError waiting to happen, and needs justifying
# rather than re-baselining. Any script NOT listed here must be at zero.
_BASELINE = {
    "api-monitor.js": 7,
    "core.js": 8,
    "discover.js": 14,
    "downloads.js": 2,
    "library.js": 18,
    "setup-wizard.js": 1,
    "stats-automations.js": 4,
    "wishlist-tools.js": 1,
}

_DECL = re.compile(r"^(?:async )?function ([A-Za-z_$][\w$]*)", re.M)
_VAR = re.compile(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.M)
_DECL_INDENTED = re.compile(r"^\s*(?:async )?function ([A-Za-z_$][\w$]*)", re.M)
_VAR_INDENTED = re.compile(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.M)


def _npx() -> str | None:
    for name in ("npx", "npx.cmd"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in ("/mnt/c/Program Files/nodejs/npx.cmd", "/usr/bin/npx"):
        if Path(candidate).exists():
            return candidate
    return None


def _globals_excluding(target: Path) -> set[str]:
    """Top-level declarations from everything EXCEPT the file being linted."""
    names: set[str] = set()
    for path in sorted(_STATIC.glob("*.js")):
        if path.name == target.name:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        names |= set(_DECL.findall(source)) | set(_VAR.findall(source))

    index = (_WEBUI / "index.html").read_text(encoding="utf-8", errors="replace")
    names |= set(_DECL_INDENTED.findall(index)) | set(_VAR_INDENTED.findall(index))
    return names


def _all_scripts() -> list[str]:
    """Every static script, not just the ones with a recorded baseline."""
    return sorted(p.name for p in _STATIC.glob("*.js"))


@pytest.mark.parametrize("filename", _all_scripts())
def test_no_new_unresolved_globals(filename, tmp_path):
    npx = _npx()
    if npx is None:
        pytest.skip("node/npx unavailable")

    target = _STATIC / filename
    config = {
        "env": {"browser": True, "es2024": True},
        "globals": {name: "readonly" for name in sorted(_globals_excluding(target))},
        "rules": {"no-undef": "error"},
    }
    # oxlint resolves -c relative to its own cwd and cannot read a WSL /tmp path
    # from a Windows binary, so the config lands inside the project.
    config_path = _WEBUI / ".oxlintrc.globalcheck.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    try:
        proc = subprocess.run(
            [npx, "oxlint", "-A", "all", "-D", "no-undef",
             "-c", config_path.name, f"static/{filename}"],
            cwd=_WEBUI, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("oxlint timed out")
    finally:
        config_path.unlink(missing_ok=True)

    output = proc.stdout + proc.stderr
    # oxlint singularises: "Found 0 warnings and 1 error". Requiring the plural
    # made this silently unparseable for any file sitting at exactly one.
    match = re.search(r"Found \d+ warnings? and (\d+) errors?", output)
    assert match, f"could not parse oxlint output:\n{output[-2000:]}"

    count = int(match.group(1))
    names = sorted(set(re.findall(r"'([A-Za-z_$][\w$]*)' is not defined", output)))
    baseline = _BASELINE.get(filename, 0)
    assert count <= baseline, (
        f"{filename}: {count} unresolved names, baseline {baseline}.\n"
        f"A declaration was probably deleted along with the code around it, or\n"
        f"a page port removed a global that a classic script still calls.\n"
        f"Unresolved: {names}"
    )
