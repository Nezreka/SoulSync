"""One Soulseek client, not one per call.

Every helper in core/audiobook_soulseek.py built its own SoulseekClient when it
was not handed one. That constructor logs at INFO and mkdirs the download path,
and the audiobook download monitor ticks every few seconds touching several of
these per pass — so app.log filled with repeated "Soulseek client configured
with slskd at ..." lines and the filesystem was hit for each one. Reported as a
"wtf is this?" while reading the log, which is the right reaction: nothing was
broken, but nothing needed doing either.

Both halves are here: the cache's behaviour, and the structural invariant that a
helper never reaches for the constructor instead of the cache.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = (_ROOT / 'core' / 'audiobook_soulseek.py').read_text(encoding='utf-8')
_CLIENT_SOURCE = (_ROOT / 'core' / 'soulseek_client.py').read_text(encoding='utf-8')


def _below_the_cache() -> str:
    """Everything after _shared_client's own definition."""
    assert 'def _shared_client' in _SOURCE, 'the shared client builder is gone'
    after = _SOURCE.split('def _shared_client', 1)[1]
    # its body ends at the next top-level def
    return re.split(r'\ndef ', after, maxsplit=1)[1]


def test_there_is_a_shared_client_builder():
    assert 'def _shared_client' in _SOURCE


def test_no_helper_constructs_its_own_client():
    assert 'SoulseekClient()' not in _below_the_cache(), (
        'a helper builds its own client again; call _shared_client() instead'
    )


def test_the_audiobook_helper_never_constructs_a_client():
    """Six helpers used to build their own. None should now."""
    assert 'SoulseekClient()' not in _SOURCE


def _builder() -> str:
    return _CLIENT_SOURCE.split('def get_shared_soulseek_client', 1)[1].split('\ndef ', 1)[0]


def test_the_cache_is_keyed_rather_than_permanent():
    """Cached outright, saving a new slskd url or key would need a restart."""
    assert '_SHARED_CACHE["key"]' in _builder()
    assert 'slskd_url' in _builder() and 'api_key' in _builder()


def test_the_cache_is_locked():
    """The monitor thread and a request thread can both ask at once."""
    assert 'with _SHARED_LOCK' in _builder()


def test_a_broken_config_read_still_yields_a_client():
    """A config that cannot be read must not take Soulseek down with it."""
    assert 'except Exception' in _builder()


def test_the_audiobook_side_reads_no_soulseek_config():
    """test_audiobooks_isolation holds this line: music's soulseek settings must
    never decide anything on the audiobook side. The cache key is a config read,
    so the cache belongs in the client module, not here."""
    for line in _SOURCE.splitlines():
        if 'config_manager.get(' in line:
            assert 'audiobooks.' in line, line.strip()


# ---------------------------------------------------------------------------
# the cache itself
# ---------------------------------------------------------------------------

@pytest.fixture
def soulseek(monkeypatch):
    built = []

    class _FakeClient:
        def __init__(self):
            built.append(1)
            self.base_url = 'http://slskd:5030'
            self.download_path = '/downloads'

    # patched on the REAL module rather than swapping sys.modules: installing a
    # fake module leaks into every later test in the session, which is the
    # pollution class that already breaks this suite elsewhere
    import core.soulseek_client as sc
    monkeypatch.setattr(sc, 'SoulseekClient', _FakeClient)
    monkeypatch.setitem(sc._SHARED_CACHE, 'key', None)
    monkeypatch.setitem(sc._SHARED_CACHE, 'client', None)

    import core.audiobook_soulseek as ab

    settings = {'slskd_url': 'http://slskd:5030', 'api_key': 'k'}

    class _Cfg:
        def get(self, key, default=None):
            return settings if key == 'soulseek' else default

    monkeypatch.setattr(sc, 'config_manager', _Cfg(), raising=False)
    return ab, built, settings


def test_repeated_calls_build_one_client(soulseek):
    ab, built, _ = soulseek
    for _ in range(50):
        ab._shared_client()
    assert len(built) == 1, f'built {len(built)} clients for 50 calls'


def test_changed_slskd_settings_rebuild_it(soulseek):
    ab, built, settings = soulseek
    ab._shared_client()
    settings['api_key'] = 'a-new-key'
    ab._shared_client()
    assert len(built) == 2, 'a saved slskd change would need a restart'


def test_unchanged_settings_do_not_rebuild_it(soulseek):
    ab, built, settings = soulseek
    ab._shared_client()
    settings['api_key'] = 'k'
    ab._shared_client()
    assert len(built) == 1


def test_it_returns_the_same_instance(soulseek):
    ab, _built, _ = soulseek
    assert ab._shared_client() is ab._shared_client()


def test_the_builder_does_not_call_itself(soulseek):
    """It did. A regex meant for the six call sites rewrote the constructor
    inside the builder too, so _shared_client recursed forever and every test
    that touched it hung rather than failed."""
    ab, built, _ = soulseek
    assert ab._shared_client() is not None
    assert len(built) == 1
