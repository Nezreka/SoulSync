import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * System health, as symbols in the notification panel header.
 *
 * Health is a STATE, not an event: "slskd is unreachable" stays true until it
 * is fixed. That is why it is NOT in the notification history - reading an entry
 * there marks it done, and a dismissed warning is a warning you no longer have,
 * while the port is still shut. It sits in the panel's header instead: visible
 * whenever you open the panel, one line, detail behind a click.
 *
 * It used to be a block on the dashboard carrying every check's full sentence,
 * healthy ones included. Boulder: "waste of space right?"
 */

const JS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
const DASH = readFileSync(resolve(process.cwd(), 'static/video/video-dashboard.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

function symbols(health: unknown): HTMLElement {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    '_notifHealth',
    `${extractFunction('_notifHealthHTML', JS)}\nreturn _notifHealthHTML();`,
  ) as (h: unknown) => string;
  const host = document.createElement('div');
  host.innerHTML = fn(health);
  return host;
}

const C = (status: string, label: string) => ({ id: label, label, status, detail: 'why' });

describe('the health symbols', () => {
  it('counts each severity it actually has', () => {
    const host = symbols({ checks: [C('error', 'a'), C('warning', 'b'), C('ok', 'c'), C('ok', 'd')] });
    const text = host.textContent ?? '';
    expect(text).toContain('1');   // one error
    expect(host.querySelector('.notif-health-sym--error')?.textContent).toContain('1');
    expect(host.querySelector('.notif-health-sym--warn')?.textContent).toContain('1');
    expect(host.querySelector('.notif-health-sym--ok')?.textContent).toContain('2');
  });

  it('shows no symbol for a severity with nothing in it', () => {
    // A "0 problems" badge is noise; the tick already carries that news.
    const host = symbols({ checks: [C('ok', 'a'), C('ok', 'b')] });
    expect(host.querySelector('.notif-health-sym--error')).toBeNull();
    expect(host.querySelector('.notif-health-sym--warn')).toBeNull();
    expect(host.querySelector('.notif-health-sym--ok')?.textContent).toContain('2');
  });

  it('takes its colour from the worst thing present', () => {
    expect(symbols({ checks: [C('ok', 'a'), C('error', 'b')] })
      .querySelector('.notif-health-btn--error')).not.toBeNull();
    expect(symbols({ checks: [C('ok', 'a'), C('warning', 'b')] })
      .querySelector('.notif-health-btn--warning')).not.toBeNull();
    expect(symbols({ checks: [C('ok', 'a')] })
      .querySelector('.notif-health-btn--ok')).not.toBeNull();
  });

  it('renders nothing at all before health has loaded', () => {
    expect(symbols(null).innerHTML).toBe('');
    expect(symbols({ checks: [] }).innerHTML).toBe('');
  });

  it('opens the detail modal rather than expanding in place', () => {
    expect(symbols({ checks: [C('ok', 'a')] }).innerHTML).toContain('_openHealthModal()');
  });
});

describe('the health detail modal', () => {
  function rows(health: unknown): HTMLElement {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const fn = new Function(
      '_notifHealth',
      `${extractFunction('_healthRowsHTML', JS)}\nreturn _healthRowsHTML();`,
    ) as (h: unknown) => string;
    const host = document.createElement('div');
    host.innerHTML = fn(health);
    return host;
  }

  it('gives every check its label and its whole sentence', () => {
    const host = rows({ checks: [{ label: 'Soulseek', status: 'error', detail: 'port shut' }] });
    expect(host.querySelector('.notif-health-label')?.textContent).toBe('Soulseek');
    expect(host.querySelector('.notif-health-detail')?.textContent).toBe('port shut');
    expect(host.querySelector('.notif-health-row--error')).not.toBeNull();
  });

  it('escapes what the server sent', () => {
    const host = rows({ checks: [{ label: '<img>', status: 'ok', detail: '<script>x</script>' }] });
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('script')).toBeNull();
  });

  it('says so plainly when there is nothing to report', () => {
    expect(rows({ checks: [] }).textContent).toContain('Nothing to report');
    expect(rows(null).textContent).toContain('Nothing to report');
  });

  it('re-reads health when opened, because the panel may be stale', () => {
    const body = extractFunction('_openHealthModal', JS);
    expect(body).toContain('_seedNotifHealth()');
    expect(body).toContain('_healthRowsHTML()');
  });
});

describe('the dashboard no longer carries it', () => {
  it('has no health strip left behind', () => {
    // Dead code that looks like a feature is worse than no code.
    expect(DASH).not.toContain('loadHealth');
    expect(HTML).not.toContain('data-vdash-health');
    expect(CSS).not.toContain('.vdash-health-chip');
  });

  it('styles everything the new UI emits', () => {
    for (const cls of ['.notif-health-btn', '.notif-health-sym--error',
                       '.notif-health-row', '.notif-health-detail']) {
      expect(CSS, `${cls} is emitted but never styled`).toContain(cls);
    }
  });
});
