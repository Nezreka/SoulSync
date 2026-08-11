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
    """Every legacy column the resync reads for one table.

    Includes a migrated service's columns. They are mirrored backfill-only rather
    than dropped (``enrich.handed_over``), so the resync still reads them and the
    trigger still has to watch them.
    """
    from core.library2.enrich import enrichment_columns
    from core.library2.match_status import SERVICES

    spec = next(s for s in MIRROR_SPECS.values() if s.legacy_table == legacy_table)
    columns = {field[1] for field in spec.scalars}
    for _lib2_column, legacy_columns in spec.id_columns:
        columns.update(legacy_columns)
    for _service, _label, id_columns in SERVICES:
        column = id_columns.get(spec.entity_type)
        if column:
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

    A migrated service stays in the inventory: what changes on migration is the
    direction the value may travel (backfill only), not whether the mirror touches
    the column at all, and the trigger/audit pair has to keep seeing it either way.
    """
    from core.library2.enrich import _ENRICHMENT_PAYLOAD, enrichment_columns

    for entity, sources in _ENRICHMENT_PAYLOAD.items():
        declared = set(enrichment_columns(entity))
        for source, fields in sources.items():
            columns = {str(column) for column in fields.values()}
            assert columns <= declared, (entity, source)


def test_a_migrated_service_is_mirrored_backfill_only():
    """The handover, stated as the invariant rather than per provider.

    Both halves matter and they pull against each other. Overwrite semantics for a
    field its own worker now writes would let the drain push a stale legacy value
    over the fresh native one. Dropping the field instead would lose a legacy value
    lib2 never received on a row the ledger already calls matched, which no
    mechanism would ever carry across afterwards.
    """
    from core.library2.enrich import (
        MIGRATED_SERVICES, active_scalars, handover_scalars,
    )

    for spec in MIRROR_SPECS.values():
        overwritten = {field[1] for field in active_scalars(spec)}
        backfilled = {field[1] for field in handover_scalars(spec)}
        assert not (overwritten & backfilled), spec.entity_type
        assert overwritten | backfilled == {field[1] for field in spec.scalars}, (
            spec.entity_type)
        for service in MIGRATED_SERVICES:
            assert not [c for c in overwritten if c.startswith(f"{service}_")], (
                spec.entity_type, service)


def test_every_provider_the_mirror_knows_has_migrated():
    """The stage-2 finish line for the mirror's provider half (docs §32.3.1).

    Every source in ``match_status.SERVICES`` now writes lib2 directly, so the mirror
    carries provider ids and payloads backfill-only — it fills a gap and never
    overwrites. What it still carries with authority is the shared prose and artwork
    the media-server scan and the importer write, which is a different pipeline.

    This fails if a provider is added to SERVICES without its worker, which would
    silently reintroduce the stale-overwrite hazard for that provider.
    """
    from core.library2.enrich import MIGRATED_SERVICES
    from core.library2.match_status import SERVICES

    unmigrated = {service for service, _label, _ids in SERVICES} - MIGRATED_SERVICES
    assert not unmigrated, sorted(unmigrated)
