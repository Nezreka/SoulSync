"""#2005: navidrome lists every client name it sees as its own player, so
the connection test's lowercase "soulsync" showed up beside "SoulSync"."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_every_subsonic_call_names_the_client_the_same_way():
    names = set()
    for path in list((_ROOT / "core").rglob("*.py")) + list((_ROOT / "api").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        names.update(re.findall(r"['\"]c['\"]\s*:\s*['\"]([^'\"]+)['\"]", text))
        names.update(re.findall(r"[&?]c=([A-Za-z0-9_-]+)", text))
    assert names == {"SoulSync"}, names
