/**
 * soulsync's own playlists on a shelf: flow, a daily mix, fresh tape. its
 * covers, its name, the line that says what it is, and a play button.
 * one that needs songs you don't have yet says so.
 */

import type { DiscoverCard as Card } from '../../-player.api';

import { songCount } from '../../-lib/playlists';
import { CardPlay, PlayerLink, styles as k } from '../kit';
import m from './music.module.css';
import { PlaylistCover } from './playlist-cover';

export function discoverHref(key: string) {
  return `/player/music/discover/${encodeURIComponent(key)}`;
}

export function DiscoverCard({ card }: { card: Card }) {
  const sub = [card.blurb, card.track_count ? songCount(card.track_count) : null]
    .filter(Boolean)
    .join(' · ');
  return (
    <div className={k.cardWrap} data-shape="square">
      <PlayerLink href={discoverHref(card.key)} className={k.card} data-nav>
        <div className={k.art}>
          <PlaylistCover
            playlist={{
              name: card.name,
              image_url: null,
              cover_tiles: card.cover_tiles,
              liked: false,
            }}
          />
          {!card.owned ? (
            <span className={m.toGetBadge} title="Some of these aren't in your library yet">
              New to you
            </span>
          ) : null}
        </div>
        <div className={k.cardText}>
          <div className={k.cardTitle} title={card.name}>
            {card.name}
          </div>
          <div className={k.cardSub} title={sub}>
            {sub}
          </div>
        </div>
      </PlayerLink>
      <div className={k.cardPlaySlot}>
        <CardPlay target={{ kind: 'discover', key: card.key }} title={card.name} />
      </div>
    </div>
  );
}
