import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The dashboard health strip.
 *
 * Every check rendered as a full-width chip carrying its whole detail sentence,
 * including the healthy ones. Adding a check per download source turned that
 * into a wall of green prose - which is precisely where a real warning goes to
 * hide, and it was Boulder's first reaction on seeing it: "waste of space".
 *
 * Problems keep their sentence. Healthy checks collapse to one line.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-dashboard.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

async function strip(checks: unknown[]): Promise<HTMLElement> {
  const host = document.createElement('div');
  host.setAttribute('data-vdash-health', '');
  document.body.appendChild(host);
  const preamble = `
    function esc(t) { return String(t == null ? '' : t); }
    document.querySelector = function () { return host; };
    global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const run = new Function(
    'host', 'payload', 'global',
    `${preamble}\n${extractFunction('loadHealth', JS)}\nreturn loadHealth();`,
  );
  // Awaited, not guessed at with a couple of microtask ticks: an un-awaited
  // render leaves the host empty, and a test asserting something is ABSENT then
  // passes for the wrong reason.
  await run(host, { checks }, globalThis);
  return host;
}

const OK = (id: string, label: string) => ({ id, label, status: 'ok', detail: 'all fine here' });
const BAD = (id: string, label: string) => ({ id, label, status: 'error', detail: 'it is broken' });

describe('the health strip', () => {
  it('collapses healthy checks into one line naming them', async () => {
    const host = await strip([OK('a', 'YouTube'), OK('b', 'Soulseek'), OK('c', 'Torrent')]);
    const more = host.querySelector('[data-vdash-health-more]');
    expect(more?.textContent).toContain('3 healthy');
    expect(more?.textContent).toContain('YouTube, Soulseek, Torrent');
    // ...and their detail sentences are present but hidden, not thrown away.
    const detail = host.querySelector('[data-vdash-health-detail]') as HTMLElement;
    expect(detail.hidden).toBe(true);
    expect(detail.querySelectorAll('.vdash-health-chip')).toHaveLength(3);
  });

  it('gives a problem its whole sentence, unfolded', async () => {
    const host = await strip([OK('a', 'YouTube'), BAD('b', 'Soulseek')]);
    const top = host.querySelector('.vdash-health-chip--error');
    expect(top?.textContent).toContain('Soulseek');
    expect(top?.textContent).toContain('it is broken');
    // The problem comes first, before the collapsed healthy line.
    expect(host.firstElementChild?.className).toContain('--error');
  });

  it('shows no expander at all when nothing is healthy', async () => {
    const host = await strip([BAD('b', 'Soulseek')]);
    expect(host.querySelector('[data-vdash-health-more]')).toBeNull();
  });

  it('binds its toggle once, not per render', () => {
    // loadHealth() replaces its own innerHTML on every refresh, so a listener
    // attached to the button would be discarded on the next poll.
    const body = extractFunction('loadHealth', JS);
    expect(body).not.toContain('addEventListener');
    expect(JS).toContain("document.addEventListener('click'");
    expect(JS).toContain('data-vdash-health-more');
  });

  it('styles the expander it emits', () => {
    expect(CSS).toContain('.vdash-health-more');
    expect(CSS).toContain('.vdash-health-detail');
  });
});
