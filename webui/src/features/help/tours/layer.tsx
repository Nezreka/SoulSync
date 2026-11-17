/**
 * What a running tour looks like: a dimmed page with a hole over the step's
 * target (four panels around it, the way helper.js's tours did it, so no
 * stacking context on the page can poke through), a ring on the target, and
 * the step card beside it. The target stays clickable through the hole.
 *
 * Keys: → next, ← back, Esc ends. They are taken in the capture phase so the
 * player's own arrows don't seek underneath, and left alone while typing.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import type { TourStep } from './tours';

import { Icon } from '../icons';
import { copy, end, nextStep, prevStep, relocate, stepPosition, useTourStore } from './engine';
import { findAnchor, isShown } from './navigate';
import { holeFor, placeCard, type Box, type Placement } from './place';
import s from './tour.module.css';

/** a shown step's target may blink out while its page re-renders */
const LOST_GRACE_MS = 1200;
const WATCH_MS = 200;
/** finding takes a moment on every page change: no card for the quick ones */
const FINDING_CARD_MS = 350;

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable;
}

function viewport() {
  return { width: window.innerWidth, height: window.innerHeight };
}

function sameBox(a: Box | null, b: Box | null): boolean {
  if (!a || !b) return a === b;
  return a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height;
}

function boxOf(el: HTMLElement): Box {
  const r = el.getBoundingClientRect();
  return {
    top: Math.round(r.top),
    left: Math.round(r.left),
    width: Math.round(r.width),
    height: Math.round(r.height),
  };
}

function reducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

export function onTourKey(event: KeyboardEvent): void {
  const { tour, phase } = useTourStore.getState();
  if (!tour || event.defaultPrevented) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const inCard = !!(event.target as HTMLElement | null)?.closest?.('[data-tour-card]');
  if (typing(event.target) && !inCard) return;
  let handled = true;
  if (event.key === 'ArrowRight' && phase === 'shown') nextStep();
  else if (event.key === 'ArrowLeft' && phase === 'shown') prevStep();
  else if (event.key === 'Escape') end(false);
  else handled = false;
  if (handled) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}

