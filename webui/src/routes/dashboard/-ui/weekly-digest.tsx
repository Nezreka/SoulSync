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
          full stats →
        </button>
      </div>
      <div className="wd-body">
        <div className="wd-stats">
          <div className="wd-stat">
            <span className="wd-num">{formatHours(digest.time_ms)}</span>
            <span className="wd-label">listened</span>
          </div>
          <div className="wd-stat">
            <span className="wd-num">{digest.tracks_played}</span>
            <span className="wd-label">tracks played</span>
          </div>
          {digest.top_artist && (
            <div className="wd-stat">
              <span className="wd-num">{digest.top_artist}</span>
              <span className="wd-label">top artist</span>
            </div>
          )}
          <div className="wd-stat">
            <span className="wd-num">{digest.discoveries}</span>
            <span className="wd-label">new discoveries</span>
          </div>
          <div className="wd-stat">
            <span className="wd-num">{digest.streak_days}-day</span>
            <span className="wd-label">streak</span>
          </div>
        </div>
        <div className="wd-chart" aria-hidden="true">
          {digest.daily.map((day) => (
            <span className="wd-bar-col" key={day.date}>
              <span
                className="wd-bar"
                style={{ height: `${Math.round((day.hours / maxHours) * 100)}%` }}
              />
              <span className="wd-day">{day.day}</span>
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
