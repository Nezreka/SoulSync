/**
 * The Enrichment Detail card (dash-card data-card="enrichment") — the
 * rate-monitor equalizer. The per-bar MATH lives in -dash.services.ts
 * (eqBarFrame); this component owns the vanilla's imperative halves, kept
 * imperative on purpose because they are animation one-shots, not state:
 *
 * - the rolling counter (`_animateRollingNumber`: easeOutCubic, 520ms, one RAF
 *   handle per element so two loops never fight over textContent) — the value
 *   div's TEXT is owned by the effect, never by JSX (its JSX child is the
 *   constant '0', so re-renders leave the DOM text alone)
 * - the .peak-flash / .recovered class restarts (remove → forced reflow →
 *   re-add), driven by the reducer's flashSeq/recoverSeq edge counters
 * - the ember sparks (self-removing spans, ≤6 live per bar, suppressed under
 *   window._reduceEffectsActive/_maxPerfActive)
 *
 * Like the vanilla, bars are built for every visible service on the FIRST
 * frame (services without data render their defaults), the grid gets the
 * --equalizer modifier class then too, and a JioSaavn experimental turn-off
 * removes its bar. Clicks open the vanilla _openRateModal (it appends to
 * document.body and keeps its own _rateMonitorState).
 */

import type { CSSProperties } from 'react';

import { useCallback, useEffect, useRef, useState } from 'react';

import type { EqBarView, RateServicePayload } from '../-dash.services';

import { useJiosaavnExperimentalEvent, useRateMonitorEvent } from '../-dash.events';
import {
  EQ_INITIAL,
  eqBarFrame,
  RATE_GAUGE_COLORS,
  RATE_GAUGE_LABELS,
  RATE_GAUGE_LOGOS,
  visibleRateGaugeServices,
} from '../-dash.services';

function useRateMonitor() {
  const [views, setViews] = useState<Record<string, EqBarView>>({});
  const [seen, setSeen] = useState(false);
  const [jiosaavnEnabled, setJiosaavnEnabled] = useState<boolean>(
    () => window.isJiosaavnExperimentalEnabled?.() ?? false,
  );

  useRateMonitorEvent(
    useCallback((data: Record<string, unknown>) => {
      const jio = window.isJiosaavnExperimentalEnabled?.() ?? false;
      setJiosaavnEnabled(jio);
      setSeen(true);
      const nowTs = performance.now();
      setViews((prev) => {
        const next = { ...prev };
        if (!jio) delete next.jiosaavn; // _removeJiosaavnRateGauge
        for (const svc of visibleRateGaugeServices(jio)) {
          const d = data[svc] as RateServicePayload | undefined;
          if (!d) continue;
          next[svc] = eqBarFrame(prev[svc] ?? EQ_INITIAL, d, nowTs);
        }
        return next;
      });
    }, []),
  );

  useJiosaavnExperimentalEvent(
    useCallback((frame) => {
      const enabled = frame.enabled === true;
      setJiosaavnEnabled(enabled);
      if (!enabled) {
        setViews((prev) => {
          if (!('jiosaavn' in prev)) return prev;
          const next = { ...prev };
          delete next.jiosaavn;
          return next;
        });
      }
    }, []),
  );

  return { views, seen, jiosaavnEnabled };
}

/** _animateRollingNumber, verbatim behaviour (easeOutCubic over 520ms). */
function animateRollingNumber(
  el: HTMLElement,
  handleRef: { current: number | null },
  from: number,
  to: number,
  duration = 520,
) {
  if (handleRef.current !== null) cancelAnimationFrame(handleRef.current);
  if (from === to) {
    el.textContent = String(to);
    handleRef.current = null;
    return;
  }
  const start = performance.now();
  const span = to - from;
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = String(Math.round(from + span * eased));
    if (t < 1) {
      handleRef.current = requestAnimationFrame(step);
    } else {
      handleRef.current = null;
    }
  };
  handleRef.current = requestAnimationFrame(step);
}

/** _spawnEmbers, verbatim: ≤6 live per bar, self-removing on animationend. */
function spawnEmbers(track: HTMLElement, pct: number, count: number) {
  if (track.querySelectorAll('.rate-eq-ember').length > 6) return;
  for (let i = 0; i < count; i++) {
    const e = document.createElement('span');
    e.className = 'rate-eq-ember';
    e.style.left = `${20 + Math.random() * 60}%`;
    e.style.bottom = `${Math.min(96, pct * 100)}%`;
    e.style.setProperty('--ember-drift', `${(Math.random() - 0.5) * 14}px`);
    e.style.animationDuration = `${1.1 + Math.random() * 0.7}s`;
    e.addEventListener('animationend', () => e.remove());
    track.appendChild(e);
  }
}

/** Restart a one-shot animation class: remove → forced reflow → re-add. */
function restartClass(el: HTMLElement, name: string, clearAfterMs: number) {
  el.classList.remove(name);
  void el.offsetWidth; // force a reflow so the animation restarts mid-cycle
  el.classList.add(name);
  window.setTimeout(() => el.classList.remove(name), clearAfterMs);
}

