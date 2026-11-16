"""#1851's opt-in had no switch: download_source.youtube_album_bundle could only
be turned on by editing the stored config. the YouTube settings section carries
it now; these pin the checkbox, its load, its save, and the docs line."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# the YouTube source panel is React now (SETTINGS_PORT.md step 7g)
PANEL = (ROOT / 'webui' / 'src' / 'features' / 'settings' / 'tabs' / 'source-panels-more.tsx').read_text(
    encoding='utf-8')
SETTINGS = (ROOT / 'webui' / 'src' / 'features' / 'settings' / 'shell.ts').read_text(encoding='utf-8')


def _youtube(src: str) -> str:
    return src.split('export function YoutubePanel(', 1)[1].split('\nexport function', 1)[0]


def _album_bundle(src: str) -> str:
    """the album bundle CheckRow, from its id to the end of its props"""
    panel = _youtube(src)
    start = panel.index('id="youtube-album-bundle"')
    return panel[start:panel.index('/>', start)]


def test_the_checkbox_sits_in_the_youtube_section_unchecked():
    panel = _youtube(PANEL)
    # unchecked unless saved as on (a CheckRow renders a checkbox for this id)
    assert "checked={d.youtube_album_bundle === true}" in _album_bundle(PANEL)
    row = PANEL.split('function CheckRow(', 1)[1].split('\n}\n', 1)[0]
    assert 'type="checkbox"' in row and 'id={id}' in row
    # same section as the other youtube download knobs
    assert panel.index('id="youtube-download-delay"') < panel.index('id="youtube-album-bundle"') \
        < panel.index('id="youtube-transcode"')


def test_save_writes_it_under_download_source_where_the_album_flow_reads_it():
    assert "store.set('download_source', 'youtube_album_bundle', checked)" in _album_bundle(PANEL)
    # and the page save no longer writes it from a field that is not there
    assert "youtube_album_bundle" not in SETTINGS.split('async function saveSettings(', 1)[1]


def test_load_only_ticks_it_when_it_is_really_on():
    assert "d.youtube_album_bundle === true" in _album_bundle(PANEL)
    assert "const d = (settings.download_source || {})" in _youtube(PANEL)


def test_the_docs_describe_it():
    docs = (ROOT / 'webui' / 'static' / 'docs-content' / '04-search.js').read_text(encoding='utf-8')
    assert 'Download whole albums from YouTube Music' in docs
