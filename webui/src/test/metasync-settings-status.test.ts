import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Settings > MetaSync says whether this install is counted.
 *
 * A MetaSync node starts in dry run: it builds the weekly report and sends
 * nothing, so the network doesn't count the install. The settings page
 * showed "connected" and the pool, and never that nothing was being sent,
 * nor how far the network was from charting on its own.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');

async function detailFor(status: Record<string, unknown>, nodeUrl = 'http://node:8790') {
  document.body.innerHTML = `<input id="metasync-node-url" value="${nodeUrl}">`;
  let detail = '';
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const refresh = new Function(
    '__status',
    '__out',
    `const _MS_BINDING_TEXT = { bound: 'Approved' };
     async function _msApi() { return { success: true, status: __status }; }
     function _msStatus() {}
     function _msDetail(html) { __out(html); }
     function _msEscape(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }
     ${extractFunction('refreshMetaSyncStatus', SRC)}
     return refreshMetaSyncStatus;`,
  )(status, (html: string) => {
    detail = html;
  }) as () => Promise<void>;
  await refresh();
  return detail;
}

const pool = { name: 'SIN', url: 'https://pool', binding_status: 'bound' };

describe('Settings > MetaSync: is this install counted', () => {
  it('a dry-run node with a report waiting says it is not counted yet', async () => {
    const html = await detailFor({
      version: '0.2.0',
      pools: [pool],
      dry_run: true,
      unsent_report: { week: '2026-W43', signal_count: 37, pools: 1 },
    });
    expect(html).toContain(
      'Step 3: dry run is on. Your report for 2026-W43 is ready (37 anonymous',
    );
    expect(html).toContain("isn't counted yet");
    expect(html).toContain('href="http://node:8790"');
  });

  it('a sending node says so', async () => {
    const html = await detailFor({ version: '0.2.0', pools: [pool], dry_run: false });
    expect(html).toContain('Step 3: sending.');
  });

  it('shows the network size and the threshold', async () => {
    const html = await detailFor({
      version: '0.2.0',
      pools: [pool],
      dry_run: false,
      network: { installs: 12, publish_threshold: 15, week: '2026-W43' },
    });
    expect(html).toContain('The network: 12 installs reported 2026-W43.');
    expect(html).toContain('start at 15');
  });

  it('an older node without the fields shows neither line', async () => {
    const html = await detailFor({ version: '0.1.0', pools: [pool] });
    expect(html).not.toContain('Step 3');
    expect(html).not.toContain('The network');
  });

  it('never links a node url that is not http(s)', async () => {
    const html = await detailFor(
      { version: '0.2.0', pools: [pool], dry_run: true },
      'javascript:alert(1)',
    );
    expect(html).not.toContain('href=');
  });
});
