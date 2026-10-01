import { useEffect, useState } from 'react';

/**
 * the page's table of contents: one chip per zone that actually rendered,
 * the one you're reading lit up.
 *
 * replaces two rows of pills that only ever scrolled. seven of them went to
 * the same four places, 'energizing' and 'chill' both landed on For You, and
 * none of them did what their label said. every chip here is a zone that is
 * really on the page, and says so by lighting up as you scroll through it.
 */

export interface DiscoverNavItem {
  /** the zone's element id, the scroll target */
  id: string;
  label: string;
}

export interface DiscoverNavProps {
  items: DiscoverNavItem[];
  onOpenLayout: () => void;
}

/** the zone whose top has passed this line (px from the viewport top) is the one being read. */
const READ_LINE_PX = 160;

/** which zone is being read: the last one whose top is above the read line. */
export function activeZone(
  tops: { id: string; top: number }[],
  line = READ_LINE_PX,
): string | null {
  let active: string | null = tops[0]?.id ?? null;
  for (const { id, top } of tops) {
    if (top <= line) active = id;
  }
  return active;
}

export function DiscoverNav({ items, onOpenLayout }: DiscoverNavProps) {
  const [active, setActive] = useState<string | null>(items[0]?.id ?? null);
  const key = items.map((i) => i.id).join('|');

  useEffect(() => {
    // scroll doesn't bubble, but a capturing listener on window hears the
    // page host's scroller too, whichever element that turns out to be
    let frame = 0;
    const measure = () => {
      frame = 0;
      const tops = items
        .map((i) => ({ id: i.id, el: document.getElementById(i.id) }))
        .filter((t): t is { id: string; el: HTMLElement } => t.el !== null)
        .map((t) => ({ id: t.id, top: t.el.getBoundingClientRect().top }));
      const next = activeZone(tops);
      setActive((cur) => (cur === next ? cur : next));
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll, { capture: true });
      if (frame) cancelAnimationFrame(frame);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (items.length === 0) return null;

  return (
    <nav className="dsc-nav" aria-label="Discover sections">
      <div className="dsc-nav-chips">
        {items.map((item) => (
          <button
            type="button"
            key={item.id}
            className={`dsc-nav-chip${active === item.id ? ' active' : ''}`}
            aria-current={active === item.id ? 'true' : undefined}
            onClick={() => {
              setActive(item.id);
              document
                .getElementById(item.id)
                ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="dsc-nav-layout"
        onClick={onOpenLayout}
        title="Choose which sections show on Discover, and their order"
      >
        <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
          <path
            d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            fill="none"
          />
          <circle cx="16" cy="6" r="2" stroke="currentColor" strokeWidth="2" fill="none" />
          <circle cx="10" cy="12" r="2" stroke="currentColor" strokeWidth="2" fill="none" />
          <circle cx="18" cy="18" r="2" stroke="currentColor" strokeWidth="2" fill="none" />
        </svg>
        <span>Customize</span>
      </button>
    </nav>
  );
}
