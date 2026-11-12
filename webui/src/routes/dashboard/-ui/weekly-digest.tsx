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
        </div>
        <div className="wd-chart" aria-hidden="true">
          {digest.daily.map((day) => (
            <span className="wd-bar-col" key={day.date} title={`${day.hours}h`}>
              <span
                className="wd-bar"
                style={{ height: `${Math.round((day.hours / maxHours) * 100)}%` }}
              />
              <span className="wd-day">{day.day}</span>
            </span>
          ))}
        </div>
      </div>
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
