"""the page shell's pre-paint accent must actually render.

a formatter once split `{{ initial_accent_rgb }}` into `{ { initial_accent_rgb } }`
across lines. jinja doesn't see that as a tag, so every page shipped the literal
text as the css value. admins never noticed (settings re-apply the accent
right after load); a member can't read /api/settings, so with nothing in
localStorage they got an app with no accent at all (found sept 25 2026 in a
live smoke of the profile rebuild).
"""

from __future__ import annotations

import os
import re
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-appearance-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'a.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'


def test_the_template_tags_are_whole():
    html = open('webui/index.html', encoding='utf-8').read()
    for var in ('initial_accent_rgb', 'initial_accent_light_rgb', 'initial_accent_neon_rgb'):
        assert '{{ %s }}' % var in html, var


web_server = pytest.importorskip('web_server')


def test_the_shell_ships_real_accent_numbers():
    body = web_server.app.test_client().get('/').get_data(as_text=True)
    m = re.search(r'--accent-rgb:\s*([^;]+);', body)
    assert m and re.fullmatch(r'\d{1,3}, \d{1,3}, \d{1,3}', m.group(1).strip()), m and m.group(1)
