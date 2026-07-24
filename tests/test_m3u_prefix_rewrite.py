"""M3U path-prefix hot-swap (wolf39us) — container path in, playback path out.

A playlist generated inside a container carries `/data/media/...` entries the
playback machine can't resolve; the mapping rewrites the prefix at generation
time (`M:/media/...`). Purely additive: both settings empty = byte-identical
output, pinned below. The transform lives in ONE place
(core.library.m3u_export.finalize_m3u_entry) shared by every writer, composed
with the '#' comment-guard from #1072.
"""

from __future__ import annotations

from pathlib import Path

from core.library.m3u_export import build_m3u, finalize_m3u_entry

_ROOT = Path(__file__).resolve().parent


def _entries():
    return [{'path': '/data/media/music/"Weird Al" Yankovic/1996 - Bad Hair Day/01 - Amish Paradise.flac',
             'artist': '"Weird Al" Yankovic', 'title': 'Amish Paradise', 'duration': 199}]


def test_prefix_hot_swap_rewrites_the_entry():
    out = build_m3u(_entries(), rewrite_from='/data/media', rewrite_to='M:/media')
    assert 'M:/media/music/"Weird Al" Yankovic/1996 - Bad Hair Day/01 - Amish Paradise.flac' in out
    assert '/data/media' not in out


def test_non_matching_entries_pass_through():
    out = build_m3u([{'path': '/other/root/x.flac', 'artist': 'A', 'title': 'X', 'duration': 1}],
                    rewrite_from='/data/media', rewrite_to='M:/media')
    assert '/other/root/x.flac' in out


def test_empty_settings_are_byte_identical():
    assert build_m3u(_entries()) == build_m3u(_entries(), rewrite_from='', rewrite_to='')


def test_rewrite_applies_after_base_prepend():
    """The mapping targets the FINAL entry line — base included — so the two
    knobs compose deterministically."""
    out = build_m3u([{'path': 'rel/x.flac', 'artist': 'A', 'title': 'X', 'duration': 1}],
                    entry_base_path='/data/media', rewrite_from='/data/media', rewrite_to='M:/media')
    assert 'M:/media/rel/x.flac' in out


def test_finalize_composes_with_the_hash_guard():
    # rewrite first, then the '#' guard on the result
    assert finalize_m3u_entry('/data/media/#/A/x.flac', '/data/media/', '') == './#/A/x.flac'
    assert finalize_m3u_entry('#/A/x.flac') == './#/A/x.flac'
    assert finalize_m3u_entry('/data/x.flac', '/data', 'M:') == 'M:/x.flac'
    assert finalize_m3u_entry('P/x.flac') == 'P/x.flac'


def test_web_server_writers_use_the_shared_finalizer():
    ws = (_ROOT.parent / 'web_server.py').read_text(encoding='utf-8')
    # both playlist writers wrap the FULL line (base included) in the finalizer
    assert ws.count("_m3u_entry_path(f'{entry_base_path}/{fp}' if entry_base_path else fp)") == 1
    assert ws.count("_m3u_entry_path(f'{entry_base_path}/{file_path}' if entry_base_path else file_path)") == 1
    assert "finalize_m3u_entry" in ws
    # library export + scan-sync auto-writer pass the mapping through
    assert ws.count("rewrite_from=config_manager.get('m3u_export.rewrite_from', '') or ''") >= 3


def test_settings_ui_round_trip_wiring():
    js = (_ROOT.parent / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    html = (_ROOT.parent / 'webui' / 'index.html').read_text(encoding='utf-8')
    assert 'id="m3u-rewrite-from"' in html and 'id="m3u-rewrite-to"' in html
    assert "rewrite_from: document.getElementById('m3u-rewrite-from').value || ''" in js
    assert "settings.m3u_export?.rewrite_from || ''" in js
