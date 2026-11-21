/**
 * WebVTT, read into cues the player draws itself (so subtitles look the
 * same in every browser, follow the player's own clock through seeks and
 * converted streams, and can be sized). only what subtitles use: times
 * and text; settings and styling blocks are skipped.
 */

export interface Cue {
  start: number;
  end: number;
  text: string;
}

function seconds(stamp: string): number | null {
  const m = /^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$/.exec(stamp.trim());
  if (!m) return null;
  return (
    Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4].padEnd(3, '0')) / 1000
  );
}

/** tags off (<i>, <c.yellow>, {\an8}), entities read */
function clean(line: string): string {
  return line
    .replace(/<[^>]+>/g, '')
    .replace(/\{\\[^}]*\}/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ');
}

export function parseVtt(text: string): Cue[] {
  const cues: Cue[] = [];
  const blocks = text.replace(/\r\n?/g, '\n').split(/\n{2,}/);
  for (const block of blocks) {
    const lines = block.split('\n');
    const at = lines.findIndex((l) => l.includes('-->'));
    if (at < 0) continue;
    const [a, b] = lines[at].split('-->');
    const start = seconds(a);
    const end = seconds(b.trim().split(/\s+/)[0]);
    if (start === null || end === null) continue;
    const body = lines
      .slice(at + 1)
      .map(clean)
      .join('\n')
      .trim();
    if (body) cues.push({ start, end, text: body });
  }
  return cues.sort((x, y) => x.start - y.start);
}

/** the cues showing at ``t`` (two can overlap), in order */
export function cuesAt(cues: readonly Cue[], t: number): Cue[] {
  // binary search for the first cue that could still be on
  let lo = 0;
  let hi = cues.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) lo = mid + 1;
    else hi = mid;
  }
  const out: Cue[] = [];
  for (let i = lo - 1; i >= 0 && i >= lo - 8; i--) if (cues[i].end > t) out.unshift(cues[i]);
  return out;
}
