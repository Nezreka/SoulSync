"""Every job the Operations Studio names is a job the backend registers.

The pillars and playbooks are filtered through the job list, so an id that
does not exist is dropped without a word: "Media Enrichment" ran ReplayGain
only, because `lyrics_fetcher` and `artwork_fetcher` were never job ids.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.repair_jobs import get_all_jobs
from core.video.repair import get_all_jobs as get_all_video_jobs

STUDIO = Path(__file__).resolve().parents[1] / "webui/src/routes/tools/-ui/operations-studio.tsx"


def _named_job_ids(media: str = "music") -> set:
    # music config sits above VIDEO_PILLARS, video's between it and STUDIO_CONFIG
    source = STUDIO.read_text(encoding="utf-8")
    split = source.index("export const VIDEO_PILLARS")
    end = source.index("export const STUDIO_CONFIG")
    section = source[:split] if media == "music" else source[split:end]
    ids = set()
    for block in re.findall(r"jobIds:\s*\[([^\]]*)\]", section):
        ids.update(re.findall(r"'([a-z0-9_]+)'", block))
    return ids


def test_the_studio_names_jobs():
    assert len(_named_job_ids("music")) > 10
    assert len(_named_job_ids("video")) > 5


def test_every_named_job_is_registered():
    missing = sorted(_named_job_ids("music") - set(get_all_jobs()))
    assert missing == []


def test_every_named_video_job_is_registered():
    missing = sorted(_named_job_ids("video") - set(get_all_video_jobs()))
    assert missing == []
