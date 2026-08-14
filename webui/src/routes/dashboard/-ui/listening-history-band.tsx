/**
 * The Recently Played band — listening history's dashboard front door.
 *
 * Rows come from /api/stats/recent, the same listening_history spine the
 * stats page reads (media-server plays via the listening-stats worker +
 * SoulSync web-player plays).
 *
 * Clicking a card PLAYS the song again — library first, streaming second, the
 * same ladder Recently Added uses (Boulder: "clicking elsewhere on the card
 * should begin playing that song from your library if available... if song
 * isn't available, then stream it"). The band originally went to the stats
 * page from anywhere on the card, which made the most obvious gesture on a
 * rail of songs do the one thing you cannot do to a song. The header still
 * leads to the full ledger, and the artist line still leads to the artist.
 *
 * These rows carry no file path — /api/stats/recent is a play LEDGER, not a
 * library view — so the title+artist resolve in playTrackByMetadata is how a
 * card finds its file. That is also why an unmatched play still works: it
 * falls through to the stream source instead of doing nothing.
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
import { playTrackByMetadata } from '../../../features/playback/play-track';
import { getShellBridge } from '../../../platform/shell/bridge';

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
        <span className="dash-rail-subtitle">
          tap a song to play it again — the heading opens your full stats
        </span>
      </div>
      <div className="dash-rail">
        {plays.map((play) => (
          <div
            key={play.key}
            className="ya-card dash-rail-card"
            title={`Play ${play.title} — ${play.artist}${play.source ? ` · ${play.source}` : ''}`}
            onClick={() =>
              void playTrackByMetadata(getShellBridge(), play.title, play.artist, play.album)
            }
          >
            <div className="ya-card-img">
              {play.imageUrl && <img src={play.imageUrl} alt="" loading="lazy" />}
              <div
                className="ya-card-placeholder"
                style={play.imageUrl ? { display: 'none' } : undefined}
              >
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
                    // The card body PLAYS; the artist line goes to the
                    // ARTIST — never both.
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
