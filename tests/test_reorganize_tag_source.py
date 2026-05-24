"""Boundary tests for ``core.library.reorganize_tag_source``.

Pin every shape the embedded-tag → reorganize-context adapter has to
handle so future drift fails here instead of at runtime against a
real library: empty / missing essentials, multi-value vs single-string
artist tags, ID3-style ``"5/12"`` track-number values, year
normalization across date shapes, releasetype validation, multi-disc
parsing, defensive paths against bad input.

The wrapper :func:`read_album_track_from_file` is tested against a
fake ``read_embedded_tags_fn`` so no real mutagen IO happens here.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict

import pytest


# ── stubs (other tests rely on these too — keep the shape consistent) ──
if 'utils.logging_config' not in sys.modules:
    utils_mod = types.ModuleType('utils')
    logging_mod = types.ModuleType('utils.logging_config')
    logging_mod.get_logger = lambda name: type('L', (), {
        'debug': lambda *a, **k: None,
        'info': lambda *a, **k: None,
        'warning': lambda *a, **k: None,
        'error': lambda *a, **k: None,
    })()
    sys.modules['utils'] = utils_mod
    sys.modules['utils.logging_config'] = logging_mod


from core.library.reorganize_tag_source import (
    extract_album_meta_from_tags,
    extract_track_meta_from_tags,
    read_album_track_from_file,
    normalize_resolved_path,
)


# ─── extract_track_meta_from_tags ─────────────────────────────────────


class TestExtractTrackMeta:
    def test_full_set_returns_canonical_shape(self):
        out = extract_track_meta_from_tags({
            'title': 'HUMBLE.',
            'artist': 'Kendrick Lamar',
            'tracknumber': '4',
            'discnumber': '1',
        })
        assert out is not None
        assert out['name'] == 'HUMBLE.'
        assert out['title'] == 'HUMBLE.'
        assert out['track_number'] == 4
        assert out['disc_number'] == 1
        assert out['artists'] == [{'name': 'Kendrick Lamar'}]
        assert out['duration_ms'] == 0
        assert out['id'] == ''
        assert out['uri'] == ''

    def test_missing_title_returns_none(self):
        assert extract_track_meta_from_tags({'artist': 'foo'}) is None
        assert extract_track_meta_from_tags({'title': '', 'artist': 'foo'}) is None
        assert extract_track_meta_from_tags({'title': '   ', 'artist': 'foo'}) is None

    def test_missing_artist_returns_none(self):
        assert extract_track_meta_from_tags({'title': 'Song'}) is None
        assert extract_track_meta_from_tags({'title': 'Song', 'artist': ''}) is None

    def test_multi_value_artists_field_takes_precedence(self):
        out = extract_track_meta_from_tags({
            'title': 'Collab',
            'artist': 'Foo Bar',
            'artists': 'Foo, Bar, Baz',  # multi-value tag joined by reader
        })
        assert out is not None
        assert out['artists'] == [{'name': 'Foo'}, {'name': 'Bar'}, {'name': 'Baz'}]

    def test_artist_string_split_on_known_separators(self):
        for sep_input, expected in [
            ('Foo, Bar', ['Foo', 'Bar']),
            ('Foo & Bar', ['Foo', 'Bar']),
            ('Foo feat. Bar', ['Foo', 'Bar']),
            ('Foo ft Bar', ['Foo', 'Bar']),
            ('Foo featuring Bar', ['Foo', 'Bar']),
            ('Foo / Bar', ['Foo', 'Bar']),
            ('Foo; Bar', ['Foo', 'Bar']),
            ('Foo x Bar', ['Foo', 'Bar']),
            ('Foo with Bar', ['Foo', 'Bar']),
        ]:
            out = extract_track_meta_from_tags({'title': 't', 'artist': sep_input})
            assert out is not None
            names = [a['name'] for a in out['artists']]
            assert names == expected, f"failed for {sep_input!r}: got {names}"

    def test_artist_dedup_case_insensitive(self):
        out = extract_track_meta_from_tags({
            'title': 't',
            'artists': 'Foo, foo, FOO, Bar',
        })
        assert out is not None
        names = [a['name'] for a in out['artists']]
        assert names == ['Foo', 'Bar']

    def test_id3_track_total_shape(self):
        # ID3 stores TRCK as "5/12" — caller must use the head only.
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
            'tracknumber': '5/12',
        })
        assert out['track_number'] == 5

    def test_disc_total_shape(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
            'discnumber': '2/2',
        })
        assert out['disc_number'] == 2

    def test_track_number_default_to_one(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
        })
        assert out['track_number'] == 1
        assert out['disc_number'] == 1

    def test_track_number_zero_or_negative_falls_back_to_one(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a', 'tracknumber': '0',
        })
        assert out['track_number'] == 1  # or-default of 0 → 1

    def test_track_number_unparseable_falls_back_to_one(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
            'tracknumber': 'side-a-2',
        })
        assert out['track_number'] == 1

    def test_int_track_number(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
            'tracknumber': 5,
            'discnumber': 2,
        })
        assert out['track_number'] == 5
        assert out['disc_number'] == 2

    def test_float_track_number_truncated(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
            'tracknumber': 5.7,
        })
        assert out['track_number'] == 5

    def test_zero_padded_track_number(self):
        out = extract_track_meta_from_tags({
            'title': 't', 'artist': 'a',
            'tracknumber': '03',
        })
        assert out['track_number'] == 3

    def test_non_dict_input(self):
        assert extract_track_meta_from_tags(None) is None
        assert extract_track_meta_from_tags([]) is None
        assert extract_track_meta_from_tags('') is None

    def test_empty_dict(self):
        assert extract_track_meta_from_tags({}) is None


# ─── extract_album_meta_from_tags ─────────────────────────────────────


class TestExtractAlbumMeta:
    def test_full_set(self):
        out = extract_album_meta_from_tags({
            'album': 'DAMN.',
            'albumartist': 'Kendrick Lamar',
            'date': '2017-04-14',
            'totaltracks': '14',
            'releasetype': 'Album',
        })
        assert out['name'] == 'DAMN.'
        assert out['title'] == 'DAMN.'
        assert out['album_artist'] == 'Kendrick Lamar'
        assert out['release_date'] == '2017'
        assert out['total_tracks'] == 14
        assert out['album_type'] == 'album'
        assert out['image_url'] == ''
        assert out['id'] == ''

    def test_year_normalization_from_full_date(self):
        for date_input, expected_year in [
            ('2020-01-15', '2020'),
            ('2020', '2020'),
            ('2020-01', '2020'),
            ('Jan 5, 2020', '2020'),
            ('1999/12/31', '1999'),
        ]:
            out = extract_album_meta_from_tags({'album': 'a', 'date': date_input})
            assert out['release_date'] == expected_year, f"date={date_input!r}"

    def test_year_falls_back_to_year_field(self):
        out = extract_album_meta_from_tags({'album': 'a', 'year': '2018'})
        assert out['release_date'] == '2018'

    def test_year_falls_back_to_originaldate(self):
        out = extract_album_meta_from_tags({'album': 'a', 'originaldate': '2010'})
        assert out['release_date'] == '2010'

    def test_year_missing_returns_empty(self):
        out = extract_album_meta_from_tags({'album': 'a'})
        assert out['release_date'] == ''

    def test_totaltracks_from_id3_shape(self):
        # ID3 may store track_number as "5/12" — use the trailing 12.
        out = extract_album_meta_from_tags({
            'album': 'a', 'tracknumber': '5/12',
        })
        assert out['total_tracks'] == 12

    def test_totaltracks_explicit_field_wins(self):
        out = extract_album_meta_from_tags({
            'album': 'a', 'totaltracks': '14', 'tracknumber': '5/12',
        })
        assert out['total_tracks'] == 14

    def test_totaltracks_tracktotal_alias(self):
        out = extract_album_meta_from_tags({'album': 'a', 'tracktotal': '8'})
        assert out['total_tracks'] == 8

    def test_releasetype_canonical(self):
        for input_val, expected in [
            ('album', 'album'), ('Album', 'album'),
            ('single', 'single'), ('Single', 'single'),
            ('ep', 'ep'), ('EP', 'ep'),
            ('compilation', 'compilation'),
            ('soundtrack', ''),  # not in canonical set
            ('mixtape', ''),
            ('', ''),
        ]:
            out = extract_album_meta_from_tags({
                'album': 'a', 'releasetype': input_val,
            })
            assert out['album_type'] == expected, f"releasetype={input_val!r}"

    def test_total_discs_explicit_field(self):
        out = extract_album_meta_from_tags({
            'album': 'a', 'totaldiscs': '2',
        })
        assert out['total_discs'] == 2

    def test_total_discs_from_id3_disc_form(self):
        out = extract_album_meta_from_tags({
            'album': 'a', 'discnumber': '1/2',
        })
        assert out['total_discs'] == 2

    def test_total_discs_explicit_wins_over_disc_form(self):
        # When both present, take the larger (defensive against drift).
        out = extract_album_meta_from_tags({
            'album': 'a', 'discnumber': '1/2', 'totaldiscs': '3',
        })
        assert out['total_discs'] == 3

    def test_total_discs_missing_zero(self):
        out = extract_album_meta_from_tags({
            'album': 'a', 'discnumber': '1',
        })
        assert out['total_discs'] == 0  # caller defaults via max() with disc count

    def test_album_artist_underscore_alias(self):
        out = extract_album_meta_from_tags({
            'album': 'a', 'album_artist': 'Foo',
        })
        assert out['album_artist'] == 'Foo'

    def test_missing_album_returns_empty_name(self):
        out = extract_album_meta_from_tags({})
        assert out['name'] == ''
        # All other fields should still be present (zero/empty), so the
        # caller's downstream consumer doesn't KeyError.
        assert 'release_date' in out
        assert 'total_tracks' in out

    def test_non_dict_input_safe(self):
        out = extract_album_meta_from_tags(None)  # type: ignore
        assert out['name'] == ''


# ─── read_album_track_from_file ───────────────────────────────────────


class TestReadAlbumTrackFromFile:
    def test_unavailable_result_returns_reason(self):
        def fake_reader(_p):
            return {'available': False, 'reason': 'No file.'}

        a, t, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert a is None and t is None
        assert err == 'No file.'

    def test_unavailable_no_reason_falls_back(self):
        def fake_reader(_p):
            return {'available': False}

        _, _, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert 'Could not read embedded tags' in (err or '')

    def test_non_dict_result_safe(self):
        def fake_reader(_p):
            return None

        a, t, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert a is None and t is None
        assert err

    def test_essentials_missing_track_returns_reason(self):
        # Title missing → unmatched.
        def fake_reader(_p):
            return {'available': True, 'tags': {'artist': 'a', 'album': 'b'}}

        a, t, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert a is None and t is None
        assert 'title' in (err or '').lower()

    def test_essentials_missing_album_returns_reason(self):
        # Album missing → unmatched even if track meta extracted.
        def fake_reader(_p):
            return {'available': True, 'tags': {'title': 't', 'artist': 'a'}}

        a, t, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert a is None and t is None
        assert 'album' in (err or '').lower()

    def test_full_extraction(self):
        def fake_reader(_p):
            return {
                'available': True,
                'duration': 234.5,
                'tags': {
                    'title': 'HUMBLE.',
                    'artist': 'Kendrick Lamar',
                    'album': 'DAMN.',
                    'albumartist': 'Kendrick Lamar',
                    'tracknumber': '4/14',
                    'discnumber': '1/1',
                    'date': '2017-04-14',
                    'releasetype': 'Album',
                },
            }

        album, track, err = read_album_track_from_file(
            '/fake.flac', read_embedded_tags_fn=fake_reader,
        )
        assert err is None
        assert track is not None and album is not None
        assert track['name'] == 'HUMBLE.'
        assert track['track_number'] == 4
        assert track['disc_number'] == 1
        assert track['duration_ms'] == 234500
        assert album['name'] == 'DAMN.'
        assert album['release_date'] == '2017'
        assert album['total_tracks'] == 14
        assert album['album_type'] == 'album'

    def test_duration_zero_when_missing(self):
        def fake_reader(_p):
            return {
                'available': True,
                'tags': {
                    'title': 't', 'artist': 'a', 'album': 'b',
                },
            }

        _, track, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert err is None
        assert track['duration_ms'] == 0

    def test_duration_unparseable_zero(self):
        def fake_reader(_p):
            return {
                'available': True,
                'duration': 'banana',
                'tags': {'title': 't', 'artist': 'a', 'album': 'b'},
            }

        _, track, err = read_album_track_from_file('fake', read_embedded_tags_fn=fake_reader)
        assert err is None
        assert track['duration_ms'] == 0

    def test_empty_path(self):
        a, t, err = read_album_track_from_file('')
        assert a is None and t is None
        assert err

    def test_non_string_path(self):
        a, t, err = read_album_track_from_file(None)  # type: ignore
        assert a is None and t is None
        assert err


# ─── normalize_resolved_path ──────────────────────────────────────────


class TestNormalizeResolvedPath:
    def test_returns_path_when_exists(self, tmp_path):
        f = tmp_path / 'x.flac'
        f.write_bytes(b'')
        assert normalize_resolved_path(str(f)) == str(f)

    def test_none_when_missing(self, tmp_path):
        assert normalize_resolved_path(str(tmp_path / 'no.flac')) is None

    def test_empty_input_safe(self):
        assert normalize_resolved_path('') is None
        assert normalize_resolved_path(None) is None


# ─── plan_album_reorganize (tag-mode integration) ────────────────────
#
# Pin the wiring between the planner branch and the tag-source helper
# so the additive-and-optional contract holds: API mode unchanged,
# tag mode produces matched plan items shaped like API mode (so
# downstream post-process treats them identically).


def _stub_metadata_service(monkeypatch):
    """Inject a minimal `core.metadata_service` so `library_reorganize`
    imports cleanly even in the test process where the real metadata
    clients aren't wired."""
    if 'core' not in sys.modules:
        sys.modules['core'] = types.ModuleType('core')
    if 'core.metadata_service' in sys.modules:
        return
    fake = types.ModuleType('core.metadata_service')
    fake.get_album_for_source = lambda *a, **k: {}
    fake.get_album_tracks_for_source = lambda *a, **k: []
    fake.get_client_for_source = lambda *a, **k: None
    fake.get_primary_source = lambda: 'deezer'
    fake.get_source_priority = lambda primary=None: ['deezer', 'spotify', 'itunes']
    sys.modules['core.metadata_service'] = fake


