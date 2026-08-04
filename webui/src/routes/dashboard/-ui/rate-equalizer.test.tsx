/**
 * RateMonitorCard — artefact differential (initial = the empty vanilla card)
 * plus the equalizer behaviours: bar creation on the first frame, fill/state
 * rendering, the imperative one-shots (flash, recovered, embers, rolling
 * counter), cooldown, budget ring, and the JioSaavn removal.
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { compareTrees, extractDashArticle, parseVanilla } from './dash-artefact';
import { RateMonitorCard } from './rate-equalizer';

beforeEach(() => {
  // Run every RAF callback immediately at a far-future timestamp so the
  // rolling-number animation completes in a single step.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(performance.now() + 10_000);
    return 1;
  });
  vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window._openRateModal;
  delete window.isJiosaavnExperimentalEnabled;
  delete window._reduceEffectsActive;
});

function mountCard() {
  return render(<RateMonitorCard />);
}

function fireFrame(data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:rate-monitor', { detail: data }));
  });
}

describe('the artefact differential', () => {
  it('renders the vanilla enrichment card 1:1 in its initial state', () => {
    const vanilla = parseVanilla(
      extractDashArticle(
        '<article class="dash-card dash-card--full" id="rate-monitor-section" data-card="enrichment">',
      ),
    );
    const view = mountCard();
    compareTrees(vanilla, view.container.firstElementChild!, 'enrichment');
  });
});

describe('bar creation', () => {
  it('builds every visible service on the first frame and adds the modifier class', () => {
    const view = mountCard();
    const grid = () => view.container.querySelector('#rate-monitor-grid')!;
    expect(grid().className).toBe('rate-monitor-grid');
    expect(grid().children).toHaveLength(0);

    fireFrame({ spotify: { cpm: 10, limit: 60 } });

    expect(grid().className).toBe('rate-monitor-grid rate-monitor-grid--equalizer');
    // 11 bars — jiosaavn is gated off; dataless services render defaults.
    expect(grid().querySelectorAll('.rate-eq')).toHaveLength(11);
    expect(view.container.querySelector('#rate-eq-jiosaavn')).toBeNull();
    const deezer = view.container.querySelector('#rate-eq-deezer')!;
    expect(deezer.querySelector('.rate-eq-state')!.getAttribute('data-status')).toBe('stopped');
    expect(deezer.querySelector('.rate-eq-state-text')!.textContent).toBe('Stopped');
  });

  it('includes jiosaavn when the experimental flag is on, and removes it on the toggle event', () => {
    window.isJiosaavnExperimentalEnabled = () => true;
    const view = mountCard();
    fireFrame({ jiosaavn: { cpm: 1, limit: 60 } });
    expect(view.container.querySelectorAll('.rate-eq')).toHaveLength(12);
    expect(view.container.querySelector('#rate-eq-jiosaavn')).not.toBeNull();

    window.isJiosaavnExperimentalEnabled = () => false;
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: false } }),
      );
    });
    expect(view.container.querySelector('#rate-eq-jiosaavn')).toBeNull();
  });

  it('a removed jiosaavn bar comes back FRESH, not with stale peak state', () => {
    // The vanilla _removeJiosaavnRateGauge deletes the display state too —
    // re-enabling must not resurrect the old peak-hold marker.
    window.isJiosaavnExperimentalEnabled = () => true;
    const view = mountCard();
    fireFrame({ jiosaavn: { cpm: 30, limit: 60 } }); // peak latches at 0.5
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: false } }),
      );
    });
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: true } }),
      );
    });
    fireFrame({ jiosaavn: { cpm: 6, limit: 60 } });
    const peak = view.container.querySelector('#rate-eq-jiosaavn .rate-eq-peak')!;
    // Fresh state: peak = the live fill (0.1), nothing meaningfully above it.
    expect(peak.classList.contains('visible')).toBe(false);
  });
});

describe('bar rendering', () => {
  it('drives fill height, glow, state and the rolled count', () => {
    const view = mountCard();
    fireFrame({ spotify: { cpm: 30, limit: 60, worker: { status: 'running' } } });
    const bar = view.container.querySelector<HTMLElement>('#rate-eq-spotify')!;
    // 0 → 30 is an upward step, so the flash one-shot rides the first frame
    // too — same as the vanilla's spike detector.
    expect(bar.className).toBe('rate-eq active peak-flash');
    expect(bar.style.getPropertyValue('--eq-accent')).toBe('#1DB954');
    expect(bar.style.getPropertyValue('--eq-glow')).toBe('0.54');
    expect(bar.querySelector<HTMLElement>('.rate-eq-fill')!.style.height).toBe('50%');
    expect(bar.querySelector('.rate-eq-state')!.getAttribute('data-status')).toBe('running');
    expect(bar.querySelector('.rate-eq-state-text')!.textContent).toBe('Running');
    expect(bar.querySelector('.rate-eq-value')!.textContent).toBe('30');
    expect(bar.getAttribute('aria-label')).toBe('Spotify rate detail');
  });

  it('marks danger from the REAL ratio and restarts peak-flash on a step up', () => {
    const view = mountCard();
    fireFrame({ tidal: { cpm: 55, limit: 60 } });
    const bar = view.container.querySelector<HTMLElement>('#rate-eq-tidal')!;
    expect(bar.classList.contains('danger')).toBe(true);
    // The first frame stepped 0 → 55: the flash one-shot is live.
    expect(bar.classList.contains('peak-flash')).toBe(true);
  });

  it('runs the cooldown drain and the recovery one-shot', () => {
    const view = mountCard();
    fireFrame({ genius: { cpm: 0, limit: 60, rate_limited: true, rl_remaining: 300 } });
    const bar = view.container.querySelector<HTMLElement>('#rate-eq-genius')!;
    expect(bar.classList.contains('cooldown')).toBe(true);
    expect(bar.classList.contains('rate-limited')).toBe(true);
    expect(bar.querySelector<HTMLElement>('.rate-eq-cooldown-fill')!.style.height).toBe('100%');
    expect(bar.querySelector('.rate-eq-cooldown-time')!.textContent).toBe('5:00');

    fireFrame({ genius: { cpm: 0, limit: 60, rate_limited: true, rl_remaining: 150 } });
    expect(bar.querySelector<HTMLElement>('.rate-eq-cooldown-fill')!.style.height).toBe('50%');
    expect(bar.querySelector('.rate-eq-cooldown-time')!.textContent).toBe('2:30');

    fireFrame({ genius: { cpm: 0, limit: 60 } });
    expect(bar.classList.contains('cooldown')).toBe(false);
    expect(bar.classList.contains('recovered')).toBe(true); // the one-shot
    expect(bar.querySelector('.rate-eq-cooldown-time')!.textContent).toBe('');
  });

  it('renders the spotify budget ring, purple once bridged to Free', () => {
    const view = mountCard();
    fireFrame({
      spotify: {
        cpm: 0,
        limit: 60,
        worker: {
          using_free: true,
          daily_budget: { used: 100, limit: 100, exhausted: true },
        },
      },
    });
    const bar = view.container.querySelector<HTMLElement>('#rate-eq-spotify')!;
    expect(bar.classList.contains('has-budget')).toBe(true);
    expect(bar.style.getPropertyValue('--eq-budget-color')).toBe('#a78bfa');
    expect(bar.querySelector('.rate-eq-avatar')!.getAttribute('title')).toBe(
      'Daily budget spent — running on Spotify Free (100/100)',
    );
  });

  it('spawns embers in proportion to traffic and honours the effects switch', () => {
    const view = mountCard();
    fireFrame({ deezer: { cpm: 70, limit: 100 } });
    const track = view.container.querySelector('#rate-eq-deezer .rate-eq-track')!;
    expect(track.querySelectorAll('.rate-eq-ember')).toHaveLength(3);

    window._reduceEffectsActive = true;
    fireFrame({ amazon: { cpm: 70, limit: 100 } });
    expect(view.container.querySelectorAll('#rate-eq-amazon .rate-eq-ember')).toHaveLength(0);
  });

  it('falls back to the initial-letter glyph when a logo fails to load', () => {
    const view = mountCard();
    fireFrame({ qobuz: { cpm: 1, limit: 60 } });
    const bar = view.container.querySelector('#rate-eq-qobuz')!;
    fireEvent.error(bar.querySelector('.rate-eq-avatar-logo')!);
    const avatar = bar.querySelector('.rate-eq-avatar')!;
    expect(avatar.classList.contains('rate-eq-avatar--fallback')).toBe(true);
    expect(avatar.querySelector('.rate-eq-avatar-glyph')!.textContent).toBe('Q');
  });
});

describe('clicks', () => {
  it('opens the vanilla rate modal for the clicked service', () => {
    const openRateModal = vi.fn();
    window._openRateModal = openRateModal;
    const view = mountCard();
    fireFrame({ lastfm: { cpm: 1, limit: 60 } });
    fireEvent.click(view.container.querySelector('#rate-eq-lastfm')!);
    expect(openRateModal).toHaveBeenCalledWith('lastfm');
  });
});
