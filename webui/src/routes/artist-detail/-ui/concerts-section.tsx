import { useEffect, useState } from 'react';

import {
  checkTracksBody,
  mergeOwnership,
  ownedCount,
} from '../-artist-detail.owned-tracks';

/**
 * What this artist is playing, and what they actually played.
 *
 * Two providers answering two different questions:
 *   Ticketmaster — upcoming dates, venue, tickets. Nothing historical.
 *   Setlist.fm  — the songs from a past show, in order. This is the half that
 *                 connects to a music library, because a setlist is a playlist
 *                 somebody already made and tested on a live audience.
 *
 * So a setlist gets a Play button. It resolves against your library first
 * (the same /api/library/check-tracks path the release-card play button uses),
 * meaning you hear the show as far as you own it and the rest is simply absent
 * rather than a queue full of failures.
 *
 * Renders nothing at all when neither provider is configured. An empty section
 * headed "Concerts" on every artist page would be a permanent advert for a
 * feature the user has chosen not to set up.
 */

interface ConcertEvent {
  id?: string;
  datetime?: string;
  venue?: string;
  city?: string;
  region?: string;
  country?: string;
  url?: string;
  tickets_url?: string;
}

interface Setlist {
  id?: string;
  date?: string;
  venue?: string;
  city?: string;
  country?: string;
  tour?: string;
  url?: string;
  songs: string[];
  song_count: number;
}

interface ConcertsPayload {
  artist?: string;
  upcoming?: ConcertEvent[];
  setlists?: Setlist[];
  providers?: Record<string, { configured?: boolean; error?: string }>;
}

/** "Berghain, Berlin" — skipping whichever half is missing rather than
 *  rendering a stray comma. */
export function placeLabel(parts: Array<string | undefined>): string {
  return parts.map((p) => (p || '').trim()).filter(Boolean).join(', ');
}

/** Setlist.fm dates are dd-MM-yyyy, which every Date parser reads as either
 *  nonsense or the wrong month. Reordered by hand rather than trusted. */
export function formatSetlistDate(raw: string | undefined): string {
  const m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(raw || '').trim());
  if (!m) return String(raw || '');
  const d = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
  if (Number.isNaN(d.getTime())) return String(raw || '');
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function formatEventDate(raw: string | undefined): string {
  const d = new Date(String(raw || ''));
  if (Number.isNaN(d.getTime())) return String(raw || '');
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

export interface ConcertsSectionProps {
  artistName: string;
  mbid?: string;
}

export function ConcertsSection({ artistName, mbid }: ConcertsSectionProps) {
  const [data, setData] = useState<ConcertsPayload | null>(null);
  const [openSetlist, setOpenSetlist] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);

  useEffect(() => {
    if (!artistName) return;
    let live = true;
    const params = new URLSearchParams({ name: artistName });
    if (mbid) params.set('mbid', mbid);
    void fetch(`/api/artist/${encodeURIComponent(artistName)}/concerts?${params}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => {
        if (live) setData(payload);
      })
      .catch(() => {
        if (live) setData(null);
      });
    return () => {
      live = false;
    };
  }, [artistName, mbid]);

  const providers = data?.providers || {};
  const anyConfigured = Object.values(providers).some((p) => p?.configured);
  const upcoming = data?.upcoming || [];
  const setlists = data?.setlists || [];

  // Nothing configured, or nothing to show: stay out of the way entirely.
  if (!data || !anyConfigured || (!upcoming.length && !setlists.length)) return null;

  const playSetlist = async (setlist: Setlist) => {
    const key = String(setlist.id || setlist.date || '');
    if (playing) return;
    setPlaying(key);
    try {
      const rows: Array<Record<string, unknown>> = setlist.songs.map(
        (title) => ({ name: title, title }),
      );
      const resp = await fetch('/api/library/check-tracks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(checkTracksBody(artistName, '', rows)),
      });
      const owned = resp.ok ? await resp.json() : null;
      const merged = mergeOwnership(rows, owned?.owned_tracks).filter((t) => t.file_path);
      if (!merged.length) {
        window.showToast?.(`You don't own any of the ${setlist.song_count} songs from that show`, 'info');
        return;
      }
      const queue = merged.map((t) => ({
        ...t,
        artist: artistName,
        artists: [{ name: artistName }],
      }));
      await window.playTrackList?.(queue, `${artistName} — ${placeLabel([setlist.venue, setlist.city])}`);
      // Say what is missing rather than quietly playing a short set.
      if (merged.length < setlist.songs.length) {
        window.showToast?.(
          `Playing ${merged.length} of ${setlist.songs.length} songs you own from that set`,
          'info',
        );
      }
    } catch {
      window.showToast?.('Could not play that setlist', 'error');
    } finally {
      setPlaying(null);
    }
  };

  return (
    <div className="artist-concerts-section">
      <h3 className="artist-concerts-title">Live</h3>

      {upcoming.length ? (
        <div className="artist-concerts-block">
          <div className="artist-concerts-block-title">Upcoming</div>
          <ul className="artist-concerts-list">
            {upcoming.map((ev, i) => (
              <li className="artist-concert-row" key={ev.id || i}>
                <span className="artist-concert-date">{formatEventDate(ev.datetime)}</span>
                <span className="artist-concert-where">
                  {placeLabel([ev.venue, ev.city, ev.region || ev.country])}
                </span>
                {ev.tickets_url || ev.url ? (
                  <a
                    className="artist-concert-link"
                    href={ev.tickets_url || ev.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {ev.tickets_url ? 'Tickets' : 'Details'}
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {setlists.length ? (
        <div className="artist-concerts-block">
          <div className="artist-concerts-block-title">Recent setlists</div>
          <ul className="artist-concerts-list">
            {setlists.map((sl, i) => {
              const key = String(sl.id || sl.date || i);
              const open = openSetlist === key;
              return (
                <li className="artist-concert-row artist-setlist-row" key={key}>
                  <div className="artist-setlist-head">
                    <button
                      type="button"
                      className="artist-setlist-toggle"
                      aria-expanded={open}
                      onClick={() => setOpenSetlist(open ? null : key)}
                    >
                      <span className="artist-concert-date">{formatSetlistDate(sl.date)}</span>
                      <span className="artist-concert-where">
                        {placeLabel([sl.venue, sl.city, sl.country])}
                      </span>
                      <span className="artist-setlist-count">{sl.song_count} songs</span>
                    </button>
                    <button
                      type="button"
                      className="artist-setlist-play"
                      title="Play the songs from this show that you own"
                      disabled={playing !== null}
                      onClick={() => void playSetlist(sl)}
                    >
                      {playing === key ? '…' : '▶'}
                    </button>
                  </div>
                  {open ? (
                    <ol className="artist-setlist-songs">
                      {sl.songs.map((song, si) => (
                        <li key={`${key}-${si}`}>{song}</li>
                      ))}
                    </ol>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export { ownedCount };
