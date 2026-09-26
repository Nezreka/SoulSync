"""the chat avatar set: 1-99 cartoons, 100 reserved, 101-244 the games set.

the count lives in two places, chat.js (what the picker shows and what a
render accepts) and api/chat.py (what the server lets you save). if they
drift the picker offers faces the server refuses, or the server accepts ids
no client draws. every id also needs its file or the picker shows a broken
image.
"""

from __future__ import annotations

import re
from pathlib import Path

import api.chat as chat_api

ROOT = Path(__file__).resolve().parents[1]
AVATAR_DIR = ROOT / "webui" / "static" / "avatar"


def _js_count() -> int:
    js = (ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    m = re.search(r"var CHAT_AVATARS = (\d+);", js)
    assert m, "chat.js lost its CHAT_AVATARS constant"
    return int(m.group(1))


def test_the_client_and_server_agree_on_the_count():
    assert _js_count() == chat_api.AVATAR_COUNT


def test_every_avatar_id_has_its_image():
    missing = [n for n in range(1, chat_api.AVATAR_COUNT + 1)
               if not (AVATAR_DIR / f"{n}.png").is_file()]
    assert missing == []


def test_the_games_set_is_in():
    assert chat_api.AVATAR_COUNT == 244
    # the owner's reserved face is still his alone
    assert chat_api.RESERVED_AVATARS == {100: "boulderbadgedad"}
