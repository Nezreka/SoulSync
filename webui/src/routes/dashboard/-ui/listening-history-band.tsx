/**
 * The Recently Played band — listening history's dashboard front door.
 *
 * Rows come from /api/stats/recent, the same listening_history spine the
 * stats page reads (media-server plays via the listening-stats worker +
 * SoulSync web-player plays). Pure display: no play buttons, no actions —
 * the Listen band below is for playing; this band is for "what have I been
 * listening to", and clicking anywhere goes to the stats page for the full
 * ledger.
 *
 * Renders NOTHING until history exists (listening stats off, or a fresh
 * install) — same calm-page rule as the content band. Speaks the content
 * band's exact card vocabulary (ya-card / dash-rail-*) so the two rails read
 * as siblings and it costs zero new card CSS.
 */

import { useEffect, useState } from 'react';

import type { RecentPlay, RecentPlayRow } from '../-dash.listening';

import { openArtistFromRail } from '../-dash.content';
import { toRecentPlays } from '../-dash.listening';

const LIMIT = 12;
const REFRESH_MS = 60_000;

export function ListeningHistoryBand() {
  const [plays, setPlays] = useState<RecentPlay[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      // Hidden tabs do no work — the steady-state poller rule.
      if (document.hidden) return;
      try {
        const response = await fetch(`/api/stats/recent?limit=${LIMIT}`);
        const data = (await response.json()) as { success?: boolean; tracks?: RecentPlayRow[] };
        if (!cancelled && data.success && Array.isArray(data.tracks)) {
          setPlays(toRecentPlays(data.tracks, new Date(), LIMIT));
        }
      } catch {
        // Band stays as-is on failure; an empty band renders nothing at all.
      }
    };
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!plays.length) return null;

  const openStats = () => void window.navigateToPage?.('stats');

  return (
    <article className="dash-card dash-card--rail" data-card="listening-history">
      <div className="dash-rail-head">
        <div className="dash-band-tabs">
          <button type="button" className="dash-band-tab active" onClick={openStats}>
            Recently Played
          </button>
        </div>
        <span className="dash-rail-subtitle">what you&apos;ve been listening to — tap for the full stats</span>
      </div>
      <div className="dash-rail">
        {plays.map((play) => (
          <div
            key={play.key}
            className="ya-card dash-rail-card"
            title={`${play.title} — ${play.artist}${play.source ? ` · ${play.source}` : ''}`}
            onClick={openStats}
          >
            <div className="ya-card-img">
              {play.imageUrl && <img src={play.imageUrl} alt="" loading="lazy" />}
              <div className="ya-card-placeholder" style={play.imageUrl ? { display: 'none' } : undefined}>
                ♫
              </div>
            </div>
            <div className="ya-card-gradient" />
            {play.ago && <div className="dash-rail-caption">{play.ago}</div>}
            <div className="ya-card-info">
              <div className="ya-card-name">{play.title}</div>
              {play.artist ? (
                <button
                  type="button"
                  className="ya-card-sub ya-card-sub--link"
                  title={`Open ${play.artist}`}
                  onClick={(event) => {
                    // The card body goes to the stats page; the artist
                    // line goes to the ARTIST — never both.
                    event.stopPropagation();
                    void openArtistFromRail({
                      name: play.artist,
                      libraryArtistId: play.artistDbId,
                    });
                  }}
                >
                  {play.artist}
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}
