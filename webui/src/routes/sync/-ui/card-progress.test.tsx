/**
 * The shared card progress line — the sync writer beating the discovery one,
 * the two discovery formats, and the hidden/visible-empty distinction.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { SourcePlaylistState } from '../-sync.state';

import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
import { cardProgressLine, syncCardCounts } from './card-progress';

function stateWith(patch: Partial<SourcePlaylistState>): SourcePlaylistState {
  return { ...freshSourceState(SYNC_SOURCES.tidal, 'x'), ...patch };
}

describe('syncCardCounts (updateTidalCardSyncProgress 1172-1177)', () => {
  it('percentage is (matched+failed)/total, NOT matched/total', () => {
    expect(syncCardCounts({ total_tracks: 10, matched_tracks: 6, failed_tracks: 2 })).toEqual({
      total: 10,
      matched: 6,
      failed: 2,
      percentage: 80,
    });
  });

  it('missing counters default to 0 and rounding matches Math.round', () => {
    expect(syncCardCounts({ total_tracks: 3, matched_tracks: 1 })).toEqual({
      total: 3,
      matched: 1,
      failed: 0,
      percentage: 33,
    });
  });

  it('null whenever there is nothing to paint — the vanilla left the discovery line up (1192)', () => {
    expect(syncCardCounts(undefined)).toBeNull();
    expect(syncCardCounts({})).toBeNull();
    expect(syncCardCounts({ total_tracks: 0, matched_tracks: 5 })).toBeNull();
  });
});

describe('cardProgressLine', () => {
  it('fresh hides the element entirely', () => {
    expect(cardProgressLine(stateWith({ phase: 'fresh' }), SYNC_SOURCES.tidal)).toBeNull();
  });

  it('sync counters win over discovery counters', () => {
    render(
      <div>
        {cardProgressLine(
          stateWith({
            phase: 'syncing',
            spotifyTotal: 10,
            spotifyMatches: 4,
            lastSyncProgress: { total_tracks: 10, matched_tracks: 6, failed_tracks: 2 },
          }),
          SYNC_SOURCES.tidal,
        )}
      </div>,
    );
    expect(screen.getByText('(80%)')).toBeInTheDocument();
    expect(screen.getByText('✓ 6')).toBeInTheDocument();
    expect(screen.queryByText(/40%/)).not.toBeInTheDocument();
  });

  it('slash-text sources render the discovery line when no sync has reported', () => {
    render(
      <div>
        {cardProgressLine(
          stateWith({ phase: 'discovered', spotifyTotal: 10, spotifyMatches: 7 }),
          SYNC_SOURCES.tidal,
        )}
      </div>,
    );
    expect(screen.getByText('♪ 10 / ✓ 7 / ✗ 3 / 70%')).toBeInTheDocument();
  });

  it('check-note sources render their span pair instead (updateDeezerCardProgress)', () => {
    render(
      <div>
        {cardProgressLine(
          stateWith({ phase: 'discovered', spotifyTotal: 9, spotifyMatches: 5 }),
          SYNC_SOURCES.deezer,
        )}
      </div>,
    );
    expect(screen.getByText('✓ 5')).toBeInTheDocument();
    expect(screen.getByText('♪ 9')).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("non-fresh with nothing to report is '' — visible and empty, not hidden", () => {
    expect(cardProgressLine(stateWith({ phase: 'discovering' }), SYNC_SOURCES.tidal)).toBe('');
    expect(cardProgressLine(stateWith({ phase: 'discovering' }), SYNC_SOURCES.deezer)).toBe('');
  });
});
