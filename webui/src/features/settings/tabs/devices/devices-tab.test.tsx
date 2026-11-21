/**
 * Settings > Devices (PAIRING_SPEC.md 7): admins only, the paired devices
 * with an unprofiled one shown apart, the codes waiting, adding a device
 * shows the code and its QR, unpairing asks first, and the remote address
 * and reachability check say what happened.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../../nav';
import { createSettingsStore } from '../../settings-store';
import { clock, deviceLine, DevicesTab } from './devices-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

const SERVER = {
  success: true,
  lookup_id: 'ae0396a668c3' + 'f'.repeat(52),
  fingerprint: 'ae0396a668c3',
  public_key: 'x',
  addresses: [
    { kind: 'local', url: 'http://192.168.1.5:8008' },
    { kind: 'remote', url: 'http://music.example.com' },
  ],
  remote_url: 'http://music.example.com',
  custom_urls: [],
  warnings: ['http://music.example.com is plain http: put it behind https.'],
  probe: null,
  guidance: [{ id: 'tailscale', title: 'Tailscale', body: 'One private network.' }],
};

const DEVICES = [
  {
    id: 1,
    name: 'Car',
    platform: 'Android',
    app: 'Player',
    kind: 'music',
    profile_id: 2,
    profile_name: 'Mags',
    created_at: '2026-11-20 10:00:00',
    last_seen_at: '2026-11-20 10:00:00',
    last_ip: '203.0.113.7',
    state: 'live',
    pick_seconds_left: null,
  },
  {
    id: 2,
    name: 'Kitchen speaker',
    platform: '',
    app: '',
    kind: 'music',
    profile_id: null,
    profile_name: null,
    created_at: '2026-11-20 10:00:00',
    last_seen_at: null,
    last_ip: null,
    state: 'unprofiled',
    pick_seconds_left: 600,
  },
];

const MUSIC_APPS = {
  success: true,
  profiles: [
    { id: 1, name: 'Admin' },
    { id: 3, name: 'Robin' },
  ],
  addresses: SERVER.addresses,
  bridge: [
    { id: 'plex', name: 'Plex', on: false, active: true },
    { id: 'jellyfin', name: 'Jellyfin', on: false, active: false },
  ],
  transcode_format: 'mp3',
  formats: ['mp3', 'opus'],
  ffmpeg: true,
};

let calls: Array<{ url: string; method: string; body?: unknown }>;

beforeEach(() => {
  calls = [];
  shell.getCurrentProfileContext = () => ({ profileId: 1, isAdmin: true });
  shell.showToast = vi.fn();
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      let data: unknown = { success: true };
      if (url === '/api/devices' && method === 'GET') data = { success: true, devices: DEVICES };
      else if (url === '/api/devices/codes' && method === 'GET')
        data = {
          success: true,
          codes: [
            { id: 9, note: null, created_at: '', expires_at: 0, seconds_left: 300, wrong_tries: 2 },
          ],
        };
      else if (url === '/api/devices/codes' && method === 'POST')
        data = {
          success: true,
          id: 10,
          code: '7KQ4-M2XD-9FHA-T3VB',
          code_hash: 'h',
          expires_at: 0,
          seconds_left: 600,
          payload: { fingerprint: 'ae0396a668c3' },
          payload_text: '{"code":"7KQ4-M2XD-9FHA-T3VB"}',
          warnings: [],
        };
      else if (url === '/api/devices/server') data = SERVER;
      else if (url === '/api/devices/reachability' && method === 'POST')
        data = {
          success: true,
          result: { ok: false, code: 'timeout', words: 'Nothing answered in time.' },
          probe: {
            nonce: 'n',
            path: '/api/devices/reachability/n',
            url: 'http://music.example.com/api/devices/reachability/n',
            seconds_left: 600,
            hits: [],
          },
          guidance: SERVER.guidance,
        };
      else if (url === '/api/devices/reachability') data = { success: true, probe: null };
      else if (url === '/api/profiles/invites') data = { success: true, invites: [] };
      else if (url === '/api/devices/music-apps' && method === 'GET') data = MUSIC_APPS;
      else if (url === '/api/devices/music-apps' && method === 'POST')
        data = {
          success: true,
          device_id: 5,
          name: 'Phone',
          server: 'http://music.example.com',
          addresses: SERVER.addresses,
          username: 'Robin',
          password: 'Abc123Def456Ghi789Jk',
          api_key: 'sdt_secret',
          qr_text: '{"type":"soulsync-music-app"}',
        };
      else if (url === '/api/devices/music-apps/settings')
        data = { ...MUSIC_APPS, bridge: [{ ...MUSIC_APPS.bridge[0], on: true }] };
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      } as Response);
    }),
  );
});

afterEach(() => {
  nav.reset();
  vi.unstubAllGlobals();
  for (const k of ['getCurrentProfileContext', 'showToast', 'showConfirmDialog']) delete shell[k];
});

const tab = (opened = 1) => {
  nav.go('devices');
  return render(
    <DevicesTab store={createSettingsStore()} part="page" opened={opened} active={true} />,
  );
};

describe('Settings > Devices', () => {
  it('draws nothing for a member', () => {
    shell.getCurrentProfileContext = () => ({ profileId: 2, isAdmin: false });
    const { container } = tab();
    expect(container.innerHTML).toBe('');
    expect(calls).toEqual([]);
  });

  it('asks nothing until the tab is opened', () => {
    tab(0);
    expect(calls).toEqual([]);
  });

  it('lists devices, with one still picking shown apart', async () => {
    const { findByText, getByText } = tab();
    await findByText('Car');
    expect(getByText('Mags')).toBeTruthy();
    expect(getByText(/No profile picked yet/)).toBeTruthy();
    expect(getByText(/runs out in [45]:[0-9]{2} · 2 wrong tries/)).toBeTruthy();
  });

  it('shows the server, its addresses and the plain http warning', async () => {
    const { findByText } = tab();
    await findByText('http://192.168.1.5:8008');
    // once, on the remote address row it is about
    const warned = [...document.querySelectorAll('[data-row]')].filter((r) =>
      /plain http/.test(r.textContent || ''),
    );
    expect(warned).toHaveLength(1);
    expect(warned[0].textContent).toContain('Remote address');
    expect(document.body.textContent).toContain('ae03 96a6 68c3');
  });

  it('adding a device shows the code and its QR', async () => {
    const { findByText, getByText, getByRole } = tab();
    await findByText('Car');
    await act(async () => {
      fireEvent.click(getByText('Add a device'));
    });
    expect(getByText('7KQ4-M2XD-9FHA-T3VB')).toBeTruthy();
    expect(getByRole('img', { name: 'Pairing QR code' })).toBeTruthy();
    expect(calls.some((c) => c.url === '/api/devices/codes' && c.method === 'POST')).toBe(true);
  });

  it('unpairing asks first, then unpairs', async () => {
    const { findByText, getAllByText } = tab();
    await findByText('Car');
    await act(async () => {
      fireEvent.click(getAllByText('Unpair')[0]);
    });
    expect(shell.showConfirmDialog).toHaveBeenCalled();
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/devices/1' && c.method === 'DELETE')).toBe(true),
    );
  });

  it('renames a device from its row', async () => {
    const { findByText, getAllByText, getByLabelText, getByText } = tab();
    await findByText('Car');
    fireEvent.click(getAllByText('Rename')[0]);
    fireEvent.change(getByLabelText('New name for Car'), { target: { value: 'Car stereo' } });
    await act(async () => {
      fireEvent.click(getByText('Save'));
    });
    const patch = calls.find((c) => c.url === '/api/devices/1' && c.method === 'PATCH');
    expect(patch?.body).toEqual({ name: 'Car stereo' });
  });

  it('is hidden while another tab is open', async () => {
    const { container, findByText } = tab();
    await findByText('Car');
    act(() => nav.go('people'));
    expect((container.querySelector('[data-stg-new="devices"]') as HTMLElement).hidden).toBe(true);
  });

  it('saves the remote address and says what the reachability check found', async () => {
    const { findByText, getByText, getByLabelText } = tab();
    await findByText('Car');
    fireEvent.change(getByLabelText(/Remote address/), {
      target: { value: 'https://music.example.com' },
    });
    await act(async () => {
      fireEvent.click(getByText('Save addresses'));
    });
    const put = calls.find((c) => c.url === '/api/devices/addresses');
    expect(put?.body).toEqual({ remote_url: 'https://music.example.com', custom_urls: [] });
    await act(async () => {
      fireEvent.click(getByText('Check reachability'));
    });
    expect(getByText(/Nothing answered in time/)).toBeTruthy();
    expect(getByText(/Turn wifi off on your phone/)).toBeTruthy();
    expect(getByText(/One private network/)).toBeTruthy();
  });
});

describe('the words', () => {
  it('counts down as m:ss', () => {
    expect(clock(600)).toBe('10:00');
    expect(clock(61.9)).toBe('1:01');
    expect(clock(-3)).toBe('0:00');
  });

  it('says what a device is, when and where it was seen', () => {
    const now = Date.parse('2026-11-20T10:05:00Z');
    expect(deviceLine(DEVICES[0] as never, now)).toBe(
      'Player on Android · seen 5 min ago from 203.0.113.7',
    );
    expect(deviceLine(DEVICES[1] as never, now)).toBe('');
  });

  it('sets up a music app for a profile and shows its sign-in once', async () => {
    const { findByLabelText, getByLabelText, getByText, getByRole, queryByLabelText } = tab();
    const pick = await findByLabelText('Whose music');
    fireEvent.change(pick, { target: { value: '3' } });
    fireEvent.change(getByLabelText('Name'), { target: { value: 'Phone' } });
    await act(async () => {
      fireEvent.click(getByText('Set it up'));
    });
    const post = calls.find((c) => c.url === '/api/devices/music-apps' && c.method === 'POST');
    expect(post?.body).toEqual({ profile_id: 3, name: 'Phone' });
    expect(getByText('Abc123Def456Ghi789Jk')).toBeTruthy();
    expect(getByText('sdt_secret')).toBeTruthy();
    expect(getByRole('img', { name: 'Music app sign-in QR code' })).toBeTruthy();
    // the devices list is fetched again so the new app shows up
    expect(
      calls.filter((c) => c.url === '/api/devices' && c.method === 'GET').length,
    ).toBeGreaterThan(1);
    await act(async () => {
      fireEvent.click(getByText('Done'));
    });
    expect(queryByLabelText('Music app sign-in')).toBeNull();
  });

  it('the media server switch shows for the active server and saves itself', async () => {
    const { findByLabelText, queryByLabelText } = tab();
    const plex = await findByLabelText("Stream files SoulSync can't read through Plex");
    expect(queryByLabelText("Stream files SoulSync can't read through Jellyfin")).toBeNull();
    await act(async () => {
      fireEvent.click(plex);
    });
    const put = calls.find((c) => c.url === '/api/devices/music-apps/settings');
    expect(put?.body).toEqual({ bridge: { plex: true } });
  });
});
