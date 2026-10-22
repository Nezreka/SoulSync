import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useCallback, useMemo, useRef, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { mirroredPipelineStateWriter } from '../-sync.mirrored';
import { SYNC_SOURCES } from '../-sync.sources';
import { useMirroredPipeline } from '../-sync.use-pipeline';
import { useSourceVertical } from '../-sync.use-vertical';
import { MirroredTab } from './mirrored-tab';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string, method: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responder(url, method)));
    }),
  );
}

const SAMPLE_PLAYLISTS = [
  {
    id: 1,
    name: 'Normal Mirror',
    source: 'spotify',
    source_playlist_id: 'sp1',
    is_request_list: false,
    track_count: 10,
    discovered_count: 10,
    updated_at: '2026-01-15T10:00:00Z',
  },
  {
    id: 2,
    name: 'User Playlist',
    source: 'soulsync',
    source_playlist_id: 'ss1',
    is_request_list: false,
    track_count: 5,
    discovered_count: 5,
    updated_at: '2026-01-15T11:00:00Z',
  },
  {
    id: 3,
    name: 'Friend Request Box',
    source: 'spotify',
    source_playlist_id: 'sp2',
    is_request_list: true,
    auto_remove_fulfilled: true,
    upstream_playlist_id: 'sp2',
    track_count: 15,
    discovered_count: 15,
    updated_at: '2026-01-15T12:00:00Z',
  },
];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
});

function Harness(props: Partial<Parameters<typeof MirroredTab>[0]> = {}) {
  const vertical = useSourceVertical(SYNC_SOURCES.mirrored);
  const [openId, setOpenId] = useState<string | null>(null);
  const reloadRef = useRef<(() => void) | undefined>(undefined);
  const onState = useMemo(() => mirroredPipelineStateWriter(vertical), [vertical]);
  const reload = useCallback(() => reloadRef.current?.(), []);
  const pipeline = useMirroredPipeline({ onState, reload });
  return (
    <div>
      <MirroredTab
        vertical={vertical}
        onOpen={setOpenId}
        pipeline={pipeline}
        registerReload={(fn) => {
          reloadRef.current = fn;
        }}
        {...props}
      />
      <span data-testid="open-id">{openId ?? 'none'}</span>
    </div>
  );
}

describe('MirroredTab — Request Lists Scope and Partitioning', () => {
  it('mirrored tab (default scope) excludes request lists and user playlists', async () => {
    stubFetch();
    responder = (url) => {
      if (url.includes('/api/mirrored-playlists')) return SAMPLE_PLAYLISTS;
      if (url.includes('/api/automations')) return [];
      return {};
    };

    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByText('Normal Mirror')).toBeDefined();
    });
    expect(screen.queryByText('User Playlist')).toBeNull();
    expect(screen.queryByText('Friend Request Box')).toBeNull();
  });

  it('user scope excludes request lists and normal mirrors', async () => {
    stubFetch();
    responder = (url) => {
      if (url.includes('/api/mirrored-playlists')) return SAMPLE_PLAYLISTS;
      if (url.includes('/api/automations')) return [];
      return {};
    };

    render(<Harness scope="user" />);
    await waitFor(() => {
      expect(screen.getByText('User Playlist')).toBeDefined();
    });
    expect(screen.queryByText('Normal Mirror')).toBeNull();
    expect(screen.queryByText('Friend Request Box')).toBeNull();
  });

  it('requests scope only shows request lists', async () => {
    stubFetch();
    responder = (url) => {
      if (url.includes('/api/mirrored-playlists')) return SAMPLE_PLAYLISTS;
      if (url.includes('/api/automations')) return [];
      return {};
    };

    render(<Harness scope="requests" />);
    await waitFor(() => {
      expect(screen.getByText('Friend Request Box')).toBeDefined();
    });
    expect(screen.queryByText('Normal Mirror')).toBeNull();
    expect(screen.queryByText('User Playlist')).toBeNull();

    // Check header
    expect(screen.getByText('Request lists')).toBeDefined();
    // Check request badge on card
    const badge = document.querySelector('.pl-card-badge--request');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain('📥');
  });

  it('requests scope offers auto-remove fulfilled toggle in menu for Spotify request lists', async () => {
    stubFetch();
    responder = (url) => {
      if (url.includes('/api/mirrored-playlists')) return SAMPLE_PLAYLISTS;
      if (url.includes('/api/automations')) return [];
      return {};
    };

    render(<Harness scope="requests" />);
    await waitFor(() => {
      expect(screen.getByText('Friend Request Box')).toBeDefined();
    });

    const more = document.querySelector('.pl-card-more') as HTMLElement;
    fireEvent.click(more);

    const items = [...document.querySelectorAll('.pl-menu-item')].map((el) =>
      el.textContent?.trim(),
    );
    expect(items.some((t) => t?.includes('Auto-remove fulfilled'))).toBe(true);
    expect(items.some((t) => t?.includes('Convert'))).toBe(false);
  });

  it('normal mirror offers Copy to Request Lists in menu', async () => {
    stubFetch();
    responder = (url) => {
      if (url.includes('/api/mirrored-playlists')) return SAMPLE_PLAYLISTS;
      if (url.includes('/api/automations')) return [];
      return {};
    };

    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByText('Normal Mirror')).toBeDefined();
    });

    const more = document.querySelector('.pl-card-more') as HTMLElement;
    fireEvent.click(more);

    const items = [...document.querySelectorAll('.pl-menu-item')].map((el) =>
      el.textContent?.trim(),
    );
    expect(items.some((t) => t?.includes('Copy to Request Lists'))).toBe(true);
    expect(items.some((t) => t?.includes('Convert'))).toBe(false);
  });

  // auto-remove deletes songs from the user's real spotify playlist, so
  // turning it on asks first, and a no leaves the setting alone
  async function openAutoRemoveItem(autoRemove: boolean, confirmAnswer: boolean) {
    stubFetch();
    const lists = SAMPLE_PLAYLISTS.map((p) =>
      p.id === 3 ? { ...p, auto_remove_fulfilled: autoRemove } : p,
    );
    responder = (url) => {
      if (url.includes('/api/mirrored-playlists') && !url.includes('request-list-settings'))
        return lists;
      if (url.includes('/api/automations')) return [];
      return { success: true };
    };
    const confirm = vi.fn(async () => confirmAnswer);
    (window as { showConfirmDialog?: unknown }).showConfirmDialog = confirm;
    window.showToast = vi.fn();

    render(<Harness scope="requests" />);
    await waitFor(() => {
      expect(screen.getByText('Friend Request Box')).toBeDefined();
    });
    fireEvent.click(document.querySelector('.pl-card-more') as HTMLElement);
    const item = [...document.querySelectorAll('.pl-menu-item')].find((el) =>
      el.textContent?.includes('Auto-remove fulfilled'),
    ) as HTMLElement;
    fireEvent.click(item);
    return confirm;
  }

  const patches = () => calls.filter((c) => c.url.includes('request-list-settings'));

  it('turning auto-remove on asks first and a no sends nothing', async () => {
    const confirm = await openAutoRemoveItem(false, false);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(patches()).toHaveLength(0);
    delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
  });

  it('turning auto-remove on after a yes saves it', async () => {
    const confirm = await openAutoRemoveItem(false, true);
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(patches()[0]!.body).toEqual({ auto_remove_fulfilled: true });
    delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
  });

  it('turning auto-remove off does not ask', async () => {
    const confirm = await openAutoRemoveItem(true, false);
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(confirm).not.toHaveBeenCalled();
    expect(patches()[0]!.body).toEqual({ auto_remove_fulfilled: false });
    delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
  });
});
