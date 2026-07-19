"""Labels P4 — the label watchlist scan engine.

Purely additive automation. These pin the pure selection + orchestration logic
(the part that decides WHAT gets wishlisted): backlog gating, owned/wishlisted
dedupe, per-label error isolation, cancel, and mark-scanned. All I/O is faked —
the live album-resolution enqueue is deliberately out of scope here (it fails
safe and wants a live smoke test).
"""

from __future__ import annotations

from core.automation.handlers import scan_watchlist_labels as sl


def _cat(*rows):
    return [{'artist': a, 'album': al, 'year': y, 'release_group_id': rg}
            for (a, al, y, rg) in rows]


class TestSelectGaps:
    def test_skips_owned_and_wishlisted(self):
        cat = _cat(('A', 'One', '2024', 'rg1'), ('A', 'Two', '2023', 'rg2'),
                   ('A', 'Three', '2022', 'rg3'))
        owned = {'Two'}
        wished = {'Three'}
        picks = sl.select_label_release_gaps(
            cat, is_owned=lambda i: i['album'] in owned,
            is_wishlisted=lambda i: i['album'] in wished, backlog=True)
        assert [p['album'] for p in picks] == ['One']

    def test_backlog_off_skips_old_releases(self):
        cat = _cat(('A', 'New', '2025', 'rg1'), ('A', 'Old', '2019', 'rg2'))
        picks = sl.select_label_release_gaps(
            cat, is_owned=lambda i: False, is_wishlisted=lambda i: False,
            backlog=False, floor_year='2024')
        assert [p['album'] for p in picks] == ['New']       # Old is back-catalog

    def test_backlog_on_keeps_old_releases(self):
        cat = _cat(('A', 'New', '2025', 'rg1'), ('A', 'Old', '2019', 'rg2'))
        picks = sl.select_label_release_gaps(
            cat, is_owned=lambda i: False, is_wishlisted=lambda i: False,
            backlog=True, floor_year='2024')
        assert {p['album'] for p in picks} == {'New', 'Old'}

    def test_dedupe_error_skips_item_never_overadds(self):
        cat = _cat(('A', 'Boom', '2024', 'rg1'), ('A', 'Fine', '2024', 'rg2'))
        def is_owned(i):
            if i['album'] == 'Boom':
                raise RuntimeError('db down')
            return False
        picks = sl.select_label_release_gaps(
            cat, is_owned=is_owned, is_wishlisted=lambda i: False, backlog=True)
        assert [p['album'] for p in picks] == ['Fine']       # Boom skipped, not added

    def test_floor_year_from_date_added(self):
        assert sl._floor_year_of({'date_added': '2024-03-01 10:00:00'}) == '2024'
        assert sl._floor_year_of({'date_added': ''}) is None


class TestOrchestrator:
    def _run(self, labels, catalogs, **over):
        added = []
        scanned = []
        seams = dict(
            get_labels=lambda: labels,
            fetch_catalog=lambda mbid: catalogs.get(mbid, []),
            is_owned=lambda i: False,
            is_wishlisted=lambda i: False,
            add_release=lambda rel, lab: (added.append((lab['musicbrainz_label_id'], rel['album'])) or True),
            mark_scanned=lambda mbid: scanned.append(mbid),
        )
        seams.update(over)
        res = sl.run_label_watchlist_scan(**seams)
        return res, added, scanned

    def test_happy_path_adds_and_marks(self):
        labels = [{'musicbrainz_label_id': 'm1', 'label_name': 'Sub Pop', 'backlog': 1}]
        catalogs = {'m1': _cat(('Nirvana', 'Bleach', '1989', 'rg1'),
                               ('Mudhoney', 'Superfuzz', '1988', 'rg2'))}
        res, added, scanned = self._run(labels, catalogs)
        assert res['status'] == 'completed'
        assert res['labels_scanned'] == 1
        assert res['releases_found'] == 2 and res['releases_added'] == 2
        assert {a[1] for a in added} == {'Bleach', 'Superfuzz'}
        assert scanned == ['m1']

    def test_one_label_failure_is_isolated(self):
        labels = [{'musicbrainz_label_id': 'bad', 'label_name': 'X', 'backlog': 1},
                  {'musicbrainz_label_id': 'ok', 'label_name': 'Y', 'backlog': 1}]
        def fetch(mbid):
            if mbid == 'bad':
                raise RuntimeError('MB down')
            return _cat(('A', 'Good', '2024', 'rg1'))
        res, added, scanned = self._run(labels, {}, fetch_catalog=fetch)
        assert res['errors'] == 1
        assert res['releases_added'] == 1 and scanned == ['ok']   # 'ok' still scanned

    def test_add_failure_counts_but_continues(self):
        labels = [{'musicbrainz_label_id': 'm1', 'label_name': 'X', 'backlog': 1}]
        catalogs = {'m1': _cat(('A', 'One', '2024', 'rg1'), ('A', 'Two', '2024', 'rg2'))}
        def add(rel, lab):
            if rel['album'] == 'One':
                raise RuntimeError('wishlist boom')
            return True
        res, _, scanned = self._run(labels, catalogs, add_release=add)
        assert res['errors'] == 1 and res['releases_added'] == 1
        assert scanned == ['m1']         # marked despite one add failing

    def test_cancel_stops_early(self):
        labels = [{'musicbrainz_label_id': f'm{i}', 'label_name': str(i), 'backlog': 1}
                  for i in range(3)]
        catalogs = {f'm{i}': _cat(('A', f'Al{i}', '2024', f'rg{i}')) for i in range(3)}
        res, _, scanned = self._run(labels, catalogs, cancel_check=lambda: True)
        assert res['status'] == 'cancelled' and scanned == []

    def test_label_without_mbid_skipped(self):
        labels = [{'label_name': 'no id', 'backlog': 1},
                  {'musicbrainz_label_id': 'm2', 'label_name': 'Y', 'backlog': 1}]
        catalogs = {'m2': _cat(('A', 'Al', '2024', 'rg1'))}
        res, _, scanned = self._run(labels, catalogs)
        assert scanned == ['m2'] and res['labels_scanned'] == 1

    def test_empty_watchlist(self):
        res, added, scanned = self._run([], {})
        assert res['status'] == 'completed' and res['labels_scanned'] == 0
        assert added == [] and scanned == []


