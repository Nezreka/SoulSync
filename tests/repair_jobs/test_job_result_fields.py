"""Repair jobs may only report through fields JobResult actually has.

``JobResult`` is a plain dataclass, so ``result.fixed += 1`` does not raise —
it silently creates an attribute nobody reads. The worker sums
``result.auto_fixed`` into its stats and logs it, so a job using the wrong name
does its work and then reports zero, which is worse than failing: it looks
like the job found nothing to do.

That happened to ``native_enrichment_sweep`` (iss32-E02). A static check is the
right shape here — a behavioural test would have to run every job.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

import pytest

from core.repair_jobs.base import JobResult

_JOBS_DIR = pathlib.Path(__file__).resolve().parents[2] / "core" / "repair_jobs"
_ALLOWED = {f.name for f in dataclasses.fields(JobResult)}
_ASSIGNMENT = re.compile(r"\bresult\.([a-z_][a-z0-9_]*)\s*(?:=|\+=|-=)")


@pytest.mark.parametrize(
    "path", sorted(p for p in _JOBS_DIR.glob("*.py") if p.name != "__init__.py"),
    ids=lambda p: p.name)
def test_job_only_writes_real_job_result_fields(path):
    used = set(_ASSIGNMENT.findall(path.read_text()))
    unknown = used - _ALLOWED
    assert not unknown, (
        f"{path.name} writes JobResult field(s) that do not exist: "
        f"{sorted(unknown)}. Known fields: {sorted(_ALLOWED)}. "
        f"A dataclass accepts the assignment silently and the worker then "
        f"reports nothing."
    )
