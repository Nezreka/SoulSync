"""The eight catalogue jobs that declare ``data_basis = 'lib2'`` must mean it.

``register_job`` enforces that a declaration exists, never that the code honours it:
``genre_cleanup`` and ``comma_artist_splitter`` once claimed 'lib2' while scanning
``artists``/``albums``/``tracks``, which meant seeing roughly 5% of the catalogue
(T-11). The fix at the time was a subclass per job in
``core.repair_jobs.native_p3`` that overrode every legacy projection, and the guard
was "the registered class comes from that module".

That guard was a proxy. What actually matters is that the code holds no legacy SQL,
and now that the native implementations have been folded back into the jobs
themselves — one implementation per identity, no rollback fork — the proxy has
nothing left to point at. Asserted directly here instead, with the same counter the
legacy-usage ratchet uses.

This is strictly stronger than the module check: a native subclass that forgot one
projection would have passed the old test and fails this one.
"""

from __future__ import annotations

import pathlib

from core.repair_jobs import JOB_DATA_BASIS, get_all_jobs
from tests.library2.legacy_usage import count_legacy_usage

# The identities whose subject boundary is the Library-v2 catalogue.
CATALOGUE_JOBS = {
    'track_number_repair',
    'acoustid_scanner',
    'album_tag_consistency',
    'metadata_gap_filler',
    'missing_cover_art',
    'live_commentary_cleaner',
    'genre_cleanup',
    'comma_artist_splitter',
}


def test_each_one_declares_lib2():
    for job_id in CATALOGUE_JOBS:
        assert JOB_DATA_BASIS[job_id] == 'lib2', job_id


def test_none_of_their_modules_holds_legacy_sql():
    jobs = get_all_jobs()
    offenders = {}
    for job_id in sorted(CATALOGUE_JOBS):
        module = jobs[job_id].__module__
        path = pathlib.Path(module.replace('.', '/') + '.py')
        usage = count_legacy_usage(path.read_text())
        if (usage.reads, usage.writes) != (0, 0):
            offenders[job_id] = (module, usage.reads, usage.writes)

    assert not offenders, (
        f"these jobs declare data_basis='lib2' but their module still reads or "
        f"writes the legacy catalogue: {offenders}. A job that scans "
        f"artists/albums/tracks sees only the rows that still have a legacy twin."
    )


def test_the_rollback_fork_is_gone():
    """One implementation per identity.

    While ``native_p3`` existed, every one of these jobs had two versions of its
    scan: the native one that ran and a legacy projection kept for a rollback that
    is no longer on the table. Two versions of the same scan is how they drift —
    and the dead one is the one nobody notices breaking.
    """
    jobs = get_all_jobs()

    assert not [job_id for job_id in CATALOGUE_JOBS
                if jobs[job_id].__module__ == 'core.repair_jobs.native_p3']
    assert not pathlib.Path('core/repair_jobs/native_p3.py').exists()
