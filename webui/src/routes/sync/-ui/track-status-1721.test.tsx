/**
 * #1721 (kevin2xk): 157 tracks synced as 103 and nothing said where the other
 * 54 went. the card now says how many need a match, and the detail modal says
 * where every track stands, a chip on each row that isn't in the library.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { needsMatchLabel, trackStatusChip, trackStatusSegments } from '../-sync.mirrored';
import { MirroredDetailModal } from './mirrored-detail-modal';
import { PlaylistCard } from './playlist-card';

const NOW = Date.UTC(2026, 9, 16, 12, 0, 0);

describe('status helpers', () => {
  it('a healthy or unsynced row carries no chip', () => {
    expect(trackStatusChip('in_library')).toBeNull();
    expect(trackStatusChip('unchecked')).toBeNull();
    expect(trackStatusChip(undefined)).toBeNull();
    expect(trackStatusChip('needs_match')).toBe('Needs a match');
    expect(trackStatusChip('wishlisted')).toBe('Wishlisted');
    expect(trackStatusChip('missing')).toBe('Not downloaded');
  });

  it('the strip leaves empty statuses out and keeps a fixed order', () => {
    const segs = trackStatusSegments({
      missing: 3,
      in_library: 103,
      needs_match: 1,
      wishlisted: 0,
    });
    expect(segs.map((s) => `${s.count} ${s.label}`)).toEqual([
      '103 in library',
      '1 needs a match',
      '3 not downloaded',
    ]);
    expect(trackStatusSegments({ needs_match: 42 })[0].label).toBe('need a match');
    expect(trackStatusSegments(undefined)).toEqual([]);
  });

  it('the card link only exists when something needs a match', () => {
    expect(needsMatchLabel({ needs_match_count: 42 })).toBe('42 need a match');
    expect(needsMatchLabel({ needs_match_count: 1 })).toBe('1 needs a match');
    expect(needsMatchLabel({ needs_match_count: 0 })).toBeNull();
    expect(needsMatchLabel({})).toBeNull();
  });
});

function renderCard(over: Partial<MirroredPlaylistRow>, extra = {}) {
  const onOpen = vi.fn();
  const onNeedsMatch = vi.fn();
  render(
    <PlaylistCard
      row={{ id: 29, name: 'Reading Soundtrack', source: 'spotify_public', ...over }}
      name="Reading Soundtrack"
      when="Mirrored 2h ago"
      schedule="Every day"
      onOpen={onOpen}
      onMore={vi.fn()}
      onNeedsMatch={onNeedsMatch}
      {...extra}
    />,
  );
  return { onOpen, onNeedsMatch };
}

describe('the card', () => {
  it('says how many need a match and opens the pool, not the card', () => {
    const { onOpen, onNeedsMatch } = renderCard({ needs_match_count: 42 });
    fireEvent.click(screen.getByRole('button', { name: '42 need a match' }));
    expect(onNeedsMatch).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a card with nothing to fix shows no link', () => {
    renderCard({ needs_match_count: 0 });
    expect(document.querySelector('.pl-card-attn')).toBeNull();
  });

  it('live narration replaces the link while a run is going', () => {
    renderCard({ needs_match_count: 42 }, { status: <span>discovering 40%</span> });
    expect(document.querySelector('.pl-card-attn')).toBeNull();
  });
});

const tracks = [
  { position: 1, track_name: 'Quiet Pages', artist_name: 'Evren Calder', status: 'in_library' },
  { position: 2, track_name: 'Lantern Hours', artist_name: 'Martin Rapide', status: 'needs_match' },
  { position: 3, track_name: 'Chapter Eleven', artist_name: 'Tomas Heredia', status: 'wishlisted' },
  { position: 4, track_name: 'Dog-Eared', artist_name: 'Ryan Oakes', status: 'missing' },
] as const;

function renderModal(on = {}) {
  const onFindMatches = vi.fn();
  render(
    <MirroredDetailModal
      playlistId={29}
      data={{
        name: 'Reading Soundtrack',
        source: 'spotify_public',
        tracks: tracks.map((t) => ({ ...t })),
        status_counts: { in_library: 1, needs_match: 1, wishlisted: 1, missing: 1 },
      }}
      now={NOW}
      onClose={vi.fn()}
      onDelete={vi.fn()}
      onEditSource={vi.fn()}
      onRunPipeline={vi.fn()}
      onDiscover={vi.fn()}
      onRefresh={vi.fn()}
      onFindMatches={onFindMatches}
      {...on}
    />,
  );
  return { onFindMatches };
}

const rowTitles = () =>
  Array.from(document.querySelectorAll('.mm-row .mm-row-title')).map((e) => e.textContent);

describe('the detail modal', () => {
  it('chips only the rows that are not in the library', () => {
    renderModal();
    const chips = Array.from(document.querySelectorAll('.mm-row-st')).map((e) => [
      e.getAttribute('data-st'),
      e.textContent,
    ]);
    expect(chips).toEqual([
      ['needs_match', 'Needs a match'],
      ['wishlisted', 'Wishlisted'],
      ['missing', 'Not downloaded'],
    ]);
  });

  it('a part of the status line filters the list, and again clears it', () => {
    renderModal();
    const seg = screen.getByRole('button', { name: /needs a match/ });
    fireEvent.click(seg);
    expect(rowTitles()).toEqual(['Lantern Hours']);
    expect(seg.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(seg);
    expect(rowTitles()).toHaveLength(4);
  });

  it('Find matches hands off to the pool', () => {
    const { onFindMatches } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Find matches' }));
    expect(onFindMatches).toHaveBeenCalledTimes(1);
  });

  it('no status line when the backend sent none', () => {
    render(
      <MirroredDetailModal
        playlistId={3}
        data={{ name: 'Old', source: 'spotify', tracks: [{ track_name: 'x' }] }}
        now={NOW}
        onClose={vi.fn()}
        onDelete={vi.fn()}
        onEditSource={vi.fn()}
        onRunPipeline={vi.fn()}
        onDiscover={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(document.querySelector('.mm-status')).toBeNull();
  });
});
