"""#2156: a member briefly saw the whole admin sidebar while signing in.

the profile arrived over fetch after the sidebar had painted. the page now
renders the signed-in profile in (boot_profile), so profile-nav.js can cut
the nav down before first paint. these pin what goes in and when it's None.
"""

from pathlib import Path

from core.security.session_profile import boot_profile

ROOT = Path(__file__).resolve().parents[1]

MEMBER = {
    'id': 3, 'name': 'sam', 'is_admin': False, 'has_pin': True, 'recovery_question': 'pet?',
    'allowed_pages': ['video-search', 'video-discover', 'video-library'],
    'allowed_sides': 'video', 'can_download': False, 'home_page': None,
}
ADMIN = {'id': 1, 'name': 'boss', 'is_admin': True, 'allowed_pages': None,
         'allowed_sides': 'both', 'can_download': True, 'home_page': None}


def _lookup(*profiles):
    by_id = {p['id']: p for p in profiles}
    return lambda pid: by_id.get(pid)


def test_member_gets_their_permissions():
    """#2156: the nav needs the member's pages and sides before first paint"""
    got = boot_profile(3, _lookup(MEMBER))
    assert got == {
        'id': 3, 'is_admin': False,
        'allowed_pages': ['video-search', 'video-discover', 'video-library'],
        'allowed_sides': 'video', 'can_download': False, 'home_page': None,
    }


def test_only_the_nav_fields_go_into_the_page():
    """#2156: nothing beyond what the sidebar reads is rendered in"""
    got = boot_profile(3, _lookup(MEMBER))
    assert 'name' not in got and 'has_pin' not in got and 'recovery_question' not in got


def test_admin_is_known_too():
    """#2156: the admin's nav is drawn straight away, no waiting"""
    got = boot_profile(1, _lookup(ADMIN))
    assert got['is_admin'] is True and got['allowed_pages'] is None


def test_nobody_known_means_none():
    """#2156: signed out, locked or not picked: the page waits for init.js"""
    assert boot_profile(None, _lookup(ADMIN)) is None
    assert boot_profile(0, _lookup(ADMIN)) is None
    assert boot_profile(9, _lookup(ADMIN)) is None


def test_turned_off_member_gets_no_sidebar():
    """#2156: a disabled member is about to be signed out"""
    assert boot_profile(3, _lookup({**MEMBER, 'disabled': True})) is None
    assert boot_profile(1, _lookup({**ADMIN, 'disabled': True}))['id'] == 1


def test_lookup_error_is_none():
    """#2156: a db hiccup never breaks the page render"""
    def boom(_pid):
        raise RuntimeError('db gone')
    assert boot_profile(3, boom) is None


def test_page_renders_it_before_the_sidebar():
    """#2156: the template hands it to profile-nav.js, loaded under the nav"""
    html = (ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')
    rendered = html.index('window.SOULSYNC_BOOT_PROFILE = {{ boot_profile | tojson }}')
    nav_script = html.index("filename='profile-nav.js'")
    video_nav = html.index('<nav class="sidebar-nav video-nav"')
    features_replay = html.index("localStorage.getItem('ss_features')")
    assert rendered < video_nav < nav_script < features_replay
    server = (ROOT / 'web_server.py').read_text(encoding='utf-8')
    assert "'boot_profile': boot_profile(getattr(g, 'profile_id', None)" in server
