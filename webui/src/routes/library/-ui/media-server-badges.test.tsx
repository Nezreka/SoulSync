import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ArtistSummary } from '../-library-v2.types';

import { ArtistCard, LibraryV2CanWriteContext } from './library-v2-page';
import { MediaServerRecognitionBadges } from './media-server-badges';

// #2213: recognition drawn with each server's own logo and a green tick

const artist: LibraryV2ArtistSummary = {
  id: 9,
  name: '3 Doors Down',
  image_url: null,
  genres: [],
  monitored: false,
  monitor_new_items: 'all',
  quality_profile_id: 1,
  added_at: null,
  album_count: 2,
  single_count: 1,
  track_count: 10,
  tracks_present: 8,
  tracks_missing: 2,
  total_size_bytes: 0,
  user_overrides: {},
};

function renderCard(sources: string[] | undefined) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <LibraryV2CanWriteContext.Provider value>
        <ArtistCard artist={{ ...artist, media_server_sources: sources }} onOpen={vi.fn()} />
      </LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
}

function renderBadges(sources: string[] | undefined, variant: 'card' | 'header' | 'compact') {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MediaServerRecognitionBadges sources={sources} variant={variant} />
    </QueryClientProvider>,
  );
}

describe('media-server recognition badges', () => {
  it.each([
    ['plex', 'Plex', '/static/img/brands/plex.png'],
    ['jellyfin', 'Jellyfin', '/static/img/brands/jellyfin.png'],
    ['navidrome', 'Navidrome', '/static/img/brands/navidrome.png'],
    ['music_assistant', 'Music Assistant', '/static/img/brands/music-assistant.png'],
    ['soulsync', 'SoulSync', '/static/trans2.png'],
  ])('a card recognised by %s wears its logo and a decorative tick', (source, label, logo) => {
    renderCard([source]);

    const badge = screen.getByRole('img', { name: `Recognised by ${label}` });
    expect(badge).toHaveAttribute('title', `Recognised by ${label}`);
    const img = badge.querySelector('img');
    expect(img).toHaveAttribute('src', logo);
    // the badge carries the name; the logo and the tick say nothing twice
    expect(img).toHaveAttribute('alt', '');
    expect(img?.closest('[aria-hidden="true"]')).not.toBeNull();
    const tick = badge.querySelector(':scope > svg');
    expect(tick).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByText(label)).not.toBeInTheDocument();
  });

  it('a server without a shipped logo still gets a named badge', () => {
    renderCard(['emby']);
    const badge = screen.getByRole('img', { name: 'Recognised by Emby' });
    expect(badge.querySelector('img')).toBeNull();
    expect(badge.querySelector('[data-server="emby"] svg')).not.toBeNull();
  });

  it.each([[undefined], [[]], [['', '  ']]])('no recognition, no badge (%j)', (sources) => {
    const { container } = renderCard(sources as string[] | undefined);
    expect(container.querySelector('[data-media-server-badges]')).toBeNull();
    expect(screen.queryByRole('img', { name: /Recognised by/ })).toBeNull();
  });

  it('two servers are two badges, in one group, each named', () => {
    const { container } = renderCard(['jellyfin', 'plex', 'PLEX']);
    const group = container.querySelector('[data-media-server-badges="card"]');
    const badges = screen.getAllByRole('img', { name: /^Recognised by / });
    expect(badges.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Recognised by Jellyfin',
      'Recognised by Plex',
    ]);
    for (const badge of badges) expect(group).toContainElement(badge);
  });

  it('hides a server that is no longer set up, keeps the ones that are', async () => {
    server.use(
      http.get('/api/media-servers', ({ request }) => {
        // config only: the badges never make the server probe its clients
        expect(new URL(request.url).searchParams.get('probe')).toBe('0');
        return HttpResponse.json({
          active: 'jellyfin',
          servers: [
            { name: 'plex', configured: false },
            { name: 'jellyfin', configured: true },
            { name: 'navidrome', configured: false },
          ],
        });
      }),
    );
    renderCard(['jellyfin', 'plex']);

    await waitFor(() =>
      expect(screen.queryByRole('img', { name: 'Recognised by Plex' })).toBeNull(),
    );
    expect(screen.getByRole('img', { name: 'Recognised by Jellyfin' })).toBeInTheDocument();
  });

  it('shows the recognition as before when the server list cannot load', async () => {
    let asked = false;
    server.use(
      http.get('/api/media-servers', () => {
        asked = true;
        return HttpResponse.json({ error: 'nope' }, { status: 500 });
      }),
    );
    renderCard(['plex']);
    await waitFor(() => expect(asked).toBe(true));
    expect(screen.getByRole('img', { name: 'Recognised by Plex' })).toBeInTheDocument();
  });

  it('the header pill names the server beside its logo', () => {
    renderBadges(['navidrome'], 'header');
    const pill = screen.getByRole('img', { name: 'Recognised by Navidrome' });
    expect(pill).toHaveTextContent('Navidrome');
    expect(pill.querySelector('img')).toHaveAttribute('src', '/static/img/brands/navidrome.png');
  });

  it('a table row groups the logos under one name', () => {
    renderBadges(['plex', 'music_assistant'], 'compact');
    const mark = screen.getByRole('img', { name: 'Recognised by Plex and Music Assistant' });
    expect(mark.querySelectorAll('img')).toHaveLength(2);
    expect(mark.querySelectorAll(':scope > svg')).toHaveLength(1);
  });
});

