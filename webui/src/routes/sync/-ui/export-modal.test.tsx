/**
 * The export picker + the card's status span (stats-automations.js 663-724,
 * 807-819).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExportModal, ExportStatusSpan } from './export-modal';

let connected: unknown = { connected: [] };

function stubFetch(): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify(connected));
    }),
  );
  return urls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  connected = { connected: [] };
});

/** Render, then let the connection probe settle so nothing updates outside act. */
async function open(props: Partial<Parameters<typeof ExportModal>[0]> = {}) {
  const onClose = vi.fn();
  const onChoose = vi.fn();
  const onGated = vi.fn();
  render(
    <ExportModal
      name="Road Trip"
      onClose={onClose}
      onChoose={onChoose}
      onGated={onGated}
      {...props}
    />,
  );
  await act(async () => {});
  return { onClose, onChoose, onGated };
}

describe('ExportModal — the four choices', () => {
  it('renders all four destinations under the vanilla heading', async () => {
    stubFetch();
    await open();
    expect(screen.getByText('Export playlist')).toBeInTheDocument();
    // The subtitle is the vanilla's, stale service list and all (668).
    expect(screen.getByText('Road Trip → ListenBrainz')).toBeInTheDocument();
    const choices = [...document.querySelectorAll('.pl-export-choice')];
    expect(choices.map((b) => b.getAttribute('data-mode'))).toEqual([
      'push',
      'download',
      'spotify',
      'deezer',
    ]);
    expect(document.querySelector('#pl-export-modal')).not.toBeNull();
  });

  it('a live choice reports the mode and the backfill checkbox state', async () => {
    stubFetch();
    const { onChoose, onGated } = await open();
    await waitFor(() => expect(document.querySelectorAll('.pl-export-choice')).toHaveLength(4));
    fireEvent.click(document.querySelector('#pl-export-backfill')!);
    fireEvent.click(screen.getByText('Download .jspf file'));
    expect(onChoose).toHaveBeenCalledWith('download', true);
    expect(onGated).not.toHaveBeenCalled();
  });

  it('backfill defaults to OFF', async () => {
    stubFetch();
    const { onChoose } = await open();
    fireEvent.click(screen.getByText('Sync to ListenBrainz'));
    expect(onChoose).toHaveBeenCalledWith('push', false);
  });

  it('Cancel and a backdrop click close it; a click inside does not', async () => {
    stubFetch();
    const { onClose } = await open();
    fireEvent.click(document.querySelector('#pl-export-modal')!.firstChild as Element);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector('#pl-export-modal')!);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe('ExportModal — the connection probe (715-723)', () => {
  it('greys out a disconnected service and nudges instead of exporting', async () => {
    const urls = stubFetch();
    connected = { connected: ['spotify'] };
    const { onChoose, onGated } = await open();
    await waitFor(() =>
      expect(document.querySelector('[data-mode="deezer"]')).toHaveAttribute(
        'data-disconnected',
        '1',
      ),
    );
    expect(urls).toContain('/api/discover/your-albums/sources');
    expect(document.querySelector('[data-mode="spotify"]')).not.toHaveAttribute(
      'data-disconnected',
    );
    expect(screen.getByText('Not connected')).toBeInTheDocument();
    expect(screen.getByText(/set up Deezer in Settings → Connections first/)).toBeInTheDocument();

    fireEvent.click(document.querySelector('[data-mode="deezer"]')!);
    expect(onGated).toHaveBeenCalledWith('deezer');
    expect(onChoose).not.toHaveBeenCalled();
  });

  it('never gates the two ListenBrainz choices', async () => {
    stubFetch();
    connected = { connected: [] };
    const { onChoose } = await open();
    await waitFor(() =>
      expect(document.querySelector('[data-mode="spotify"]')).toHaveAttribute('data-disconnected'),
    );
    expect(document.querySelector('[data-mode="push"]')).not.toHaveAttribute('data-disconnected');
    fireEvent.click(screen.getByText('Sync to ListenBrainz'));
    expect(onChoose).toHaveBeenCalledWith('push', false);
  });

  it('a failed probe gates NOTHING — the vanilla swallows the error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    const { onChoose } = await open();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(document.querySelector('[data-mode="spotify"]')).not.toHaveAttribute(
      'data-disconnected',
    );
    fireEvent.click(screen.getByText('Sync to Spotify'));
    expect(onChoose).toHaveBeenCalledWith('spotify', false);
  });
});

describe('ExportStatusSpan', () => {
  it('paints plain text in the status colour', () => {
    render(<ExportStatusSpan status={{ text: 'Starting export…', color: '#a78bfa' }} />);
    const span = document.querySelector('.export-status-span') as HTMLElement;
    expect(span.textContent).toBe('Starting export…');
    expect(span.style.color).toBe('rgb(167, 139, 250)');
  });

  it('renders a trailing link, blue and unstyled unless underlined', () => {
    render(
      <ExportStatusSpan
        status={{
          text: 'Exported to Spotify · 3 added',
          color: '#22c55e',
          link: { url: 'https://open.spotify/p/1', label: 'open' },
        }}
      />,
    );
    const anchor = screen.getByText('open') as HTMLAnchorElement;
    expect(anchor.getAttribute('href')).toBe('https://open.spotify/p/1');
    expect(anchor.getAttribute('target')).toBe('_blank');
    expect(anchor.style.color).toBe('rgb(56, 189, 248)');
    expect(anchor.style.textDecoration).toBe('');
    expect(document.querySelector('.export-status-span')!.textContent).toBe(
      'Exported to Spotify · 3 added open',
    );
  });

  it('renders the authorize sentence with the link INSIDE it, underlined', () => {
    render(
      <ExportStatusSpan
        status={{
          text: 'Spotify needs permission to create playlists —',
          link: { url: 'https://accounts/x', label: 'authorize', underline: true },
          suffix: ', then click Export again.',
          color: '#f59e0b',
        }}
      />,
    );
    expect(document.querySelector('.export-status-span')!.textContent).toBe(
      'Spotify needs permission to create playlists — authorize, then click Export again.',
    );
    expect((screen.getByText('authorize') as HTMLElement).style.textDecoration).toBe('underline');
  });

  it('a link click does not bubble to the card behind it', () => {
    const onCard = vi.fn();
    render(
      <div onClick={onCard}>
        <ExportStatusSpan
          status={{
            text: 'Synced',
            color: '#22c55e',
            link: { url: 'https://lb/x', label: 'view' },
          }}
        />
      </div>,
    );
    fireEvent.click(screen.getByText('view'));
    expect(onCard).not.toHaveBeenCalled();
  });
});
