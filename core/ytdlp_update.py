"""Update yt-dlp from inside SoulSync, and be honest about what that does.

YouTube changes how it serves video faster than any release schedule, so a yt-dlp
that is a few weeks old starts answering ``HTTP Error 403: Forbidden`` on videos
that worked yesterday. The live install proved the cost: 22 videos hit that error
across many channels, and one had already burned all three retry attempts and been
skipped permanently — for a cause a package update fixes.

Three things this module refuses to paper over:

**The update does not take effect until a restart.** ``yt_dlp`` is already imported;
replacing the files on disk does not replace the loaded module, and reloading a
package that size is not reliable. A button that quietly implies otherwise is worse
than no button — the user updates, retries, gets the same 403, and stops trusting
it. :func:`interpret_result` therefore always carries ``restart_required``.

**Nightly is the default, deliberately.** Stable lags precisely on the extractor
fixes this exists to deliver. Stable remains selectable for anyone who wants it.

**Failure is reported as what it was.** A read-only site-packages, a distro-managed
Python, a container, or the wrong user all fail differently and need different
fixes; collapsing them into "update failed" sends people hunting the wrong thing.

The core here is pure — the subprocess and the network are injected — because the
one thing worse than not updating is a settings button that hangs the server.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

STABLE = "stable"
NIGHTLY = "nightly"
DEFAULT_CHANNEL = NIGHTLY

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
_TIMEOUT = 300          # pip over a slow link; still bounded


def normalize_channel(channel: Any) -> str:
    """Anything unrecognised becomes the default rather than an error — a bad
    stored setting must not make the button unusable."""
    return STABLE if str(channel or "").strip().lower() == STABLE else NIGHTLY


def pip_command(channel: Any, python: str) -> List[str]:
    """The exact install command. ``[default]`` matches what yt-dlp documents, and
    ``--pre`` is the only thing that separates nightly from stable on PyPI."""
    cmd = [str(python), "-m", "pip", "install", "-U"]
    if normalize_channel(channel) == NIGHTLY:
        cmd.append("--pre")
    cmd.append("yt-dlp[default]")
    return cmd


def _version_key(v: Any) -> tuple:
    """yt-dlp versions are date-based ('2026.06.09', nightly '2026.8.11.232712'),
    so a plain string compare gets 2026.6.9 vs 2026.06.09 wrong."""
    out = []
    for part in str(v or "").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def parse_pypi(payload: Any, channel: Any = DEFAULT_CHANNEL) -> Optional[str]:
    """Newest version on the chosen channel, or None.

    Stable is whatever PyPI calls the current release. Nightly is the highest
    version present at all — yt-dlp publishes nightlies as pre-releases of the same
    package, which is why ``--pre`` installs them and why they never appear as
    ``info.version``."""
    try:
        data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
        if not isinstance(data, dict):
            return None
        stable = ((data.get("info") or {}).get("version")) or None
        if normalize_channel(channel) == STABLE:
            return stable
        everything = list((data.get("releases") or {}).keys())
        if not everything:
            return stable
        newest = max(everything, key=_version_key)
        # A nightly older than stable means no nightly has been cut since; say stable.
        if stable and _version_key(stable) >= _version_key(newest):
            return stable
        return newest
    except (ValueError, TypeError, AttributeError):
        return None


def installed_version(importer: Optional[Callable] = None) -> Optional[str]:
    """The version currently LOADED in this process — which is the one actually
    doing the downloading, and the only one whose staleness matters."""
    try:
        if importer is not None:
            mod = importer()
        else:
            import yt_dlp as mod   # noqa: PLC0415
        return str(getattr(getattr(mod, "version", None), "__version__", "") or "") or None
    except Exception:   # noqa: BLE001 - not installed / broken install
        return None


def is_behind(installed: Any, latest: Any) -> bool:
    if not installed or not latest:
        return False
    return _version_key(latest) > _version_key(installed)


def interpret_result(returncode: Any, stdout: Any = "", stderr: Any = "",
                     *, installed_before: Any = None) -> Dict[str, Any]:
    """Turn pip's exit into something a person can act on.

    Every failure below has a different fix, so each is named. The success case
    always states the restart requirement — the update is on disk, but the running
    process is still using the yt-dlp it imported at startup."""
    out = "%s\n%s" % (stdout or "", stderr or "")
    low = out.lower()
    try:
        code = int(returncode)
    except (TypeError, ValueError):
        code = 1

    if code == 0:
        if "already satisfied" in low and "installed" not in low.split("already satisfied")[-1]:
            return {"ok": True, "changed": False, "restart_required": False,
                    "message": "Already on the newest build — nothing to update.",
                    "detail": out.strip()}
        return {"ok": True, "changed": True, "restart_required": True,
                "message": "yt-dlp updated. Restart SoulSync for it to take effect — "
                           "the running process is still using the version it loaded at startup.",
                "detail": out.strip()}

    if "permission denied" in low or "access is denied" in low or "errno 13" in low:
        return {"ok": False, "restart_required": False,
                "message": "No permission to write to this Python environment. SoulSync would "
                           "need to run as the user that owns it, or you can update it yourself.",
                "detail": out.strip()}
    if "externally-managed-environment" in low or "externally managed" in low:
        return {"ok": False, "restart_required": False,
                "message": "This Python is managed by the operating system, so pip won't write "
                           "to it. Update yt-dlp through your package manager, or run SoulSync "
                           "in a virtualenv.",
                "detail": out.strip()}
    if "no module named pip" in low or "no such file" in low:
        return {"ok": False, "restart_required": False,
                "message": "pip isn't available to this Python, so SoulSync can't install "
                           "anything. Update yt-dlp yourself in that environment.",
                "detail": out.strip()}
    if ("could not find a version" in low or "no matching distribution" in low
            or "temporary failure in name resolution" in low or "network is unreachable" in low
            or "connection" in low and "error" in low):
        return {"ok": False, "restart_required": False,
                "message": "Couldn't reach PyPI to fetch yt-dlp. Check this machine's network "
                           "or proxy settings.",
                "detail": out.strip()}
    if "read-only file system" in low:
        return {"ok": False, "restart_required": False,
                "message": "The Python environment is on a read-only filesystem — likely a "
                           "container. Rebuild the image with a newer yt-dlp instead.",
                "detail": out.strip()}
    return {"ok": False, "restart_required": False,
            "message": "pip couldn't install yt-dlp (exit code %d). The output below says why."
                       % code,
            "detail": out.strip() or "pip produced no output."}


def run_update(channel: Any = DEFAULT_CHANNEL, *, python: Optional[str] = None,
               runner: Optional[Callable] = None) -> Dict[str, Any]:
    """Install the newest yt-dlp on ``channel``. ``runner(cmd, timeout)`` returns
    ``(returncode, stdout, stderr)``; injected so this is testable without touching
    the real environment."""
    import sys
    cmd = pip_command(channel, python or sys.executable)
    before = installed_version()
    if runner is None:
        def runner(c, timeout):
            import subprocess
            p = subprocess.run(c, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
    try:
        code, out, err = runner(cmd, _TIMEOUT)
    except Exception as e:   # noqa: BLE001 - a timeout or a missing python must not 500
        return {"ok": False, "restart_required": False,
                "message": "Couldn't run pip: %s" % e, "detail": "", "channel":
                normalize_channel(channel)}
    res = interpret_result(code, out, err, installed_before=before)
    res["channel"] = normalize_channel(channel)
    res["previous_version"] = before
    return res


__all__ = ["STABLE", "NIGHTLY", "DEFAULT_CHANNEL", "PYPI_URL", "normalize_channel",
           "pip_command", "parse_pypi", "installed_version", "is_behind",
           "interpret_result", "run_update"]