describe('media-server badges stay clear of the card controls', () => {
  const css = readFileSync(resolve(__dirname, 'media-server-badges.module.css'), 'utf8');
  const classic = readFileSync(resolve(__dirname, 'library-classic-cards.module.css'), 'utf8');
  const bulk = readFileSync(resolve(__dirname, 'library-bulk.module.css'), 'utf8');

  function rule(sheet: string, selector: string, from = 0): string {
    const start = sheet.indexOf(`${selector} {`, from);
    expect(start, `${selector} rule`).toBeGreaterThanOrEqual(0);
    return sheet.slice(start, sheet.indexOf('}', start));
  }
  const px = (block: string, prop: string) =>
    Number(new RegExp(`\\n\\s*${prop}:\\s*(-?[\\d.]+)px`).exec(block)?.[1]);

  it('the group is not inside the toggle, the checkbox or the badge column', () => {
    const { container } = renderCard(['plex', 'jellyfin']);
    const group = container.querySelector('[data-media-server-badges="card"]');
    expect(group).not.toBeNull();
    expect(group?.closest('.card-badge-container')).toBeNull();
    expect(group?.closest('button')).toBeNull();
    expect(container.querySelector('.card-badge-container [data-media-server-badges]')).toBeNull();
    for (const control of container.querySelectorAll('button, input')) {
      expect(group?.contains(control)).toBe(false);
      expect(control.contains(group)).toBe(false);
    }
  });

  it('starts right of the 30px watchlist toggle and reserves the badge column', () => {
    const monitor = rule(classic, '.monitor');
    const group = rule(css, '.cardGroup');
    const chip = rule(css, '.chip');
    // toggle: left 8 + 30 wide; the group starts after it with a gap
    expect(px(group, 'left')).toBeGreaterThanOrEqual(px(monitor, 'left') + 30 + 6);
    // same top as the toggle, same 30px height
    expect(px(group, 'top')).toBe(px(monitor, 'top'));
    expect(px(chip, 'height')).toBe(30);
    // the select box sits under the toggle (left 14): the group never starts
    // left of where it ends, so even a wrapped second row stays clear
    const check = rule(bulk, '.cardCheck');
    expect(px(group, 'left')).toBeGreaterThan(px(check, 'left') + 18);
    // right side: two 24px badge columns + 3px gap + 8px inset = 59px
    expect(group).toMatch(/max-width:\s*calc\(100% - 46px - (\d+)px\)/);
    const reserve = Number(/max-width:\s*calc\(100% - \d+px - (\d+)px\)/.exec(group)?.[1]);
    expect(reserve).toBeGreaterThanOrEqual(24 * 2 + 3 + 8);
    expect(group).toMatch(/flex-wrap:\s*wrap/);
  });

  it('on a phone the reserve follows the 18px badge column', () => {
    const phone = css.slice(css.indexOf('@media (max-width: 768px)'));
    const group = rule(phone, '.cardGroup');
    const reserve = Number(/max-width:\s*calc\(100% - \d+px - (\d+)px\)/.exec(group)?.[1]);
    // two 18px columns + 2px gap + 4px inset = 42px
    expect(reserve).toBeGreaterThanOrEqual(18 * 2 + 2 + 4);
    expect(px(group, 'left')).toBeGreaterThanOrEqual(8 + 30);
  });
});
