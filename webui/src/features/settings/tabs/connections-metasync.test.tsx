/**
 * Connections > MetaSync (SETTINGS_PORT.md, step 10d): the markup it
 * replaced, the load on each opening, pairing, the handoff, saving, the
 * status readout and disconnect, all through /api/metasync.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { detectTried, MetaSyncSection, metasyncSentence } from './connections-metasync';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: unknown }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  detectTried.done = false;
  calls = [];
  answers = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      const data = answers[`${method} ${url}`] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
  delete shell.showConfirmDialog;
});

const el = (id: string) => document.getElementById(id) as HTMLInputElement;
const button = (label: string) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent === label)!;
const sent = (method: string, url: string) =>
  calls.filter((c) => c.method === method && c.url === url);

describe('MetaSync', () => {
  it('asks nothing before it opens, and reads back loading', () => {
    render(<MetaSyncSection opened={0} />);
    expect(calls).toEqual([]);
    expect(document.body.textContent).toContain('Loading MetaSync.');
    // the steps are cards, Disconnect is in a danger zone
    for (const title of [
      'Step 1: connect SoulSync and MetaSync',
      'Step 2: join a pool',
      'Status',
      'Danger zone',
    ])
      expect(document.querySelector(`[data-card="${title}"]`), title).toBeTruthy();
    expect(document.querySelector('[data-card="Danger zone"]')!.textContent).toContain(
      'Disconnect',
    );
  });

  it('loads each time Connections opens: the fields, the connection and the status lines', async () => {
    answers['GET /api/metasync'] = {
      success: true,
      node_url: 'http://ms:8790',
      pool_url: 'https://pool',
      pool_key_set: true,
      local_key_set: true,
      node_fingerprint: 'ab:cd',
    };
    answers['GET /api/metasync/status'] = {
      success: true,
      status: {
        version: '1.2',
        soulsync_connected: true,
        node_fingerprint: 'ab:cd',
        pools: [{ name: 'SIN', url: 'https://pool', binding_status: 'bound' }],
        dry_run: true,
        unsent_report: { week: '2026-W46', signal_count: 12 },
        network: { installs: 3, publish_threshold: 5, week: '2026-W45' },
      },
    };
    const { rerender } = render(<MetaSyncSection opened={1} />);
    await waitFor(() =>
      expect(el('metasync-status').textContent).toBe(
        'Step 1: connected to MetaSync 1.2, which is reading this SoulSync',
      ),
    );
    expect(el('metasync-node-url').value).toBe('http://ms:8790');
    expect(el('metasync-pool-url').value).toBe('https://pool');
    expect(el('metasync-pool-key').placeholder).toBe('Saved, type a new key only to replace it');
    expect(el('metasync-connect-state').textContent).toBe('Connected, MetaSync ID ab:cd');
    const detail = el('metasync-status-detail');
    expect(detail.querySelectorAll('br')).toHaveLength(3);
    expect(detail.textContent).toContain('Pool SIN (https://pool): Bound to the pool');
    expect(detail.textContent).toContain(
      'Your report for 2026-W46 is ready (12 anonymous album signals)',
    );
    expect(detail.querySelector('a')!.getAttribute('href')).toBe('http://ms:8790');
    expect(detail.textContent).toContain(
      'The network: 3 installs reported 2026-W45. Charts from the network start at 5 installs per album',
    );
    rerender(<MetaSyncSection opened={2} />);
    await waitFor(() => expect(sent('GET', '/api/metasync')).toHaveLength(2));
  });

  it('finds MetaSync on its own once, while nothing is set', async () => {
    answers['GET /api/metasync'] = { success: true, local_key_set: false };
    answers['POST /api/metasync/detect'] = {
      success: true,
      found: true,
      node_url: 'http://localhost:8790',
    };
    render(<MetaSyncSection opened={1} />);
    await waitFor(() =>
      expect(el('metasync-status').textContent).toBe(
        'Found MetaSync on this machine at http://localhost:8790. Press Connect to pair with it.',
      ),
    );
    expect(el('metasync-node-url').value).toBe('http://localhost:8790');
  });

  it('a second visit in the same page load does not look again', async () => {
    answers['GET /api/metasync'] = { success: true, local_key_set: false };
    answers['POST /api/metasync/detect'] = { success: true, found: false };
    const first = render(<MetaSyncSection opened={1} />);
    await waitFor(() => expect(sent('POST', '/api/metasync/detect')).toHaveLength(1));
    first.unmount();
    render(<MetaSyncSection opened={1} />);
    await waitFor(() => expect(sent('GET', '/api/metasync')).toHaveLength(2));
    await act(async () => {});
    expect(sent('POST', '/api/metasync/detect')).toHaveLength(1);
  });

  it('pairs: the code and ID show, finishing hides them', async () => {
    answers['POST /api/metasync/pair/request'] = {
      success: true,
      pairing_code: '1234',
      node_fingerprint: 'ff',
    };
    render(<MetaSyncSection opened={0} />);
    fireEvent.change(el('metasync-node-url'), { target: { value: ' http://ms ' } });
    fireEvent.click(button('Connect'));
    await waitFor(() => expect(el('metasync-pair-code').textContent).toBe('1234'));
    expect(el('metasync-pair-box').style.display).toBe('');
    expect(el('metasync-pair-fingerprint').textContent).toBe('ff');
    expect(sent('POST', '/api/metasync/pair/request')[0].body).toEqual({ node_url: 'http://ms' });
    fireEvent.click(button('I approved it, finish connecting'));
    await waitFor(() => expect(el('metasync-pair-box').style.display).toBe('none'));
    expect(shell.showToast).toHaveBeenCalledWith('MetaSync connected', 'success');
  });

  it('Save sends the fields, and the keys only when typed; the handoff says how it went', async () => {
    // what the server holds once saved: the next load puts it back in the fields
    answers['GET /api/metasync'] = { success: true, pool_url: 'https://pool', local_key_set: true };
    render(<MetaSyncSection opened={0} />);
    fireEvent.change(el('metasync-pool-url'), { target: { value: 'https://pool ' } });
    fireEvent.click(button('Save'));
    await waitFor(() => expect(sent('PUT', '/api/metasync/settings')).toHaveLength(1));
    expect(sent('PUT', '/api/metasync/settings')[0].body).toEqual({
      node_url: '',
      pool_url: 'https://pool',
    });
    fireEvent.change(el('metasync-pool-key'), { target: { value: ' pk ' } });
    answers['POST /api/metasync/handoff'] = { success: true, exchange_status: 'binding_pending' };
    fireEvent.click(button('Send pool key to MetaSync'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Pool key handed over, waiting for approval in the portal',
        'warning',
      ),
    );
    expect(sent('POST', '/api/metasync/handoff')[0].body).toEqual({
      pool_url: 'https://pool',
      pool_key: 'pk',
    });
  });

  it('Disconnect asks first', async () => {
    answers['POST /api/metasync/disconnect'] = { success: true, node_notified: false };
    render(<MetaSyncSection opened={0} />);
    fireEvent.click(button('Disconnect'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Disconnected here. MetaSync could not be reached, so clear its pool key from its dashboard too',
        'success',
      ),
    );
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Disconnect MetaSync?', destructive: true }),
    );
    await act(() => Promise.resolve());
  });

  it('the hero reads the connection, the pools and the reports back', async () => {
    answers['GET /api/metasync'] = { success: true, local_key_set: true };
    answers['GET /api/metasync/status'] = {
      success: true,
      status: {
        version: '1.2',
        soulsync_connected: true,
        pools: [{ name: 'SIN', binding_status: 'pending_approval' }],
        dry_run: false,
      },
    };
    render(<MetaSyncSection opened={1} />);
    await waitFor(() => expect(document.body.textContent).toContain('It is in SIN'));
    expect(document.body.textContent).toContain('SIN: waiting for approval in the portal');
    expect(document.body.textContent).toContain('It sends one anonymous report a week.');
  });
});

describe('metasyncSentence', () => {
  const words = (f: Parameters<typeof metasyncSentence>[0]) =>
    metasyncSentence(f)
      .map((p) => p.text)
      .join('');
  it('says loading, then not connected, then what it is in', () => {
    expect(words({ linked: null, pools: [] })).toBe('Loading MetaSync.');
    expect(words({ linked: false, pools: [] })).toMatch(/^SoulSync is not connected to MetaSync\./);
    expect(words({ linked: true, version: '2', pools: [] })).toBe(
      'SoulSync is connected to MetaSync 2. It has not joined a pool, so nothing is shared.',
    );
    expect(
      words({
        linked: true,
        reading: false,
        pools: [{ name: 'SIN', binding_status: 'bound' }],
        dryRun: true,
      }),
    ).toBe(
      'SoulSync is connected to MetaSync, which cannot read this SoulSync yet. It is in SIN. Dry run is on, so nothing is sent yet.',
    );
  });
  it('marks what needs a look', () => {
    const parts = metasyncSentence({
      linked: true,
      reading: false,
      pools: [{ name: 'SIN', binding_status: 'auth_failed' }],
    });
    expect(parts.filter((p) => p.kind === 'warn').map((p) => p.text)).toEqual([
      'cannot read this SoulSync yet',
      'SIN: pool authentication failed',
    ]);
  });
});
