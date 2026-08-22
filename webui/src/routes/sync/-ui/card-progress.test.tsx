/**
 * The shared card progress line — the sync writer beating the discovery one,
 * the two discovery formats, and the hidden/visible-empty distinction.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { SourcePlaylistState } from '../-sync.state';

import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
import { CardCoverage, coverageBarWidth } from './card-coverage';
import { cardCoverageValue, cardProgressLine, syncCardCounts } from './card-progress';

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

  /*
   * The four writers now render ONE coverage bar. Every assertion below is on
   * the same numbers the old text asserted — only the markup moved, which is
   * the whole point of the change. `cardCoverageValue` is asserted directly
   * wherever the number matters, so a future markup change cannot quietly take
   * the arithmetic with it.
   */

  it('sync counters win over discovery counters, and keep (matched+failed)/total', () => {
    const state = stateWith({
      phase: 'syncing',
      spotifyTotal: 10,
      spotifyMatches: 4,
      lastSyncProgress: { total_tracks: 10, matched_tracks: 6, failed_tracks: 2 },
    });
    // The formula that must not drift: 80, NOT the discovery line's 40.
    expect(cardCoverageValue(state, SYNC_SOURCES.tidal)).toEqual({
      total: 10,
      matched: 6,
      failed: 2,
      percentage: 80,
    });

    render(<div>{cardProgressLine(state, SYNC_SOURCES.tidal)}</div>);
    expect(screen.getByText('6 / 10')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
    expect(screen.getByText('✗ 2')).toBeInTheDocument();
    expect(screen.queryByText(/40%/)).not.toBeInTheDocument();
  });

  it('slash-text sources render their discovery numbers when no sync has reported', () => {
    const state = stateWith({ phase: 'discovered', spotifyTotal: 10, spotifyMatches: 7 });
    expect(cardCoverageValue(state, SYNC_SOURCES.tidal)).toEqual({
      total: 10,
      matched: 7,
      failed: 3,
      percentage: 70,
    });

    render(<div>{cardProgressLine(state, SYNC_SOURCES.tidal)}</div>);
    expect(screen.getByText('7 / 10')).toBeInTheDocument();
    expect(screen.getByText('70%')).toBeInTheDocument();
    expect(screen.getByText('✗ 3')).toBeInTheDocument();
  });

  it('check-note sources still show NO failures and NO percentage', () => {
    // These writers never counted failures or printed a percent, and unifying
    // the markup must not invent either — the bar fills, the digits do not.
    const state = stateWith({ phase: 'discovered', spotifyTotal: 9, spotifyMatches: 5 });
    expect(cardCoverageValue(state, SYNC_SOURCES.deezer)).toEqual({
      total: 9,
      matched: 5,
      failed: null,
      percentage: null,
    });

    render(<div>{cardProgressLine(state, SYNC_SOURCES.deezer)}</div>);
    expect(screen.getByText('5 / 9')).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(document.querySelector('.pcc-failed')).toBeNull();
    // The bar still fills to 5/9 even with no printed percentage.
    expect(document.querySelector('.pcc-bar > i')?.getAttribute('style')).toContain('56%');
  });

  it('a slash-text source at zero still RENDERS, where the check-note twin goes empty', () => {
    // The vanilla slash-text writers have no total>0 gate — only the
    // check-note ones do (deezer 3372). The distinction is what matters and it
    // survives: tidal renders a zeroed bar, deezer renders the empty string.
    const zero = stateWith({ phase: 'discovering' });
    expect(cardCoverageValue(zero, SYNC_SOURCES.tidal)).toEqual({
      total: 0,
      matched: 0,
      failed: 0,
      percentage: 0,
    });
    expect(cardCoverageValue(zero, SYNC_SOURCES.deezer)).toBe('');
    expect(cardProgressLine(zero, SYNC_SOURCES.deezer)).toBe('');

    render(<div>{cardProgressLine(zero, SYNC_SOURCES.tidal)}</div>);
    expect(screen.getByText('0 / 0')).toBeInTheDocument();
  });

  it('the bar never exceeds its track when a source over-reports mid-crawl', () => {
    expect(coverageBarWidth({ total: 10, matched: 14, failed: 0, percentage: 140 })).toBe(100);
    expect(coverageBarWidth({ total: 10, matched: -2, failed: 0, percentage: -20 })).toBe(0);
  });

  it('fills the bar from matched/total when the source prints no percentage', () => {
    expect(coverageBarWidth({ total: 8, matched: 2, failed: null, percentage: null })).toBe(25);
    expect(coverageBarWidth({ total: 0, matched: 0, failed: null, percentage: null })).toBe(0);
  });
});

describe('CardCoverage — the shared renderer', () => {
  it('renders the bar, the count, the percentage and the failure chip', () => {
    render(<CardCoverage total={140} matched={128} failed={2} percentage={91} />);
    expect(screen.getByText('128 / 140')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByText('✗ 2')).toBeInTheDocument();
    expect(document.querySelector('.pcc-bar > i')?.getAttribute('style')).toContain('91%');
  });

  it('omits the percentage when the source never printed one', () => {
    render(<CardCoverage total={9} matched={5} failed={null} percentage={null} />);
    expect(document.querySelector('.pcc-pct')).toBeNull();
  });

  it('omits the failure chip at zero — it is only shown when it is actionable', () => {
    render(<CardCoverage total={10} matched={10} failed={0} percentage={100} />);
    expect(document.querySelector('.pcc-failed')).toBeNull();
    expect(document.querySelector('.pcc-bar--has-failures')).toBeNull();
  });

  it('tints the bar when there ARE failures, so it reads before the digits do', () => {
    render(<CardCoverage total={10} matched={7} failed={3} percentage={100} />);
    expect(document.querySelector('.pcc-bar--has-failures')).not.toBeNull();
  });

  it('exposes progressbar semantics for screen readers', () => {
    render(<CardCoverage total={10} matched={5} failed={0} percentage={50} />);
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuenow')).toBe('50');
    expect(bar.getAttribute('aria-valuemin')).toBe('0');
    expect(bar.getAttribute('aria-valuemax')).toBe('100');
  });
});
