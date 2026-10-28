"""SeadogsBooty (PUID 1026, PGID 100): playlist covers failed with permission
denied. /app/storage/playlist_covers was owned 1000:1000 and came back that way
after every recreate. Two causes, both pinned here as text since there's no
docker in the suite:

- .dockerignore excluded "Storage/*" only. linux is case-sensitive, and the
  nightly build runs straight after the test suite in the same checkout, so a
  storage/playlist_covers made by tests went into the image as uid 1000.
- the entrypoint's recursive chown is skipped whenever /app/data already
  matches PUID, and the fallback only chowns the top-level dirs, so anything
  under /app/storage kept the image's owner.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_storage_never_goes_into_the_image():
    lines = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines()]
    assert "storage/*" in lines


def test_the_entrypoint_fixes_what_is_wrong_under_storage_every_boot():
    sh = (ROOT / "entrypoint.sh").read_text()
    line = next(l for l in sh.splitlines() if l.startswith("find /app/storage"))
    assert '! -user "$PUID"' in line and '! -group "$PGID"' in line
    assert "chown soulsync:soulsync" in line
    assert line.rstrip().endswith("|| true")      # a read-only mount must not stop boot
    # it runs outside the "UIDs changed" branch, so it isn't skipped when /app/data already matches
    assert sh.index("find /app/storage") > sh.index("# Ensure all directories exist")
