"""Release-aware update detection (Kazimir's version-glow ask).

The old check compared git commit SHAs — technically "an update exists"
but it couldn't say WHAT: the version button glowed identically for a
typo fix and a breaking release, and the What's New modal (bundled with
the shipped code) kept insisting the installed version was the newest.

This module is the pure half: parse release tags, compare against the
running base version, and classify the update:

  'update'   — a newer release exists (routine; glows green)
  'major'    — the MAJOR version increased (breaking/big; glows yellow)
  'critical' — any newer release is flagged critical/security/hotfix in
               its title or notes (glows red, ignores dismissal)

Fetching is a separate function so everything else is unit-testable
without network. No imports from web_server.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("update_check")

_CRITICAL_MARKERS = re.compile(r'\b(critical|security|hotfix|urgent)\b', re.I)
_SEMVER = re.compile(r'v?(\d+)\.(\d+)(?:\.(\d+))?')


def parse_semver(value: Any) -> Optional[tuple]:
    """'v3.0.5', '3.0.5+abc1234', '3.1' → (major, minor, patch). None when
    no version number can be found."""
    m = _SEMVER.search(str(value or ''))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def evaluate_update(current_base: str, releases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Judge the newest published release against the running version.

    ``releases`` is the GitHub /releases payload (newest first); drafts and
    prereleases are ignored. Returns {available, latest_version, severity,
    release_url, notes} — severity None when up to date or undeterminable.
    """
    current = parse_semver(current_base)
    out = {"available": False, "latest_version": None, "severity": None,
           "release_url": None, "notes": None}
    if current is None:
        return out
    newer = []
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get('draft') or rel.get('prerelease'):
            continue
        ver = parse_semver(rel.get('tag_name') or rel.get('name'))
        if ver is None or ver <= current:
            continue
        newer.append((ver, rel))
    if not newer:
        return out
    newer.sort(key=lambda pair: pair[0], reverse=True)
    latest_ver, latest_rel = newer[0]
    # A critical release ANYWHERE between here and latest makes the whole
    # jump critical — skipping straight past a security fix is still
    # running without it.
    critical = any(
        _CRITICAL_MARKERS.search(f"{rel.get('name') or ''} {rel.get('body') or ''}")
        for _v, rel in newer
    )
    if critical:
        severity = 'critical'
    elif latest_ver[0] > current[0]:
        severity = 'major'
    else:
        severity = 'update'
    out.update({
        "available": True,
        "latest_version": "%d.%d.%d" % latest_ver,
        "severity": severity,
        "release_url": latest_rel.get('html_url'),
        "notes": (latest_rel.get('name') or '').strip() or None,
    })
    return out


def fetch_releases(repo: str, timeout: int = 10, limit: int = 15) -> List[Dict[str, Any]]:
    """The newest published releases from GitHub. Raises on any failure —
    the caller owns caching + error policy."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page={limit}",
        headers={'Accept': 'application/vnd.github.v3+json',
                 'User-Agent': 'SoulSync-UpdateCheck'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data if isinstance(data, list) else []


__all__ = ["parse_semver", "evaluate_update", "fetch_releases"]