function EqBar({ svc, view }: { svc: string; view: EqBarView | null }) {
  const accent = RATE_GAUGE_COLORS[svc] || '#888';
  const label = RATE_GAUGE_LABELS[svc] || svc;
  const logoSrc = RATE_GAUGE_LOGOS[svc] || '';
  const fallbackGlyph = (label[0] || '?').toUpperCase();
  const [logoFailed, setLogoFailed] = useState(false);

  const barRef = useRef<HTMLButtonElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const valueRef = useRef<HTMLDivElement>(null);
  const rollHandle = useRef<number | null>(null);
  const displayedValue = useRef(0);

  // Rolling counter — the effect owns the value div's text.
  const rounded = view?.rounded ?? 0;
  useEffect(() => {
    const el = valueRef.current;
    if (!el) return;
    const from = displayedValue.current;
    if (rounded !== from) {
      animateRollingNumber(el, rollHandle, from, rounded);
    } else {
      el.textContent = String(rounded);
    }
    displayedValue.current = rounded;
  }, [rounded]);
  useEffect(
    () => () => {
      if (rollHandle.current !== null) cancelAnimationFrame(rollHandle.current);
    },
    [],
  );

  // Peak-flash one-shot on each upward step.
  const flashSeq = view?.flashSeq ?? 0;
  useEffect(() => {
    if (flashSeq > 0 && barRef.current) restartClass(barRef.current, 'peak-flash', 700);
  }, [flashSeq]);

  // Recovery one-shot when a ban ends.
  const recoverSeq = view?.recoverSeq ?? 0;
  useEffect(() => {
    if (recoverSeq > 0 && barRef.current) restartClass(barRef.current, 'recovered', 1300);
  }, [recoverSeq]);

  // Embers per frame, in proportion to real traffic.
  const emberCount = view?.emberCount ?? 0;
  const emberPct = view?.pct ?? 0.04;
  useEffect(() => {
    if (!emberCount || !trackRef.current) return;
    if (window._reduceEffectsActive || window._maxPerfActive) return;
    spawnEmbers(trackRef.current, emberPct, emberCount);
  });

  const classes = ['rate-eq'];
  if (view?.danger) classes.push('danger');
  if (view?.active) classes.push('active');
  if (view?.rateLimited) classes.push('rate-limited');
  if (view?.cooling) classes.push('cooldown');
  if (view?.budget) classes.push('has-budget');

  const style: CSSProperties & Record<string, string> = { '--eq-accent': accent };
  if (view) style['--eq-glow'] = String(view.glow);
  if (view?.budget) {
    style['--eq-budget'] = String(view.budget.pct);
    style['--eq-budget-color'] = view.budget.color;
  }

  return (
    <button
      type="button"
      className={classes.join(' ')}
      id={`rate-eq-${svc}`}
      data-svc={svc}
      style={style}
      aria-label={`${label} rate detail`}
      onClick={() => window._openRateModal?.(svc)}
      ref={barRef}
    >
      <div className="rate-eq-avatar-wrap">
        {logoSrc && !logoFailed ? (
          <div className="rate-eq-avatar" aria-hidden="true" title={view?.budget?.title}>
            <img
              className="rate-eq-avatar-logo"
              src={logoSrc}
              alt=""
              onError={() => setLogoFailed(true)}
            />
          </div>
        ) : (
          <div
            className="rate-eq-avatar rate-eq-avatar--fallback"
            aria-hidden="true"
            title={view?.budget?.title}
          >
            <span className="rate-eq-avatar-glyph">{fallbackGlyph}</span>
          </div>
        )}
      </div>
      <div className="rate-eq-track" ref={trackRef}>
        <div className="rate-eq-ticks"></div>
        <div className="rate-eq-fill" style={view ? { height: `${view.pct * 100}%` } : undefined}>
          <div className="rate-eq-shimmer"></div>
          <div className="rate-eq-tip"></div>
        </div>
        <div
          className={view?.peakVisible ? 'rate-eq-peak visible' : 'rate-eq-peak'}
          style={view?.peak !== undefined ? { bottom: `${view.peak * 100}%` } : undefined}
        ></div>
        <div className="rate-eq-cooldown">
          <div
            className="rate-eq-cooldown-fill"
            style={view?.cooling ? { height: `${view.cooldownHeight}%` } : undefined}
          ></div>
          <div className="rate-eq-cooldown-time">{view?.cooling ? view.cooldownTime : ''}</div>
        </div>
        <div className="rate-eq-value" ref={valueRef}>
          0
        </div>
      </div>
      <div className="rate-eq-meta">
        <span className="rate-eq-state" data-status={view?.wStatus ?? 'stopped'}>
          <span className="rate-eq-state-dot"></span>
          <span className="rate-eq-state-text">{view?.statusLabel ?? 'Stopped'}</span>
        </span>
        <span className="rate-eq-name">{label}</span>
      </div>
    </button>
  );
}

export function RateMonitorCard({ embedded = false }: { embedded?: boolean } = {}) {
  const { views, seen, jiosaavnEnabled } = useRateMonitor();
  const services = visibleRateGaugeServices(jiosaavnEnabled);

  // The grid is the SAME node either way; only the wrapper differs. Embedded
  // adds a #rate-monitor-section div (the vanilla geometry hook the article
  // used to carry); standalone keeps the exact vanilla-artefact tree, grid
  // directly under dash-card__body.
  const grid = (
    <div
      // The vanilla grid gains the --equalizer modifier on its first
      // render pass; before any frame it is the bare class from the markup.
      className={seen ? 'rate-monitor-grid rate-monitor-grid--equalizer' : 'rate-monitor-grid'}
      id="rate-monitor-grid"
    >
      {seen
        ? services.map((svc) => <EqBar key={svc} svc={svc} view={views[svc] ?? null} />)
        : null}
    </div>
  );
  if (embedded) return <div id="rate-monitor-section">{grid}</div>;
  return (
    <article className="dash-card dash-card--full" id="rate-monitor-section" data-card="enrichment">
      <header className="dash-card__head">
        <h3 className="dash-card__title">Enrichment Services</h3>
        <p className="dash-card__sub">API rate monitoring across providers.</p>
      </header>
      <div className="dash-card__body">{grid}</div>
    </article>
  );
}
