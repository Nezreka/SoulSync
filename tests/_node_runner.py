"""Finding and running node for the JS contract suites, in ONE place.

These wrappers exist because the real tests are .mjs files run by node's test
runner — cross-client determinism for chat protocol, the chess/battleship
engines, the games fold. They are the tests that matter most and the easiest to
lose, because a wrapper that cannot find node SKIPS: a green run that checked
nothing.

Two things went wrong, and both were invisible:

  shutil.which("node") returns None on WSL. The only node is the Windows one and
  it is on PATH as node.exe. Two wrappers had already learned this and eight had
  not, so most of the JS suites had been silently skipping.

  Once found, that node is a WINDOWS binary and cannot open /mnt/e/... — it needs
  E:\\... So the fix that makes the suite RUN is not the same as the fix that
  makes it PASS, and stopping at the first one turns silent skips into red.

One module so the next wrapper inherits both answers instead of half of one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

#: The node binary, or None. node.exe is the WSL case.
NODE = shutil.which("node") or shutil.which("node.exe")

#: True when NODE is a Windows executable reached from WSL, so paths handed to
#: it have to be translated out of /mnt/<drive>/ form.
NODE_IS_WINDOWS = bool(NODE and NODE.lower().endswith(".exe"))

MIN_MAJOR = 22


def node_available(min_major: int = MIN_MAJOR) -> bool:
    """Whether a node new enough to run these suites exists."""
    if not NODE:
        return False
    try:
        result = subprocess.run([NODE, "--version"], capture_output=True,
                                text=True, timeout=10)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    raw = (result.stdout or "").strip().lstrip("v")
    try:
        return int(raw.split(".")[0]) >= min_major
    except (ValueError, IndexError):
        return False


def node_path(path) -> str:
    """A path in the form THIS node understands.

    A Windows node reached from WSL cannot open /mnt/e/...; wslpath rewrites it
    to E:\\... If the translation is unavailable the original is returned, so a
    setup this does not anticipate fails loudly on a missing file rather than
    silently skipping.
    """
    p = str(path)
    if not NODE_IS_WINDOWS:
        return p
    try:
        out = subprocess.run(["wslpath", "-w", p], capture_output=True,
                             text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return p


def run_node_test(test_file, *, cwd=None, timeout: int = 180):
    """Run one .mjs suite under node's test runner."""
    return subprocess.run(
        [NODE, "--test", node_path(test_file)],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def assert_node_suite(test_file, label: str, *, cwd=None) -> None:
    """Run a suite and fail the pytest with node's own output attached."""
    if not Path(test_file).is_file():
        raise AssertionError(f"{label}: missing test file {test_file}")
    result = run_node_test(test_file, cwd=cwd)
    assert result.returncode == 0, (
        f"{label} JS tests failed:\n" + (result.stdout or "") + (result.stderr or ""))
