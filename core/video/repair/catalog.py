"""video library maintenance: what each finding type is, for the ui.

The music side learned this the hard way (core/repair_worker.py
FINDING_TYPE_META): when the client keeps its own list of labels and fixable
types, it drifts. Nine working music fixes had no button and two dead ends
had one. So the catalog lives here, next to the jobs, and the Tools page asks
for it (/api/video/repair/finding-types).

Every type a video job can emit has a row. A test fails if a job grows a type
this table doesn't describe.

  label        what the group is called
  verb         the button. says what will HAPPEN, not "Fix"
  blurb        one line on why you'd care, shown under the label
  destructive  approving moves, renames or deletes a file on disk
  acquires     approving starts downloads (a grab, or rows on the wishlist).
               nothing is lost, but a movie is gigabytes, so the one-click
               "fix all safe" leaves these alone too
  confirm      what the fix-all confirmation says when the generic line is wrong
"""

from __future__ import annotations

VIDEO_FINDING_TYPE_META = {
    "missing_episodes": {
        "label": "Missing Episodes", "verb": "Send to Wishlist", "acquires": True,
        "blurb": "Aired episodes of your shows with no file.",
        "confirm": "Every listed episode goes on the wishlist and the auto-downloader "
                   "takes it from there.",
    },
    "incomplete_collection": {
        "label": "Incomplete Collections", "verb": "Add to Wishlist", "acquires": True,
        "blurb": "Franchises you started but haven't finished.",
        "confirm": "The missing films go on the wishlist and the auto-downloader takes "
                   "it from there.",
    },
    "quality_upgrade": {
        "label": "Quality Upgrades", "verb": "Grab Upgrade", "acquires": True,
        "destructive": True,
        "blurb": "Files below the cutoff in your quality profile.",
        "confirm": "Each one searches for a better release and downloads it. The import "
                   "replaces the old file once the new one lands.",
    },
    "broken_file": {
        "label": "Broken Files", "verb": "Re-download", "acquires": True,
        "destructive": True,
        "blurb": "Truncated or stub files, far shorter than the title runs.",
        "confirm": "Each one searches for a replacement and downloads it. The import "
                   "replaces the broken file once the new one lands.",
    },
    "metadata_gap": {
        "label": "Metadata Gaps", "verb": "Re-enrich",
        "blurb": "Movies missing a summary, genres, art or a TMDB match.",
    },
    "duplicate_movie": {
        "label": "Duplicate Movies", "verb": None,
        "blurb": "The same movie owned more than once. Review only.",
    },
    "duplicate_episode": {
        "label": "Duplicate Episodes", "verb": None,
        "blurb": "An episode with two or more files. Review only.",
    },
    "stale_wishlist": {
        "label": "Stale Wishlist", "verb": "Remove",
        "blurb": "Wishlist rows for things you already own.",
        "confirm": "Only the wishlist rows are removed. No file is touched.",
    },
    "youtube_ghost": {
        "label": "YouTube Ghosts", "verb": "Mark Deleted",
        "blurb": "Downloaded YouTube videos whose file is gone from disk.",
        "confirm": "They're marked deleted and won't be downloaded again. Files are "
                   "never touched.",
    },
    "youtube_mass_missing": {
        "label": "Most YouTube Files Missing", "verb": "Flag Individually",
        "blurb": "Most of the YouTube ledger vanished at once. A drive is often down.",
        "confirm": "Each missing video gets its own finding to review. Nothing is "
                   "marked by this step.",
    },
    "watched_cleanup": {
        "label": "Watched Cleanup", "verb": "Clean Up", "destructive": True,
        "blurb": "Movies you've finished, taking up disk.",
        "confirm": "The files move to the recycle bin, where they stay until its "
                   "retention clears them.",
    },
    "naming_mismatch": {
        "label": "Naming", "verb": "Rename", "destructive": True,
        "blurb": "Files whose names don't match your naming templates.",
        "confirm": "Each file is renamed to its template name. Sidecars and subtitles "
                   "come along, and nothing is ever overwritten.",
    },
    "missing_subtitles": {
        "label": "Missing Subtitles", "verb": "Queue Subtitles",
        "blurb": "Videos with no subtitles in your languages.",
        "confirm": "They're queued for the subtitle worker, which fetches them within "
                   "your daily quota.",
    },
}

# What a finished fix did, for the badge on a handled row.
VIDEO_ACTION_LABELS = {
    "wishlisted": "Wishlisted", "grabbed": "Grabbed", "refreshed": "Refreshed",
    "removed": "Removed", "resolved": "Resolved", "marked_deleted": "Marked Deleted",
    "forgotten": "Forgotten", "flagged": "Flagged", "cleaned": "Cleaned",
    "renamed": "Renamed", "queued": "Queued",
}

# The family each job is filed under, in the order the families show. Same
# idea as music's JOB_CATEGORIES: one table where the whole taxonomy is
# visible, and a job missing from it lands in "Other" rather than vanishing.
VIDEO_JOB_CATEGORY_ORDER = [
    "Files & storage",
    "Quality",
    "Collection gaps",
    "Metadata",
    "Other",
]

VIDEO_JOB_CATEGORIES = {
    "broken_files": "Files & storage",
    "duplicate_movies": "Files & storage",
    "naming_conformance": "Files & storage",
    "watched_cleanup": "Files & storage",
    "youtube_ghosts": "Files & storage",
    "quality_upgrade": "Quality",
    "missing_subtitles": "Quality",
    "missing_episodes": "Collection gaps",
    "movie_collections": "Collection gaps",
    "wishlist_audit": "Collection gaps",
    "metadata_gaps": "Metadata",
}

# Jobs whose fixes move, rename or delete real files. The settings drawer
# shows its strongest warning on these.
VIDEO_JOBS_WRITING_FILES = frozenset({"naming_conformance", "watched_cleanup"})


def job_category(job_id: str) -> str:
    return VIDEO_JOB_CATEGORIES.get(job_id, "Other")


def is_safe_type(finding_type: str) -> bool:
    """safe = approving only writes SoulSync's own data. no file is touched
    and nothing is downloaded."""
    meta = VIDEO_FINDING_TYPE_META.get(finding_type) or {}
    return not meta.get("destructive") and not meta.get("acquires")
