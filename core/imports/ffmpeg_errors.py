"""Extract the actionable failure reason from ffmpeg's stderr.

ffmpeg prints its multi-line version/build banner to stderr on EVERY run — the
``configuration:`` line alone is routinely 1KB+ — and always writes the real
error at the END. So surfacing the START of stderr (the old ``stderr[:200]``)
showed only the banner and hid the actual reason: every failure looked identical
and unactionable (issue #995: "conversion fails ... without any error logs
shown"). This pulls out the error line(s) instead, so a failed FLAC->lossy
conversion reports e.g. "Unknown encoder 'libopus' ... Encoder not found" (a
ffmpeg build without libopus) rather than the version banner.

Pure/stdlib-only so it's unit-testable without invoking ffmpeg.
"""

from __future__ import annotations

# Substrings (lower-cased match) that mark a line as an actual error/diagnostic
# worth surfacing, as opposed to banner / input-dump / progress noise.
_ERROR_MARKERS = (
    'error', 'unknown encoder', 'invalid', 'failed', 'no such',
    'permission denied', 'not found', 'cannot', 'could not', 'unable',
    'no space', 'not supported', 'does not contain', 'conversion failed',
)


def _is_banner(line: str) -> bool:
    """True for the version/build banner lines ffmpeg prints on every run — they
    carry no failure information and must never be the surfaced error."""
    low = line.lower()
    if low.startswith(('ffmpeg version', 'built with', 'configuration:')):
        return True
    # Library-version lines, e.g. "libavutil  59.  8.100 / 59.  8.100".
    if low.startswith('lib') and '/' in line and any(c.isdigit() for c in line):
        return True
    return False


def summarize_ffmpeg_error(stderr, max_len: int = 300) -> str:
    """Return the meaningful tail of an ffmpeg stderr dump.

    Prefers lines that look like errors (skipping the version banner, build
    ``configuration:``, the input/stream dump and progress spam). Falls back to
    the last few non-banner lines when no error-shaped line is found. Never
    returns the leading version banner.
    """
    if not stderr:
        return 'no ffmpeg output (check that ffmpeg is installed and on PATH)'

    lines = [ln.strip() for ln in str(stderr).splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return 'no ffmpeg output'

    non_banner = [ln for ln in lines if not _is_banner(ln)]
    error_lines = [ln for ln in non_banner if any(m in ln.lower() for m in _ERROR_MARKERS)]
    picked = (error_lines or non_banner or lines)[-4:]

    # De-dupe while preserving order (ffmpeg repeats some lines).
    seen: set = set()
    out = []
    for ln in picked:
        if ln not in seen:
            out.append(ln)
            seen.add(ln)

    msg = ' | '.join(out)
    if len(msg) > max_len:
        msg = msg[:max_len - 1].rstrip() + '…'
    return msg


__all__ = ['summarize_ffmpeg_error']
