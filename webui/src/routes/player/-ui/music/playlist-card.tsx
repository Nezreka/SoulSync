/** a playlist on a shelf: its cover, its name, how many songs, and a play button */

import type { PlaylistSummary } from '../../-player.api';

import { songCount, sourceName } from '../../-lib/playlists';
import { CardPlay, PlayerLink, styles as k } from '../kit';
import { PlaylistCover } from './playlist-cover';

export function PlaylistCard({ playlist }: { playlist: PlaylistSummary }) {
  const from = sourceName(playlist.source);
  const sub = [from, songCount(playlist.track_count)].filter(Boolean).join(' · ');
  return (
    <div className={k.cardWrap} data-shape="square">
      <PlayerLink href={`/player/music/playlist/${playlist.id}`} className={k.card} data-nav>
        <div className={k.art}>
          <PlaylistCover playlist={playlist} />
        </div>
        <div className={k.cardText}>
          <div className={k.cardTitle} title={playlist.name}>
            {playlist.name}
          </div>
          <div className={k.cardSub}>{sub}</div>
        </div>
      </PlayerLink>
      {playlist.track_count ? (
        <div className={k.cardPlaySlot}>
          <CardPlay target={{ kind: 'playlist', id: playlist.id }} title={playlist.name} />
        </div>
      ) : null}
    </div>
  );
}
