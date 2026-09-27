/**
 * #1301: the quick-switch modal shows the media server and the download chain,
 * it doesn't switch them. a server flip means a fresh library scan, and the
 * modal's own hybrid editor kept drifting from the one in settings (no deezer,
 * a hybrid toggle that forgot your picks). these pin what each tab offers and
 * where its one button goes.
 */

import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  closeServiceSwitchModal,
  openServiceSwitchModal,
  openServiceSwitchSettings,
  setActiveSource,
} from './service-switch';

const posted: unknown[] = [];

function mockSources(download: Record<string, unknown>, serverActive = 'plex') {
  server.use(
    http.get('/api/profiles/me/active-sources', () =>
      HttpResponse.json({
        success: true,
        editable: true,
        metadata: {
          active: 'deezer',
          effective: 'deezer',
          options: [
            { id: 'deezer', available: true },
            { id: 'itunes', available: true },
          ],
        },
        server: {
          active: serverActive,
          options: [
            { id: 'plex', available: true },
            { id: 'jellyfin', available: true },
            { id: 'navidrome', available: false },
            { id: 'soulsync', available: true },
          ],
        },
        download,
      }),
    ),
    http.post('/api/profiles/active-sources', async ({ request }) => {
      posted.push(JSON.parse(await request.text()));
      return HttpResponse.json({ success: true });
    }),
  );
}

async function openOn(tab: string) {
  openServiceSwitchModal(tab);
  await vi.waitFor(() => {
    const panel = document.getElementById('ss-panel')!;
    expect(panel.textContent).not.toContain('Loading');
  });
  return document.getElementById('ss-panel')!;
}

function setStatus(media: Record<string, unknown> | null) {
  (globalThis as unknown as { _lastStatusPayload: unknown })._lastStatusPayload = media
    ? { media_server: media }
    : null;
}

beforeEach(() => {
  posted.length = 0;
  setStatus(null);
});