export function TourLayer() {
  const tour = useTourStore((st) => st.tour);
  const index = useTourStore((st) => st.index);
  const phase = useTourStore((st) => st.phase);
  const target = useTourStore((st) => st.target);
  const blockedBy = useTourStore((st) => st.blockedBy);
  const ctx = useTourStore((st) => st.ctx);
  const adHoc = useTourStore((st) => st.adHoc);
  const skipped = useTourStore((st) => st.skipped);

  const [box, setBox] = useState<Box | null>(null);
  const [view, setView] = useState(viewport);
  const [place, setPlace] = useState<Placement | null>(null);
  const [showFinding, setShowFinding] = useState(false);
  /** the last step on screen: it stays up through a quick hop to the next one */
  const [shownAt, setShownAt] = useState<number | null>(null);
  const [, setMeasured] = useState(0);
  const card = useRef<HTMLDivElement>(null);
  const primary = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  const step: TourStep | undefined = tour?.steps[index];
  const shown = phase === 'shown' && !!target;

  useEffect(() => {
    if (phase === 'shown') setShownAt(index);
  }, [phase, index]);

  useEffect(() => {
    if (tour) return;
    setShownAt(null);
    setBox(null);
  }, [tour]);

  // keys, while a tour runs
  useEffect(() => {
    if (!tour) return;
    window.addEventListener('keydown', onTourKey, true);
    return () => window.removeEventListener('keydown', onTourKey, true);
  }, [tour]);

  // follow the target through scrolls, resizes and layout shifts
  useEffect(() => {
    // between steps the last box holds, so the page doesn't flash undimmed
    if (!shown || !target) return;
    const r = target.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) {
      target.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' });
    }
    let frame = 0;
    const tick = () => {
      const next = boxOf(target);
      setBox((prev) => (sameBox(prev, next) ? prev : next));
      const v = viewport();
      setView((prev) => (prev.width === v.width && prev.height === v.height ? prev : v));
      frame = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(frame);
  }, [shown, target]);

  // the target can go (the page re-rendered it) and the user can do the thing
  useEffect(() => {
    if (!shown || !step) return;
    let lostAt = 0;
    const timer = setInterval(() => {
      const { target: current } = useTourStore.getState();
      if (step.advanceOn && findAnchor(step.advanceOn)) {
        nextStep();
        return;
      }
      if (isShown(current)) {
        lostAt = 0;
        return;
      }
      const again = findAnchor(step.anchor);
      if (again) {
        useTourStore.setState({ target: again });
        lostAt = 0;
      } else if (!lostAt) {
        lostAt = Date.now();
      } else if (Date.now() - lostAt > LOST_GRACE_MS) {
        relocate();
      }
    }, WATCH_MS);
    return () => clearInterval(timer);
  }, [shown, step]);

  // a quick page change shows no card; a slow one says it is working
  useEffect(() => {
    if (phase !== 'finding') {
      setShowFinding(false);
      return;
    }
    const timer = setTimeout(() => setShowFinding(true), FINDING_CARD_MS);
    return () => clearTimeout(timer);
  }, [phase, index]);

  // the card's size can change after it renders (fonts, a longer step)
  const cardEl = card.current;
  useEffect(() => {
    if (!cardEl || typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(() => setMeasured((n) => n + 1));
    watch.observe(cardEl);
    return () => watch.disconnect();
  }, [cardEl]);

  // the card's spot, measured after it renders
  const phone = ctx.phone;
  const holding = phase === 'finding' && !showFinding && shownAt !== null;
  useLayoutEffect(() => {
    const el = card.current;
    if (!el) {
      setPlace(null);
      return;
    }
    const size = { width: el.offsetWidth, height: el.offsetHeight };
    const next = placeCard(shown || holding ? box : null, size, view, phone);
    setPlace((prev) =>
      prev && prev.top === next.top && prev.left === next.left && prev.side === next.side
        ? prev
        : next,
    );
  });

  // focus moves to the card on each step, so keys and a screen reader follow
  useEffect(() => {
    if (phase === 'shown' || phase === 'blocked') primary.current?.focus({ preventScroll: true });
  }, [phase, index]);

  if (!tour || !step) return null;

  const at = holding && shownAt !== null ? shownAt : index;
  const shownStep = tour.steps[at] ?? step;
  const { position, count, earlier } = stepPosition(tour.steps.length, at, skipped);
  const last = !tour.steps.some((_, i) => i > at && !skipped.includes(i));
  const hole = (shown || holding) && box ? holeFor(box, view) : null;
  const cardVisible = shown || holding || phase === 'blocked' || showFinding;
  const title = copy(shownStep.title, ctx);
  const body = copy(shownStep.body, ctx);
  const docked = place?.side === 'dock-top' || place?.side === 'dock-bottom';

  return (
    <div className={s.root} data-phase={phase} data-reduced={reducedMotion() || undefined}>
      {hole ? (
        <>
          <div
            className={s.panel}
            aria-hidden="true"
            style={{ top: 0, left: 0, right: 0, height: hole.top }}
          />
          <div
            className={s.panel}
            aria-hidden="true"
            style={{ top: hole.top + hole.height, left: 0, right: 0, bottom: 0 }}
          />
          <div
            className={s.panel}
            aria-hidden="true"
            style={{ top: hole.top, left: 0, width: hole.left, height: hole.height }}
          />
          <div
            className={s.panel}
            aria-hidden="true"
            style={{ top: hole.top, left: hole.left + hole.width, right: 0, height: hole.height }}
          />
          <div
            className={s.ring}
            aria-hidden="true"
            style={{ top: hole.top, left: hole.left, width: hole.width, height: hole.height }}
          />
        </>
      ) : (
        <div className={s.panel} data-full aria-hidden="true" style={{ inset: 0 }} />
      )}

      {cardVisible ? (
        <div
          ref={card}
          className={s.card}
          data-tour-card
          data-side={place?.side}
          data-phone={phone || undefined}
          data-busy={holding || undefined}
          role="dialog"
          aria-modal="false"
          aria-labelledby={titleId}
          aria-describedby={bodyId}
          style={
            docked
              ? undefined
              : place
                ? { top: place.top, left: place.left }
                : { top: -9999, left: -9999 }
          }
        >
          {phase === 'finding' && !holding ? (
            <>
              <div className={s.top}>
                <span className={s.kicker}>{adHoc ? 'Show me' : tour.title}</span>
                <EndButton />
              </div>
              <p className={s.title} id={titleId}>
                One moment
              </p>
              <p className={s.body} id={bodyId}>
                <span className={s.spin} aria-hidden="true" />
                Getting the page ready.
              </p>
            </>
          ) : phase === 'blocked' ? (
            <>
              <div className={s.top}>
                <span className={s.kicker}>{tour.title}</span>
                <EndButton />
              </div>
              <p className={s.title} id={titleId}>
                One thing first
              </p>
              <p className={s.body} id={bodyId}>
                {blockedBy}
              </p>
              <div className={s.bar}>
                <span />
                <div className={s.acts}>
                  <button ref={primary} type="button" className={s.btn} onClick={nextStep}>
                    Skip this step
                  </button>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className={s.top}>
                {adHoc ? (
                  <span className={s.kicker}>What’s new</span>
                ) : (
                  <span className={s.kicker}>
                    {tour.title}
                    <span className={s.count}>
                      {position} of {count}
                    </span>
                  </span>
                )}
                <EndButton />
              </div>
              <h2 className={s.title} id={titleId}>
                {title}
              </h2>
              <p className={s.body} id={bodyId}>
                {body}
              </p>
              {shownStep.advanceOn ? (
                <p className={s.wait}>
                  <span className={s.pulse} aria-hidden="true" />
                  Waiting for you
                </p>
              ) : null}
              <div className={s.bar}>
                {adHoc ? (
                  <span />
                ) : (
                  <span className={s.dots} aria-hidden="true">
                    {tour.steps.map((_, i) =>
                      skipped.includes(i) && i !== at ? null : (
                        <span
                          key={i}
                          className={s.dot}
                          data-on={i === at || undefined}
                          data-past={i < at || undefined}
                        />
                      ),
                    )}
                  </span>
                )}
                <div className={s.acts}>
                  {!adHoc && earlier ? (
                    <button type="button" className={s.btn} data-kind="ghost" onClick={prevStep}>
                      Back
                    </button>
                  ) : null}
                  <button
                    ref={primary}
                    type="button"
                    className={s.btn}
                    data-kind="primary"
                    onClick={adHoc ? () => end(true) : nextStep}
                  >
                    {adHoc ? 'Got it' : last ? 'Done' : 'Next'}
                    {!adHoc && !last ? <Icon name="chev" size={14} /> : null}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      ) : null}

      <p className={s.srOnly} aria-live="polite">
        {shown ? (adHoc ? title : `Step ${position} of ${count}. ${title}`) : ''}
      </p>
    </div>
  );
}

function EndButton() {
  return (
    <button
      type="button"
      className={s.x}
      aria-label="End tour"
      title="End tour (Esc)"
      onClick={() => end(false)}
    >
      <Icon name="x" size={14} />
    </button>
  );
}