class TestPlannerTagModeIntegration:
    def test_tag_mode_planner_matches_every_track_with_good_tags(self, monkeypatch):
        _stub_metadata_service(monkeypatch)
        from core import library_reorganize as lr

        per_path = {
            '/a/track1.flac': {
                'available': True, 'duration': 200,
                'tags': {
                    'title': 'Song A', 'artist': 'Foo', 'album': 'AlbumX',
                    'tracknumber': '1/3', 'discnumber': '1/1',
                    'date': '2020', 'releasetype': 'Album',
                },
            },
            '/a/track2.flac': {
                'available': True, 'duration': 230,
                'tags': {
                    'title': 'Song B', 'artist': 'Foo', 'album': 'AlbumX',
                    'tracknumber': '2/3', 'discnumber': '1/1',
                    'date': '2020', 'releasetype': 'Album',
                },
            },
        }
        monkeypatch.setattr(
            'core.library.file_tags.read_embedded_tags',
            lambda p: per_path.get(p, {'available': False, 'reason': 'missing'}),
        )
        plan = lr.plan_album_reorganize(
            album_data={'artist_name': 'Foo', 'title': 'AlbumX'},
            tracks=[
                {'id': 't1', 'title': 'Song A', 'track_number': 1, 'file_path': '/a/track1.flac'},
                {'id': 't2', 'title': 'Song B', 'track_number': 2, 'file_path': '/a/track2.flac'},
            ],
            metadata_source='tags',
            resolve_file_path_fn=lambda p: p,
        )
        assert plan['status'] == 'planned'
        assert plan['source'] == 'tags'
        assert plan['total_discs'] == 1
        assert len(plan['items']) == 2
        for it in plan['items']:
            assert it['matched'] is True
            assert it['api_track']['name'] in ('Song A', 'Song B')
            assert it['api_album']['name'] == 'AlbumX'
            assert it['api_album']['album_type'] == 'album'

    def test_tag_mode_partial_disc_uses_tagged_total_discs(self, monkeypatch):
        # User has only disc 2 of a 2-disc album; tags say so.
        # max_disc must reflect tagged total so path builder still
        # routes into the multi-disc subfolder.
        _stub_metadata_service(monkeypatch)
        from core import library_reorganize as lr

        monkeypatch.setattr(
            'core.library.file_tags.read_embedded_tags',
            lambda p: {
                'available': True,
                'tags': {
                    'title': 'Song A', 'artist': 'Foo', 'album': 'Y',
                    'tracknumber': '1/8', 'discnumber': '2/2',
                    'totaldiscs': '2',
                },
            },
        )
        plan = lr.plan_album_reorganize(
            album_data={'artist_name': 'Foo', 'title': 'Y'},
            tracks=[{'id': 't1', 'title': 'Song A', 'track_number': 1, 'file_path': '/a.flac'}],
            metadata_source='tags',
            resolve_file_path_fn=lambda p: p,
        )
        assert plan['status'] == 'planned'
        assert plan['total_discs'] == 2

    def test_tag_mode_file_missing_unmatched_with_reason(self, monkeypatch):
        _stub_metadata_service(monkeypatch)
        from core import library_reorganize as lr

        plan = lr.plan_album_reorganize(
            album_data={'artist_name': 'Foo', 'title': 'X'},
            tracks=[{'id': 't1', 'title': 'Song', 'track_number': 1, 'file_path': '/missing.flac'}],
            metadata_source='tags',
            resolve_file_path_fn=lambda p: None,  # always missing
        )
        # All tracks unmatched → no_source_id status, source='tags'.
        assert plan['status'] == 'no_source_id'
        assert plan['source'] == 'tags'
        assert plan['items'][0]['matched'] is False
        assert 'no longer exists' in plan['items'][0]['reason'].lower()

    def test_tag_mode_some_match_some_unreadable(self, monkeypatch):
        _stub_metadata_service(monkeypatch)
        from core import library_reorganize as lr

        per_path = {
            '/good.flac': {
                'available': True,
                'tags': {'title': 'Good', 'artist': 'A', 'album': 'X', 'tracknumber': '1/2'},
            },
            '/bad.flac': {'available': False, 'reason': 'unreadable'},
        }
        monkeypatch.setattr(
            'core.library.file_tags.read_embedded_tags',
            lambda p: per_path.get(p, {'available': False, 'reason': 'missing'}),
        )
        plan = lr.plan_album_reorganize(
            album_data={'artist_name': 'A', 'title': 'X'},
            tracks=[
                {'id': 'g', 'title': 'Good', 'track_number': 1, 'file_path': '/good.flac'},
                {'id': 'b', 'title': 'Bad', 'track_number': 2, 'file_path': '/bad.flac'},
            ],
            metadata_source='tags',
            resolve_file_path_fn=lambda p: p,
        )
        assert plan['status'] == 'planned'
        assert plan['source'] == 'tags'
        matched = [it for it in plan['items'] if it['matched']]
        unmatched = [it for it in plan['items'] if not it['matched']]
        assert len(matched) == 1
        assert len(unmatched) == 1
        assert unmatched[0]['reason'] == 'unreadable'

    def test_tag_mode_without_resolver_returns_no_source_id(self, monkeypatch):
        # Defensive: caller forgot to pass resolve_file_path_fn.
        _stub_metadata_service(monkeypatch)
        from core import library_reorganize as lr

        plan = lr.plan_album_reorganize(
            album_data={'artist_name': 'A', 'title': 'X'},
            tracks=[{'id': 't1', 'title': 'Song', 'track_number': 1, 'file_path': '/a.flac'}],
            metadata_source='tags',
            resolve_file_path_fn=None,
        )
        assert plan['status'] == 'no_source_id'
        assert plan['items'][0]['matched'] is False
        assert 'requires the file path resolver' in plan['items'][0]['reason']

    def test_api_mode_unchanged_default(self, monkeypatch):
        # Regression guard: omitting metadata_source preserves the API
        # path — calls _resolve_source which calls our stubbed
        # metadata_service. Should land in 'no_source_id' since stubs
        # return empty.
        _stub_metadata_service(monkeypatch)
        from core import library_reorganize as lr

        plan = lr.plan_album_reorganize(
            album_data={'artist_name': 'Foo', 'title': 'Bar'},
            tracks=[{'id': 't1', 'title': 'Song', 'track_number': 1, 'file_path': '/a.flac'}],
        )
        # No metadata_source param → defaults to 'api' → empty stubs
        # produce no_source_id.
        assert plan['status'] == 'no_source_id'
        assert plan['source'] is None  # never reached the tags branch