afterEach(() => {
  closeServiceSwitchModal();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('server tab', () => {
  it('offers no server to click, only settings', async () => {
    mockSources({ mode: 'soulseek', chain: [{ id: 'soulseek', ready: true }] });
    const panel = await openOn('server');

    expect(panel.querySelectorAll('.ss-card')).toHaveLength(0);
    expect(panel.querySelector('[onclick*="setActiveSource"]')).toBeNull();
    const cta = panel.querySelector('.ss-cta')!;
    expect(cta.textContent).toContain('Change in Settings');
    expect(cta.getAttribute('onclick')).toBe("openServiceSwitchSettings('server')");
  });

  it('says online with the response time when status knows this server', async () => {
    setStatus({ connected: true, response_time: 41.6, type: 'plex' });
    mockSources({ mode: 'soulseek', chain: [] });
    const panel = await openOn('server');

    expect(panel.querySelector('.ss-hero-pill--ok')!.textContent).toContain('Online');
    expect(panel.textContent).toContain('answered in 42 ms');
    // other servers that are set up; navidrome isn't
    expect(panel.textContent).toContain('Jellyfin, SoulSync');
    expect(panel.textContent).not.toContain('Navidrome');
  });

  it('says offline when it is down', async () => {
    setStatus({ connected: false, response_time: 0, type: 'plex' });
    mockSources({ mode: 'soulseek', chain: [] });
    const panel = await openOn('server');
    expect(panel.querySelector('.ss-hero-pill--bad')!.textContent).toContain('Offline');
  });

  it("doesn't trust a status frame about a different server", async () => {
    setStatus({ connected: true, response_time: 10, type: 'jellyfin' });
    mockSources({ mode: 'soulseek', chain: [] });
    const panel = await openOn('server');
    expect(panel.querySelector('.ss-hero-pill--wait')!.textContent).toContain('Checking');
  });

  it('repaints when a fresh status frame arrives', async () => {
    mockSources({ mode: 'soulseek', chain: [] });
    const panel = await openOn('server');
    expect(panel.querySelector('.ss-hero-pill--wait')).not.toBeNull();

    setStatus({ connected: true, response_time: 5, type: 'plex' });
    window.dispatchEvent(new CustomEvent('ss:service-status'));
    expect(panel.querySelector('.ss-hero-pill--ok')).not.toBeNull();
  });
});

describe('download tab', () => {
  it('shows the chain as saved, deezer included, with readiness', async () => {
    mockSources({
      mode: 'hybrid',
      hybrid_order: ['deezer_dl', 'soulseek', 'amazon'],
      chain: [
        { id: 'deezer_dl', ready: true },
        { id: 'soulseek', ready: false },
        { id: 'amazon', ready: null },
      ],
    });
    const panel = await openOn('download');

    const steps = [...panel.querySelectorAll('.ss-chain-step')];
    expect(steps.map((s) => s.querySelector('.ss-chain-name')!.textContent)).toEqual([
      'Deezer',
      'Soulseek',
      'Amazon Music',
    ]);
    expect(steps[0].querySelector('.ss-chip--ok')!.textContent).toBe('Ready');
    expect(steps[1].querySelector('.ss-chip--warn')!.textContent).toBe('Needs setup');
    expect(steps[2].querySelector('.ss-chip')).toBeNull();
    expect(panel.querySelector('.ss-hero-pill')!.textContent).toContain('3 sources');
  });

  it('has no single/hybrid toggle and nothing draggable', async () => {
    mockSources({ mode: 'deezer_dl', chain: [{ id: 'deezer_dl', ready: true }] });
    const panel = await openOn('download');

    expect(panel.querySelector('.ss-seg')).toBeNull();
    expect(panel.querySelector('[draggable="true"]')).toBeNull();
    expect(panel.querySelector('.ss-hero-pill')!.textContent).toContain('Single source');
    expect(panel.querySelector('.ss-cta')!.getAttribute('onclick')).toBe(
      "openServiceSwitchSettings('download')",
    );
  });
});

describe('writes', () => {
  it('only the metadata source is ever posted', async () => {
    mockSources({ mode: 'soulseek', chain: [] });
    await setActiveSource('server', 'jellyfin');
    await setActiveSource('download', 'hybrid');
    await setActiveSource('metadata', 'itunes');
    expect(posted).toEqual([{ metadata_source: 'itunes' }]);
  });
});

describe('the way into settings', () => {
  it('opens the connections tab on the music side and lights up the server section', async () => {
    vi.useFakeTimers();
    try {
      document.body.innerHTML = `
        <div class="settings-group" id="grp"><div class="server-toggle-container"><button id="plex-toggle"></button></div></div>`;
      const calls: string[] = [];
      window.navigateToPage = (p: string) => {
        calls.push(`page:${p}`);
      };
      window.switchSettingsTab = (t: string) => {
        calls.push(`tab:${t}`);
      };
      window.switchServiceKind = (k: string) => {
        calls.push(`side:${k}`);
      };
      const grp = document.getElementById('grp')!;
      grp.scrollIntoView = vi.fn();

      openServiceSwitchSettings('server');
      vi.advanceTimersByTime(300);

      expect(calls).toEqual(['page:settings', 'tab:connections', 'side:music']);
      expect(grp.scrollIntoView).toHaveBeenCalled();
      expect(grp.classList.contains('ss-landed')).toBe(true);
      vi.advanceTimersByTime(2300);
      expect(grp.classList.contains('ss-landed')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('opens the downloads tab on the music chain', () => {
    vi.useFakeTimers();
    try {
      document.body.innerHTML = '<div id="download-chain-widget"></div>';
      const calls: string[] = [];
      window.navigateToPage = (p: string) => {
        calls.push(`page:${p}`);
      };
      window.switchSettingsTab = (t: string) => {
        calls.push(`tab:${t}`);
      };
      window.switchDownloadChain = (k: string) => {
        calls.push(`chain:${k}`);
      };
      const widget = document.getElementById('download-chain-widget')!;
      widget.scrollIntoView = vi.fn();

      openServiceSwitchSettings('download');
      vi.advanceTimersByTime(300);

      expect(calls).toEqual(['page:settings', 'tab:downloads', 'chain:music']);
      expect(widget.classList.contains('ss-landed')).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
