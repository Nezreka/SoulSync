/** Parse zone-less API timestamps as UTC, preserving explicit zones. */
export function parseServerTime(ts: string): number {
  if (/[Zz]$/.test(ts) || /[+-]\d{2}:\d{2}$/.test(ts)) return new Date(ts).getTime();
  return new Date(`${ts}Z`).getTime();
}

/** Last-resort automation label, without the music/video namespace prefix. */
export function humanizeType(type: string | null | undefined): string {
  return (
    String(type || '')
      .replace(/^(video|music)_/, '')
      .split('_')
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ') || 'Unknown'
  );
}
