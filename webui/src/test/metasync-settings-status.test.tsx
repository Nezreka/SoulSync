import { render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MetaSyncSection } from '@/features/settings/tabs/connections-metasync';

/**
 * Settings > MetaSync says whether this install is counted.
 *
 * A MetaSync node starts in dry run: it builds the weekly report and sends
 * nothing, so the network doesn't count the install. The settings page
 * showed "connected" and the pool, and never that nothing was being sent,
 * nor how far the network was from charting on its own.
 *
 * The section is React (SETTINGS_PORT.md step 10d): this runs it, with the
 * node's answers stubbed, and reads the status lines it draws.
 */

async function detailFor(status: Record<string, unknown>, nodeUrl = 'http://node:8790') {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      const data =
        url === '/api/metasync/status'
          ? { success: true, status }
          : { success: true, node_url: nodeUrl, local_key_set: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  render(<MetaSyncSection opened={1} />);
  const detail = document.getElementById('metasync-status-detail')!;
  await waitFor(() =>
    expect(document.getElementById('metasync-status')!.textContent).toMatch(/^Step 1: connected/),
  );
  return detail.innerHTML;
}

afterEach(() => vi.unstubAllGlobals());

const pool = { name: 'SIN', url: 'https://pool', binding_status: 'bound' };

describe('Settings > MetaSync: is this install counted', () => {
  it('a dry-run node with a report waiting says it is not counted yet', async () => {
    const html = await detailFor({
      version: '0.2.0',
      pools: [pool],
      dry_run: true,
      unsent_report: { week: '2026-W43', signal_count: 37, pools: 1 },
    });
    expect(html.replace(/<!-- -->/g, '')).toContain(
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
