/**
 * WeeklyDigest — this-week listening banner under the header.
 *
 * One fetch of /api/stats/weekly-digest on mount: hours listened, tracks,
 * top artist, new discoveries, streak, and 7 daily buckets for the chart.
 * Renders NOTHING while loading, on failure, or when there are no plays in
 * the window (listening stats off, or a fresh install) — the calm-page rule.
 */

import { useEffect, useState } from 'react';

interface DigestDay {
  date: string;
  day: string;
  hours: number;
  /** plays that day */
  tracks?: number;
  /** the album played most that day, and its cover when the library has one */
  album?: string | null;
  cover?: string | null;
}

interface RepeatAlbum {
  name: string;
  artist: string | null;
  play_count: number;
  image_url?: string | null;
}

interface WeeklyDigestData {
  tracks_played: number;
  time_ms: number;
  top_artist: string | null;
  /** the library's own picture of that artist, when it has one */
  top_artist_image?: string | null;
  discoveries: number;
  streak_days: number;
  start_date: string;
  end_date: string;
  daily: DigestDay[];
  /** the seven days before these, for the comparison */
  previous_time_ms?: number;
  /** the albums played most this week */
  on_repeat?: RepeatAlbum[];
}

/** "1h 12m" for a day's bar; hours come rounded to a tenth from the server. */
function dayTime(hours: number): string {
  const minutes = Math.round(hours * 60);
  if (minutes <= 0) return '';
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ''}`
    : `${minutes}m`;
}

/** The same, short enough to sit over a bar on a phone: "2.6h", "36m". */
function dayTimeShort(hours: number): string {
  if (hours * 60 < 0.5) return '0';
  return hours >= 1 ? `${hours.toFixed(1).replace(/\.0$/, '')}h` : `${Math.round(hours * 60)}m`;
}

/** This week against the one before it, in words. Null with nothing to compare. */
export function weekTrend(
  timeMs: number,
  previousMs: number | undefined,
): { text: string; tone: 'up' | 'down' | 'same' } | null {
  if (!previousMs || previousMs <= 0) return null;
  const diff = timeMs - previousMs;
  if (Math.abs(diff) < 5 * 60000) return { text: 'about the same as last week', tone: 'same' };
  return diff > 0
    ? { text: `${formatHours(diff)} more than last week`, tone: 'up' }
    : { text: `${formatHours(-diff)} less than last week`, tone: 'down' };
}

/** A cover that hides itself when it will not load. */
function Cover({ src, className }: { src?: string | null; className: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <span className={`${className} ${className}--none`} />;
  return (
    <img className={className} src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
  );
}

function formatHours(timeMs: number): string {
  const totalMinutes = Math.round(timeMs / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function formatRange(start: string, end: string): string {
  const fmt = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    });
  return `${fmt(start)} – ${fmt(end)}`;
}

export function WeeklyDigest() {
  const [digest, setDigest] = useState<WeeklyDigestData | null>(null);
  const [artFailed, setArtFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/stats/weekly-digest')
      .then((response) => response.json())
      .then((data) => {
        if (cancelled) return;
        if (data?.success && typeof data.tracks_played === 'number' && data.tracks_played > 0) {
          setDigest(data as WeeklyDigestData);
        }
      })
      .catch(() => {
        // Banner stays hidden on failure; an empty digest renders nothing.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!digest) return null;

  const maxHours = Math.max(0.1, ...digest.daily.map((d) => d.hours));
  const trend = weekTrend(digest.time_ms, digest.previous_time_ms);
  const repeats = (digest.on_repeat ?? []).slice(0, 3);
  const openStats = () => void window.navigateToPage?.('stats');

  return (
    <section
      className="weekly-digest"
      data-card="weekly-digest"
      aria-label="This week in your library"
    >
      <div className="wd-head">
        <span className="wd-kicker">This week</span>
        <span className="wd-range">{formatRange(digest.start_date, digest.end_date)}</span>
        <button type="button" className="wd-link" onClick={openStats}>
          Full stats
        </button>
      </div>
      <div className="wd-body">
        <div className="wd-lead">
          <span className="wd-num">{formatHours(digest.time_ms)}</span>
          <span className="wd-label">
            listened across {digest.tracks_played.toLocaleString()} track
            {digest.tracks_played === 1 ? '' : 's'}
          </span>
          {trend ? <span className={`wd-trend wd-trend--${trend.tone}`}>{trend.text}</span> : null}
        </div>
        <div className="wd-chart">
          {digest.daily.map((day, index) => {
            const time = dayTime(day.hours);
            const classes = [
              'wd-bar-col',
              index === digest.daily.length - 1 ? 'wd-bar-col--today' : '',
              day.hours > 0 && day.hours === maxHours ? 'wd-bar-col--best' : '',
              day.hours > 0 ? '' : 'wd-bar-col--none',
            ];
            const said = time
              ? `${time}${day.tracks ? `, ${day.tracks} ${day.tracks === 1 ? 'play' : 'plays'}` : ''}${day.album ? `, mostly ${day.album}` : ''}`
              : 'Nothing played';
            return (
              <span className={classes.filter(Boolean).join(' ')} key={day.date} title={said}>
                <span className="wd-val">{dayTimeShort(day.hours)}</span>
                <span className="wd-stick">
                  <span
                    className="wd-bar"
                    style={{ height: `${Math.round((day.hours / maxHours) * 100)}%` }}
                  >
                    {day.cover ? <Cover src={day.cover} className="wd-bar-cover" /> : null}
                  </span>
                </span>
                <span className="wd-day">{day.day}</span>
              </span>
            );
          })}
        </div>
      </div>
      {repeats.length ? (
        <div className="wd-repeat" aria-label="On repeat this week">
          <p className="wd-repeat-title">On repeat</p>
          {repeats.map((album) => (
            <div className="wd-repeat-row" key={`${album.name}|${album.artist ?? ''}`}>
              <Cover src={album.image_url} className="wd-repeat-art" />
              <span className="wd-repeat-text">
                <b title={album.name}>{album.name}</b>
                {album.artist ? <span>{album.artist}</span> : null}
              </span>
              <span className="wd-repeat-plays">
                {album.play_count} {album.play_count === 1 ? 'play' : 'plays'}
              </span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="wd-chips">
        {digest.top_artist && (
          <span className="wd-chip wd-chip--artist">
            {digest.top_artist_image && !artFailed ? (
              <img
                src={digest.top_artist_image}
                alt=""
                loading="lazy"
                onError={() => setArtFailed(true)}
              />
            ) : null}
            <b>{digest.top_artist}</b>top artist
          </span>
        )}
        <span className="wd-chip">
          <b>{digest.discoveries}</b>new discover{digest.discoveries === 1 ? 'y' : 'ies'}
        </span>
        <span className="wd-chip">
          <b>{digest.streak_days}-day</b>streak
        </span>
      </div>
    </section>
  );
}
