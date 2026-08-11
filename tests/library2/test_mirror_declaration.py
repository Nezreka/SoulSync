"""The trigger and the resync must agree on which columns matter.

The mirror has two halves that are declared in different files: the ``AFTER
UPDATE`` trigger's watched column list (``legacy_mirror._WATCHED_COLUMNS``)
decides *when* a row is queued, and ``enrich.MIRROR_SPECS`` decides *what* is
then copied. Either half can be extended alone, and both failure modes are
silent:

- watched but not mirrored — every worker write queues a row and the drain
  copies nothing, so the queue churns and lib2 never changes;
- mirrored but not watched — the resync would copy the field, but nothing ever
  enqueues the row, so it only moves on a manual Enrich.

The second one is worse, because the divergence audit cannot see it either: the
audit compares what ``MIRROR_SPECS`` declares, so a field missing from the
declaration is invisible to the metric as well. That is how
``artists.lastfm_playcount`` was being dropped.
"""

from __future__ import annotations

from core.library2.enrich import MIRROR_SPECS
from core.library2.legacy_mirror import _WATCHED_COLUMNS


def _mirrored_columns(legacy_table: str) -> set:
    """Every legacy column the resync reads for one table."""
    from core.library2.enrich import _ARTIST_ENRICHMENT_COLUMNS
    from core.library2.match_status import SERVICES

    spec = next(s for s in MIRROR_SPECS.values() if s.legacy_table == legacy_table)
    columns = {field[1] for field in spec.scalars}
    for _lib2_column, legacy_columns in spec.id_columns:
        columns.update(legacy_columns)
    for _service, _label, id_columns in SERVICES:
        column = id_columns.get(spec.entity_type)
        if column:
            columns.add(column)
    if spec.entity_type == "artist":
        columns.update(_ARTIST_ENRICHMENT_COLUMNS)
    return columns


def test_every_watched_column_is_actually_mirrored():
    for legacy_table, watched in _WATCHED_COLUMNS.items():
        unmirrored = set(watched) - _mirrored_columns(legacy_table)
        assert not unmirrored, (
            f"{legacy_table}: the trigger queues a row when {sorted(unmirrored)} "
            f"changes, but the resync copies nothing for it. The queue churns "
            f"and lib2 never changes."
        )


def test_every_mirrored_column_is_actually_watched():
    for legacy_table in _WATCHED_COLUMNS:
        watched = set(_WATCHED_COLUMNS[legacy_table])
        unwatched = _mirrored_columns(legacy_table) - watched
        assert not unwatched, (
            f"{legacy_table}: the resync copies {sorted(unwatched)}, but the "
            f"trigger does not fire on it, so nothing enqueues the row. The "
            f"field only moves on a manual Enrich — and the divergence metric "
            f"cannot see the gap either, because it audits what MIRROR_SPECS "
            f"declares."
        )


def test_the_lastfm_payload_is_declared_in_one_place():
    """The artist enrichment bundle is the one part of the mirror that reads
    legacy columns without naming them in ``scalars`` or ``id_columns``, so it
    needs its own inventory for the two tests above to see it."""
    from core.library2.enrich import _ARTIST_ENRICHMENT_COLUMNS

    assert "lastfm_playcount" in _ARTIST_ENRICHMENT_COLUMNS
    assert "lastfm_listeners" in _ARTIST_ENRICHMENT_COLUMNS