class _FakeDzAlbum:
    def __init__(self, aid, name):
        self.id = aid
        self.name = name


class _FakeDeezer:
    def __init__(self, search_name, meta):
        self._search_name = search_name
        self._meta = meta
    def search_albums(self, q, limit=5):
        return [_FakeDzAlbum('dz1', self._search_name)]
    def get_album_metadata(self, aid, include_tracks=True):
        return self._meta


def _meta(name='Bleach', items=None):
    return {'id': 'dz1', 'name': name, 'images': [{'url': 'https://cdn.deezer/x.jpg'}],
            'album_type': 'album', 'release_date': '1989',
            'artists': [{'name': 'Nirvana', 'id': '7'}],
            'tracks': {'items': items if items is not None else
                       [{'id': 't1', 'name': 'Blew'}, {'id': 't2', 'name': 'Floyd'}]}}


class TestResolveAlbum:
    def test_no_deezer_returns_nothing(self):
        assert sl.resolve_album_for_release(None, 'A', 'Al') == (None, [])

    def test_name_mismatch_rejected(self):
        # search returns a differently-named album → never wishlist the wrong one
        dz = _FakeDeezer('Completely Different', _meta())
        assert sl.resolve_album_for_release(lambda: dz, 'Nirvana', 'Bleach') == (None, [])

    def test_match_resolves_album_with_images_and_tracks(self):
        dz = _FakeDeezer('Bleach', _meta())
        payload, tracks = sl.resolve_album_for_release(lambda: dz, 'Nirvana', 'Bleach')
        assert payload['images'][0]['url'] == 'https://cdn.deezer/x.jpg'   # real CDN art
        assert payload['name'] == 'Bleach' and payload['total_tracks'] == 2
        assert [t['name'] for t in tracks] == ['Blew', 'Floyd']

    def test_no_tracks_resolves_nothing(self):
        dz = _FakeDeezer('Bleach', _meta(items=[]))
        assert sl.resolve_album_for_release(lambda: dz, 'Nirvana', 'Bleach') == (None, [])


class TestAddReleaseEndToEnd:
    def test_wishlist_entry_carries_album_image(self, tmp_path):
        import json
        from database.music_database import MusicDatabase
        db = MusicDatabase(str(tmp_path / 'm.db'))
        dz = _FakeDeezer('Bleach', _meta())
        seams = sl.build_default_seams(database=db, get_deezer=lambda: dz, profile_id=1)
        ok = seams['add_release']({'artist': 'Nirvana', 'album': 'Bleach', 'year': '1989'},
                                  {'label_name': 'Sub Pop', 'musicbrainz_label_id': 'm1'})
        assert ok is True
        with db._get_connection() as c:
            rows = c.execute("SELECT spotify_data FROM wishlist_tracks").fetchall()
        assert rows, "expected wishlist rows"
        sd = json.loads(rows[0][0])
        # the stored entry has a browser-loadable CDN album image (not blank)
        assert sd['album']['images'][0]['url'] == 'https://cdn.deezer/x.jpg'
        assert sd['artists'][0]['name'] == 'Nirvana'

    def test_add_release_no_match_adds_nothing(self, tmp_path):
        from database.music_database import MusicDatabase
        db = MusicDatabase(str(tmp_path / 'm.db'))
        dz = _FakeDeezer('Nope', _meta())      # name won't match
        seams = sl.build_default_seams(database=db, get_deezer=lambda: dz, profile_id=1)
        ok = seams['add_release']({'artist': 'Nirvana', 'album': 'Bleach'}, {})
        assert ok is False
        with db._get_connection() as c:
            assert c.execute("SELECT COUNT(*) FROM wishlist_tracks").fetchone()[0] == 0
