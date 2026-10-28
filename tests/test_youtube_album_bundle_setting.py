"""#1851's opt-in had no switch: download_source.youtube_album_bundle could only
be turned on by editing the stored config. the YouTube settings section carries
it now; these pin the checkbox, its load, its save, and the docs line."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')
SETTINGS = (ROOT / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')


def _download_source_block(js: str) -> str:
    start = js.index('async function saveSettings(')
    block = js.index('download_source: {', start)
    return js[block:js.index('\n        },', block)]


def test_the_checkbox_sits_in_the_youtube_section_unchecked():
    assert '<input type="checkbox" id="youtube-album-bundle">' in INDEX
    # same section as the other youtube download knobs
    assert INDEX.index('id="youtube-download-delay"') < INDEX.index('id="youtube-album-bundle"') \
        < INDEX.index('id="youtube-transcode"')


def test_save_writes_it_under_download_source_where_the_album_flow_reads_it():
    assert "youtube_album_bundle: _cfgBool('youtube-album-bundle')" in _download_source_block(SETTINGS)


def test_load_only_ticks_it_when_it_is_really_on():
    assert "settings.download_source?.youtube_album_bundle === true" in SETTINGS


def test_the_docs_describe_it():
    docs = (ROOT / 'webui' / 'static' / 'docs-content' / '04-search.js').read_text(encoding='utf-8')
    assert 'Download whole albums from YouTube Music' in docs
