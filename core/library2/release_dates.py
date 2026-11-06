"""the release date a download should carry for a library album.

lib2_albums has a full release_date and a year, and they don't always agree:
on one real library 45,804 albums had a year and no release_date, and 1,403
had a release_date from another edition. the download path names the folder
from the date it is handed ([$year] in the template), so a missing date filed
tracks in a yearless twin of the album's folder (SeadogsBooty, Nov 5). the
year is what the library shows, so it wins.
"""

from __future__ import annotations

from typing import Any


def _year_of(value: Any) -> int:
    text = str(value or "").strip()[:4]
    if len(text) == 4 and text.isdigit():
        year = int(text)
        if 1000 <= year <= 2999:
            return year
    return 0


def album_release_date(release_date: Any, year: Any) -> str:
    """release_date when it agrees with year, else the year, else ''."""
    date = str(release_date or "").strip()
    try:
        stored_year = int(year or 0)
    except (TypeError, ValueError):
        stored_year = 0
    if not (1000 <= stored_year <= 2999):
        stored_year = 0
    date_year = _year_of(date)
    if date_year and (not stored_year or date_year == stored_year):
        return date
    if stored_year:
        return str(stored_year)
    return ""
