"""Which drive each copy of a title lives on — and whether the copies are the same.

Boulder's library spans eleven mount roots. When SoulSync cannot resolve the copy
it already owns (the stored path is the media server's view of a drive SoulSync
has no mapping for), an upgrade files a SECOND copy in the template location
instead of replacing the first. Nothing is corrupted; you simply end up with the
old copy on the old drive and no way to find it from the app.

The live shape, from 120,805 scanned files:

    6,838 titles hold more than one file
      3,429 of those are the SAME resolution   <- fork-shaped
      3,409 are DIFFERENT resolutions          <- often a deliberate 4K + 1080p pair
    4,963 titles have copies on more than one mount root

That second figure is why this module refuses to talk about "reclaimable space".
Half of the multi-file titles are quality pairs somebody kept on purpose, and from
the database alone a deliberate pair is indistinguishable from a fork. So the job
built on this reports, ranks and explains — it never proposes a deletion.

What it DOES add over "this title has two files": which drive each copy is on, and
whether they look like the same encode. That is the pair of facts you need to find
the superseded original, and neither was on screen anywhere.

Pure: no DB, no filesystem. The caller supplies the rows.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional

# A Windows/UNC path (\\host\share\…) and a POSIX mount (/mnt/easystore3/…) name
# the same drive in different dialects; the library stores whichever view the
# scanning media server reported.
_UNC = re.compile(r"^\\\\([^\\]+)\\([^\\]+)")


def mount_root(path: Any) -> Optional[str]:
    """The drive-ish prefix of a stored path, or None.

    POSIX ``/mnt/easystore3/TV/...`` → ``/mnt/easystore3``; UNC
    ``\\\\192.168.86.36\\plex_20tb_2_share\\PLEX\\...`` → ``\\\\192.168.86.36\\plex_20tb_2_share``;
    ``D:\\Media\\...`` → ``D:``. Two roots being different does not prove two
    physical drives — an SMB share and its POSIX mount are the same disk seen
    twice — which is why the caller SHOWS the root rather than counting on it."""
    raw = str(path or "").strip()
    if not raw:
        return None
    unc = _UNC.match(raw)
    if unc:
        return "\\\\%s\\%s" % (unc.group(1), unc.group(2))
    if re.match(r"^[A-Za-z]:", raw):
        return raw[:2].upper()
    norm = raw.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return None
    if norm.startswith("/"):
        # /mnt/easystore3/... keeps two segments; /media/Movies/... likewise.
        return "/" + "/".join(parts[:2]) if len(parts) >= 2 else "/" + parts[0]
    return parts[0]


def _gb(n: Any) -> float:
    try:
        return round(float(n or 0) / 1073741824.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _res(v: Any) -> str:
    return str(v or "").strip().lower() or "?"


def describe_copies(files: Iterable[Any]) -> Dict[str, Any]:
    """Summarise one title's copies for a finding.

    ``files`` = rows carrying ``relative_path``/``size_bytes``/``resolution``.
    Returns ``{copies, roots, spans_drives, same_resolution, largest_index,
    smaller_gb}``.

    ``same_resolution`` is the honest discriminator this exists for: copies at
    ONE resolution are what an upgrade-that-forked leaves behind, while a mix of
    resolutions is usually somebody keeping a 4K and a 1080p on purpose. Neither
    is proof, so both are reported and labelled rather than acted on."""
    rows: List[Dict[str, Any]] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        rows.append({
            "file_id": f.get("file_id") or f.get("id"),
            "path": f.get("relative_path") or f.get("path") or "",
            "root": mount_root(f.get("relative_path") or f.get("path")),
            "size_bytes": f.get("size_bytes") or 0,
            "size_gb": _gb(f.get("size_bytes")),
            "resolution": f.get("resolution"),
        })
    if not rows:
        return {"copies": [], "roots": [], "spans_drives": False,
                "same_resolution": False, "largest_index": None, "smaller_gb": 0.0}
    roots = []
    for r in rows:
        if r["root"] and r["root"] not in roots:
            roots.append(r["root"])
    sizes = [int(r["size_bytes"] or 0) for r in rows]
    largest = sizes.index(max(sizes)) if sizes else None
    smaller = sum(s for i, s in enumerate(sizes) if i != largest)
    return {
        "copies": rows,
        "roots": roots,
        "spans_drives": len(roots) > 1,
        "same_resolution": len({_res(r["resolution"]) for r in rows}) == 1,
        "largest_index": largest,
        "smaller_gb": _gb(smaller),
    }


def summary_line(summary: Any) -> str:
    """One line for the finding. Names the drives, because "two copies" is not
    actionable and "one on /mnt/easystore3, one on /mnt/plex_20tb" is."""
    if not isinstance(summary, dict) or not summary.get("copies"):
        return "no copies"
    copies = summary["copies"]
    bits = []
    for c in copies:
        where = c.get("root") or "?"
        bits.append("%s %.1f GB on %s" % (str(c.get("resolution") or "?"), c.get("size_gb") or 0, where))
    line = " · ".join(bits)
    if summary.get("spans_drives") and summary.get("same_resolution"):
        # The shape an upgrade-that-forked leaves: identical quality, two drives.
        line += " — same quality on different drives"
    return line


def severity_for(summary: Any) -> str:
    """Same quality across two drives is the one worth looking at; a mixed-quality
    pair on one drive is almost certainly deliberate."""
    if not isinstance(summary, dict) or not summary.get("copies"):
        return "info"
    if summary.get("spans_drives") and summary.get("same_resolution"):
        return "warning"
    return "info"


__all__ = ["mount_root", "describe_copies", "summary_line", "severity_for"]
