import { useCallback, useEffect, useState } from 'react';

import { usePromoVideo } from '../-discover.use-promo';
import { PromoBanner } from './promo-banner';
import { useReveal } from './pulse-banners';

/**
 * a rail of tall 9:16 cards, one artist each, like spotify's vertical video
 * feed. every card's artwork is always drifting. the rail plays its cards'
 * videos one after another on its own (a countdown line shows how long is
 * left), point at a card and its video plays right away, and while the
 * pointer is on the rail the cycle waits.
 */

export interface RailArtist {
  key: string;
  name: string;
  image: string;
  reason?: string;
  href: string;
}

export interface VideoRailProps {
  title: string;
  subtitle: string;
  artists: RailArtist[];
  videosOn: boolean;
}

/** how long each card gets before the rail moves on */
export const RAIL_CYCLE_MS = 14000;

/**
 * the card to play after `current`: the next one in rail order that can play
 * (has a video, on screen), wrapping around. null when none can.
 */
export function nextUp(order: string[], ready: ReadonlySet<string>, current: string | null) {
  if (order.length === 0) return null;
  const from = current ? order.indexOf(current) : -1;
  for (let i = 1; i <= order.length; i += 1) {
    const key = order[(from + i + order.length) % order.length];
    if (ready.has(key)) return key;
  }
  return null;
}

function RailCard({
  artist,
  videosOn,
  up,
  onReady,
}: {
  artist: RailArtist;
  videosOn: boolean;
  up: boolean;
  onReady: (key: string, ready: boolean) => void;
}) {
  const promo = usePromoVideo(`rail-${artist.key}`, artist.name, null, artist.image, videosOn);
  const ready = Boolean(promo.videoId) && promo.visible;
  const { setBoost } = promo;
  useEffect(() => onReady(artist.key, ready), [artist.key, ready, onReady]);
  useEffect(() => {
    setBoost(up);
    return () => setBoost(false);
  }, [up, setBoost]);
  return (
    <PromoBanner
      kind="rail"
      size="portrait"
      eyebrow={artist.reason || 'For you'}
      title={artist.name}
      art={artist.image}
      actions={
        <a className="dsc-pulse-btn" href={artist.href}>
          View artist
        </a>
      }
      glowRgb={promo.glowRgb}
      rootRef={promo.ref}
      videoId={promo.videoId}
      playing={promo.playing}
      onUnplayable={promo.onUnplayable}
      onHoverChange={promo.setHover}
      onSoundChange={promo.setHeld}
      cycleMs={up ? RAIL_CYCLE_MS : null}
    />
  );
}

export function VideoRail({ title, subtitle, artists, videosOn }: VideoRailProps) {
  const [ready, setReady] = useState<ReadonlySet<string>>(() => new Set());
  const [up, setUp] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [revealRef, revealed] = useReveal<HTMLDivElement>();
  const order = artists.map((a) => a.key);
  const orderKey = order.join('|');

  const onReady = useCallback((key: string, isReady: boolean) => {
    setReady((prev) => {
      if (prev.has(key) === isReady) return prev;
      const next = new Set(prev);
      if (isReady) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);

  // the card that's up stopped being able to play: move on now, not in 14s
  useEffect(() => {
    if (up && ready.has(up)) return;
    setUp(nextUp(orderKey ? orderKey.split('|') : [], ready, up));
  }, [ready, up, orderKey]);

  useEffect(() => {
    if (paused || !up) return;
    const t = setTimeout(
      () => setUp(nextUp(orderKey ? orderKey.split('|') : [], ready, up)),
      RAIL_CYCLE_MS,
    );
    return () => clearTimeout(t);
  }, [paused, up, ready, orderKey]);

  if (artists.length === 0) return null;
  return (
    <section
      className={`dsc-video-rail${paused ? ' is-paused' : ''}`}
      aria-label={title}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <header className="dsc-section-head">
        <h3>{title}</h3>
        <p>{subtitle}</p>
      </header>
      <div ref={revealRef} className={`dsc-video-rail-track dsc-reveal${revealed ? ' is-in' : ''}`}>
        {artists.map((a) => (
          <RailCard
            key={a.key}
            artist={a}
            videosOn={videosOn}
            up={!paused && up === a.key}
            onReady={onReady}
          />
        ))}
      </div>
    </section>
  );
}
