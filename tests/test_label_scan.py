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


class TestResolveFailsSafe:
    def test_no_metadata_service_adds_nothing(self):
        assert sl._resolve_album_tracks(None, 'A', 'Al') == []

    def test_wrong_artist_match_rejected(self):
        class MS:
            def search_albums_via_artist(self, artist, album):
                return [{'id': 'x', 'name': album, 'artists': [{'name': 'Someone Else'}]}]
            def get_album(self, aid, include_tracks=True):
                return {'tracks': {'items': [{'name': 't'}]}}
        # name matches but artist doesn't → resolve to nothing (never misfile)
        assert sl._resolve_album_tracks(MS(), 'Real Artist', 'Al') == []

    def test_exact_match_resolves_tracks(self):
        class MS:
            def search_albums_via_artist(self, artist, album):
                return [{'id': 'x', 'name': album, 'artists': [{'name': artist}]}]
            def get_album(self, aid, include_tracks=True):
                return {'tracks': {'items': [{'name': 't1'}, {'name': 't2'}]}}
        out = sl._resolve_album_tracks(MS(), 'Real Artist', 'Al')
        assert [t['name'] for t in out] == ['t1', 't2']
