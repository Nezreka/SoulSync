"""Every job the Operations Studio names is a job the backend registers.

The pillars and playbooks are filtered through the job list, so an id that
does not exist is dropped without a word: "Media Enrichment" ran ReplayGain
only, because `lyrics_fetcher` and `artwork_fetcher` were never job ids.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.repair_jobs import get_all_jobs

STUDIO = Path(__file__).resolve().parents[1] / "webui/src/routes/tools/-ui/operations-studio.tsx"


def _named_job_ids() -> set:
    source = STUDIO.read_text(encoding="utf-8")
    ids = set()
    for block in re.findall(r"jobIds:\s*\[([^\]]*)\]", source):
        ids.update(re.findall(r"'([a-z0-9_]+)'", block))
    return ids


def test_the_studio_names_jobs():
    assert len(_named_job_ids()) > 10


def test_every_named_job_is_registered():
    missing = sorted(_named_job_ids() - set(get_all_jobs()))
    assert missing == []
