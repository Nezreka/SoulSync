import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';

import {
  type ArtistYourPlays as Plays,
  artistYourPlaysQueryOptions,
  monthYear,
  rankLabel,
  sinceLabel,
} from '../-artist-your-plays';
import styles from './artist-your-plays.module.css';

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
}

function monthName(key: string, style: 'short' | 'long'): string {
  const [year, month] = key.split('-').map(Number);
  return new Date(year, month - 1, 1).toLocaleString('en-US', {
    month: style,
    ...(style === 'long' ? { year: 'numeric' } : {}),
  });
}

function Months({ months }: { months: Plays['months'] }) {
  if (!months.length) return null;
  const peak = Math.max(1, ...months.map((m) => m.plays));
  return (
    <figure className={styles.months} aria-label="Your plays per month, the last 12 months">
      <div className={styles.bars}>
        {months.map((m) => {
          const tip = `${monthName(m.month, 'long')}: ${plural(m.plays, 'play', 'plays')}`;
          return (
            <span key={m.month} className={styles.col} title={tip} aria-label={tip}>
              <span
                className={m.plays ? styles.bar : `${styles.bar} ${styles.barEmpty}`}
                style={m.plays ? { height: `${(m.plays / peak) * 100}%` } : undefined}
              />
            </span>
          );
        })}
      </div>
      <figcaption className={styles.axis}>
        <span>{monthName(months[0].month, 'short')}</span>
        <span>{monthName(months[months.length - 1].month, 'short')}</span>
      </figcaption>
    </figure>
  );
}

/**
 * "You and <artist>": your own plays of this artist, and the songs of theirs
 * you play most. Nothing renders until you have played them.
 */
export function ArtistYourPlays({
  artistId,
  artistName,
}: {
  artistId: number;
  artistName: string;
}) {
  const query = useQuery(artistYourPlaysQueryOptions(artistId));
  const plays = query.data;
  if (!plays || !plays.play_count) return null;
  return (
    <section className={styles.panel} aria-label={`You and ${artistName}`}>
      <div className={styles.card}>
        <p className={styles.eyebrow}>You and {artistName}</p>
        <p className={styles.total}>
          <b>{plays.play_count.toLocaleString('en-US')}</b>
          <span>{plays.play_count === 1 ? 'play' : 'plays'}</span>
        </p>
        <ul className={styles.facts}>
          <li>{rankLabel(plays.rank, plays.artists_played)}</li>
          {plays.first_played ? <li>First listened {monthYear(plays.first_played)}</li> : null}
          {plays.last_played ? (
            <li>
              Last played {sinceLabel(plays.last_played)}
              {plays.plays_30d
                ? ` · ${plays.plays_30d.toLocaleString('en-US')} in the last 30 days`
                : ''}
            </li>
          ) : null}
        </ul>
        <Months months={plays.months} />
      </div>
      {plays.top_tracks.length ? (
        <div className={styles.most}>
          <p className={styles.eyebrow}>You play most</p>
          <ol className={styles.tracks}>
            {plays.top_tracks.map((track, index) => {
              const body = (
                <>
                  <span className={styles.num}>{index + 1}</span>
                  {track.thumb_url ? (
                    <img className={styles.thumb} src={track.thumb_url} alt="" loading="lazy" />
                  ) : (
                    <span className={styles.thumb} aria-hidden="true" />
                  )}
                  <span className={styles.name}>
                    <span className={styles.title} title={track.title}>
                      {track.title}
                    </span>
                    <span className={styles.sub}>
                      {track.last_played ? `last played ${sinceLabel(track.last_played)}` : ''}
                    </span>
                  </span>
                  <span className={styles.count}>{plural(track.play_count, 'play', 'plays')}</span>
                </>
              );
              return (
                <li key={`${track.title}-${index}`}>
                  {track.album_id ? (
                    <Link
                      from="/library"
                      to="/library"
                      search={(previous) => ({ ...previous, album: track.album_id ?? undefined })}
                      className={styles.row}
                      title={track.album ? `Open ${track.album}` : 'Open the album'}
                    >
                      {body}
                    </Link>
                  ) : (
                    <div className={styles.row}>{body}</div>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
