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
from core.library2.legacy_mirror import _WATCHED_COLUMNS, watched_columns


def _mirrored_columns(legacy_table: str) -> set:
    """Every legacy column the resync reads for one table."""
    from core.library2.enrich import MIGRATED_SERVICES, enrichment_columns
    from core.library2.match_status import SERVICES

    from core.library2.enrich import active_scalars

    spec = next(s for s in MIRROR_SPECS.values() if s.legacy_table == legacy_table)
    columns = {field[1] for field in active_scalars(spec)}
    for _lib2_column, legacy_columns in spec.id_columns:
        columns.update(legacy_columns)
    for service, _label, id_columns in SERVICES:
        column = id_columns.get(spec.entity_type)
        if column and service not in MIGRATED_SERVICES:
            columns.add(column)
    columns.update(enrichment_columns(spec.entity_type))
    return columns


def test_every_watched_column_is_actually_mirrored():
    for legacy_table in _WATCHED_COLUMNS:
        unmirrored = set(watched_columns(legacy_table)) - _mirrored_columns(legacy_table)
        assert not unmirrored, (
            f"{legacy_table}: the trigger queues a row when {sorted(unmirrored)} "
            f"changes, but the resync copies nothing for it. The queue churns "
            f"and lib2 never changes."
        )


def test_every_mirrored_column_is_actually_watched():
    for legacy_table in _WATCHED_COLUMNS:
        watched = set(watched_columns(legacy_table))
        unwatched = _mirrored_columns(legacy_table) - watched
        assert not unwatched, (
            f"{legacy_table}: the resync copies {sorted(unwatched)}, but the "
            f"trigger does not fire on it, so nothing enqueues the row. The "
            f"field only moves on a manual Enrich — and the divergence metric "
            f"cannot see the gap either, because it audits what MIRROR_SPECS "
            f"declares."
        )


def test_the_provider_payload_is_declared_for_every_entity_type():
    """The enrichment bucket is the one part of the mirror that reads legacy
    columns without naming them in ``scalars`` or ``id_columns``, so it needs its
    own inventory for the two tests above to see it — for all three entity types,
    not just artists (docs §50.4.4.3).

    Stated as the invariant rather than as spot-checks on particular columns,
    because the columns are supposed to disappear one provider at a time: a source
    is declared exactly while it has NOT migrated. Naming ``discogs_bio`` here
    would mean this test had to be edited every time a worker moved, which is how a
    guard stops guarding.
    """
    from core.library2.enrich import (
        _ENRICHMENT_PAYLOAD, MIGRATED_SERVICES, enrichment_columns,
    )

    for entity, sources in _ENRICHMENT_PAYLOAD.items():
        declared = set(enrichment_columns(entity))
        for source, fields in sources.items():
            columns = {str(column) for column in fields.values()}
            if source in MIGRATED_SERVICES:
                # Its worker writes lib2 now. Mirroring legacy on top would push a
                # stale value over the fresh native one on the next drain.
                assert not (columns & declared), (entity, source)
            else:
                assert columns <= declared, (entity, source)


def test_the_enrichment_bucket_has_no_mirroring_left_to_do():
    """Every provider with a per-service enrichment payload now writes lib2
    directly, so this half of the mirror is finished (docs §32.3.1 stage 2).

    The mirror is not finished — the scalar columns and provider ids the remaining
    workers write are still carried across. This pins which half is done, and will
    fail the moment a payload provider is added back without its worker, which
    would silently reintroduce the stale-overwrite hazard.
    """
    from core.library2.enrich import (
        _ENRICHMENT_PAYLOAD, MIGRATED_SERVICES, enrichment_columns,
    )

    for entity, sources in _ENRICHMENT_PAYLOAD.items():
        assert set(sources) <= MIGRATED_SERVICES, entity
        assert enrichment_columns(entity) == (), entity
