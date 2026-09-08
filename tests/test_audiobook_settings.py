"""Guards for the audiobook settings surface.

Two things are being protected here.

The first is that audiobooks have their OWN download source chain. Music's chain
lists tidal, qobuz, hifi, deezer and amazon — music-streaming services with no
audiobooks in them at all — so a book searched down the music chain burns an
attempt on each of them before it can succeed. The download ENGINE is shared on
purpose (an audiobook is structurally an album: a directory of ordered chapter
files with shared metadata, which is what album_bundle already handles); only the
ordering of sources differs.

The second is that a setting nobody wired up is a setting that does not exist.
Every new field is checked all the way through: the default, the input in
index.html, the code that loads it, and the code that saves it.
"""

from pathlib import Path

import pytest

from core.settings import ConfigManager

_ROOT = Path(__file__).resolve().parents[1]

# Sources that cannot serve an audiobook. Adding one of these to the audiobook
# chain means every book search spends an attempt on a music-only service.
_MUSIC_ONLY_SOURCES = ("tidal", "qobuz", "hifi", "deezer", "amazon", "lidarr", "bandcamp")


@pytest.fixture(scope="module")
def defaults():
    """The default config template, read without touching the real database."""
    return ConfigManager.__new__(ConfigManager)._get_default_config()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_audiobooks_have_their_own_library_path(defaults):
    assert defaults["library"]["audiobooks_path"]


def test_audiobook_path_is_not_the_music_or_podcast_path(defaults):
    # Chapter files under a music root get indexed as albums by every media
    # server there is.
    library = defaults["library"]
    assert library["audiobooks_path"] != library["podcasts_path"]
    assert library["audiobooks_path"] not in (library["music_paths"] or [])


def test_audiobook_organization_template_exists(defaults):
    template = defaults["file_organization"]["templates"]["audiobook_path"]
    assert "$author" in template
    assert "$title" in template


def test_audiobook_block_defaults(defaults):
    audiobooks = defaults["audiobooks"]
    assert audiobooks["download_path"]
    assert audiobooks["embed_metadata"] is True
    assert audiobooks["renumber_chapters"] is True


def test_audiobooks_do_not_inherit_the_music_source_chain(defaults):
    # The whole reason this block exists.
    music_mode = defaults["download_source"]["mode"]
    audiobook_source = defaults["audiobooks"]["download_source"]
    assert "mode" in audiobook_source
    assert audiobook_source is not defaults["download_source"]
    assert music_mode not in _MUSIC_ONLY_SOURCES or audiobook_source["mode"] != music_mode


@pytest.mark.parametrize("source", _MUSIC_ONLY_SOURCES)
def test_the_audiobook_chain_lists_no_music_only_service(defaults, source):
    chain = defaults["audiobooks"]["download_source"]
    assert chain["mode"] != source
    assert source not in (chain.get("hybrid_order") or [])


def test_the_audiobook_chain_is_made_of_real_sources(defaults):
    chain = defaults["audiobooks"]["download_source"]
    allowed = {"soulseek", "torrent", "usenet", "hybrid"}
    assert chain["mode"] in allowed
    for source in chain.get("hybrid_order") or []:
        assert source in allowed


def test_audiobooks_search_the_audiobook_indexer_category(defaults):
    # 3030 is Newznab's audiobook category. core/prowlarr_client.py already
    # defines it and deliberately keeps it out of music searches, so asking for
    # it here costs the music side nothing.
    from core.prowlarr_client import MUSIC_CATEGORY_AUDIOBOOK

    assert defaults["audiobooks"]["prowlarr_categories"] == [MUSIC_CATEGORY_AUDIOBOOK]


def test_the_podcast_settings_are_untouched(defaults):
    # Audiobooks were added beside podcasts; nothing about them should have moved.
    podcasts = defaults["podcasts"]
    assert podcasts["download_path"]
    assert podcasts["media_format"] == "audio"
    assert defaults["file_organization"]["templates"]["podcast_path"]


# ---------------------------------------------------------------------------
# Wiring — a setting nobody reads or writes is not a setting
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def index_html():
    return (_ROOT / "webui/index.html").read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def settings_js():
    return (_ROOT / "webui/static/settings.js").read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize("element_id", ["audiobooks-path", "template-audiobook-path"])
def test_the_settings_page_has_the_input(index_html, element_id):
    assert f'id="{element_id}"' in index_html


def test_the_folder_path_input_can_be_unlocked(index_html, settings_js):
    # Every other output path has an unlock button plus an entry in the path map;
    # without the map entry the button silently does nothing.
    assert "togglePathLock('audiobooks'" in index_html
    assert "audiobooks: 'audiobooks-path'" in settings_js


@pytest.mark.parametrize("element_id", ["audiobooks-path", "template-audiobook-path"])
def test_settings_js_reads_and_writes_the_input(settings_js, element_id):
    assert settings_js.count(f"getElementById('{element_id}')") >= 2


def test_settings_js_persists_the_audiobook_fields(settings_js):
    assert "audiobooks_path:" in settings_js
    assert "audiobook_path:" in settings_js


def test_the_template_reset_covers_audiobooks(settings_js):
    # "Reset to defaults" that skips a field leaves a stale template behind.
    assert "defaults.audiobook" in settings_js
