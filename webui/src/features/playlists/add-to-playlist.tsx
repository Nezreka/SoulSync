/**
 * add to playlist, the spotify way: a + on a track opens a small picker at the
 * button. pick one of your playlists, or make a new one with the song in it.
 *
 * one picker for the whole app. react pages render <AddToPlaylistButton>;
 * vanilla pages (the download missing modal, the player) call
 * window.openAddToPlaylist with the same track shape. both land here, so the
 * picker, the duplicate prompt and the toasts are the same everywhere.
 */

import type { CSSProperties } from 'react';

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';

import { thumb } from '@/platform/artwork-thumb';
import { usePopoverDismiss } from '@/routes/sync/-ui/use-popover-dismiss';
import { usePopoverPosition } from '@/routes/sync/-ui/use-popover-position';

import type {
  AddTracksResult,
  PlaylistMembership,
  UserPlaylistSummary,
  UserPlaylistTrack,
} from './user-playlists';

import styles from './add-to-playlist.module.css';
import {
  addToUserPlaylist,
  createUserPlaylist,
  fetchMembership,
  fetchUserPlaylists,
  likeSongs,
  removeSongsFromUserPlaylist,
  unlikeSongs,
} from './user-playlists';

declare global {
  interface Window {
    /** open the add-to-playlist picker. anchor is the button it pops from. */
    openAddToPlaylist?: (
      tracks: UserPlaylistTrack | UserPlaylistTrack[],
      anchor?: HTMLElement | null,
    ) => void;
  }
}

const PICKER_WIDTH = 320;

interface Request {
  id: number;
  tracks: UserPlaylistTrack[];
  anchor: HTMLElement | null;
  top: number;
  left: number;
}

/* ── the one open request, shared by every button and the window global ──── */

let current: Request | null = null;
let opened = 0;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** artist + title or nothing: a row without both can't be identified. */
export function cleanTracks(
  tracks: UserPlaylistTrack | UserPlaylistTrack[] | null | undefined,
): UserPlaylistTrack[] {
  const list = Array.isArray(tracks) ? tracks : tracks ? [tracks] : [];
  return list
    .map((t) => ({
      ...t,
      track_name: String(t?.track_name ?? '').trim(),
      artist_name: String(t?.artist_name ?? '').trim(),
    }))
    .filter((t) => t.track_name && t.artist_name);
}

export function openAddToPlaylist(
  tracks: UserPlaylistTrack | UserPlaylistTrack[],
  anchor?: HTMLElement | null,
): void {
  const clean = cleanTracks(tracks);
  if (clean.length === 0) {
    window.showToast?.("Can't add this one, it has no artist or title", 'error');
    return;
  }
  // the same button again closes it, like every other popover here
  if (current && anchor && current.anchor === anchor) {
    closeAddToPlaylist();
    return;
  }
  const box = anchor?.getBoundingClientRect();
  opened += 1;
  current = {
    id: opened,
    tracks: clean,
    anchor: anchor ?? null,
    top: box ? box.bottom + 6 : window.innerHeight / 3,
    left: box ? box.right - PICKER_WIDTH : (window.innerWidth - PICKER_WIDTH) / 2,
  };
  emit();
}

export function closeAddToPlaylist(): void {
  if (!current) return;
  current = null;
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function useRequest(): Request | null {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => null,
  );
}

/* ── the button ─────────────────────────────────────────────────────────── */

/** lines with a plus: "add to a list". not a bare +, which already means
 *  add-to-queue on the library rows. */
export function PlaylistAddIcon({ size = 16 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path
        fill="currentColor"
        d="M14 10H3v2h11v-2zm0-4H3v2h11V6zm4 8v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zM3 16h7v-2H3v2z"
      />
    </svg>
  );
}

export function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        d="M12 5v14M5 12h14"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

export function AddToPlaylistButton({
  track,
  className,
  style,
  size = 16,
}: {
  /** null when the row has nothing to add (no title or artist): no button. */
  track: UserPlaylistTrack | UserPlaylistTrack[] | null;
  /** the page's own row-button class, so it sits in the row like its neighbours */
  className?: string;
  style?: CSSProperties;
  size?: number;
}) {
  const clean = cleanTracks(track);
  if (clean.length === 0) return null;
  const label =
    clean.length === 1 ? `Add ${clean[0].track_name} to a playlist` : 'Add to a playlist';
  return (
    <button
      type="button"
      className={className ?? styles.button}
      style={style}
      aria-label={label}
      title="Add to playlist"
      onClick={(event) => {
        // rows open things on click; this is its own action
        event.stopPropagation();
        event.preventDefault();
        openAddToPlaylist(clean, event.currentTarget);
      }}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <PlaylistAddIcon size={size} />
    </button>
  );
}

/* ── the picker ─────────────────────────────────────────────────────────── */

function describe(tracks: UserPlaylistTrack[]): string {
  return tracks.length === 1 ? `"${tracks[0].track_name}"` : `${tracks.length} songs`;
}

/** the duplicate prompt. names the copy that's there when it's spelled
 *  differently, so "Alright" vs "Alright (Remastered)" makes sense. */
export function duplicateMessage(
  dupes: AddTracksResult['duplicates'],
  playlistName: string,
): string {
  if (dupes.length > 1)
    return `${dupes.length} of these songs look like they're already in ${playlistName}.`;
  const [d] = dupes;
  const there = d.existing_track_name ?? d.track_name;
  if (there.trim().toLowerCase() === d.track_name.trim().toLowerCase()) {
    return `"${d.track_name}" is already in ${playlistName}.`;
  }
  return `"${d.track_name}" looks like "${there}", which is already in ${playlistName}.`;
}

/** add, ask about duplicates, say what happened. exported for the tests. */
export async function addAndReport(
  playlist: { id: number; name: string },
  tracks: UserPlaylistTrack[],
): Promise<void> {
  const result = await addToUserPlaylist(playlist.id, tracks);
  const dupes = result.duplicates ?? [];
  if (dupes.length > 0) {
    const again = await window.showConfirmDialog?.({
      title: 'Already added',
      message: duplicateMessage(dupes, playlist.name),
      confirmText: 'Add anyway',
      cancelText: result.added > 0 ? 'Skip' : "Don't add",
    });
    if (again) {
      const back = new Set(dupes.map((d) => `${d.artist_name}\u0000${d.track_name}`));
      const retry = tracks.filter((t) => back.has(`${t.artist_name}\u0000${t.track_name}`));
      const forced = await addToUserPlaylist(playlist.id, retry, true);
      result.added += forced.added;
    }
  }
  if (result.added > 0) {
    window.showToast?.(
      tracks.length === 1
        ? `Added to ${playlist.name}`
        : `Added ${result.added} song${result.added === 1 ? '' : 's'} to ${playlist.name}`,
      'success',
    );
  }
}

/**
 * the same art the playlists page shows: the playlist's own cover, else its
 * collage (four covers make a 2x2, fewer make one), else its first letter.
 * a cover that won't load drops to the next rung instead of a broken image.
 */
export function PlaylistCover({ playlist }: { playlist: UserPlaylistSummary }) {
  const tiles = playlist.cover_tiles ?? [];
  const [failed, setFailed] = useState(false);
  const single = playlist.image_url || (tiles.length > 0 && tiles.length < 4 ? tiles[0] : '');
  if (!failed && !playlist.image_url && tiles.length >= 4) {
    return (
      <span className={`${styles.cover} ${styles.mosaic}`} aria-hidden="true">
        {tiles.slice(0, 4).map((tile) => (
          <img
            key={tile}
            src={thumb(tile, 'card')}
            alt=""
            loading="lazy"
            onError={() => setFailed(true)}
          />
        ))}
      </span>
    );
  }
  if (!failed && single) {
    return (
      <img
        className={styles.cover}
        src={thumb(single, 'card')}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span className={styles.cover} aria-hidden="true">
      {playlist.name.slice(0, 1).toUpperCase()}
    </span>
  );
}

function HeartIcon({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path
        d="M12 20.5s-7.5-4.6-9.3-9.2C1.4 8 3.6 4.5 7.1 4.5c2 0 3.6 1.1 4.9 2.9 1.3-1.8 2.9-2.9 4.9-2.9 3.5 0 5.7 3.5 4.4 6.8-1.8 4.6-9.3 9.2-9.3 9.2z"
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2.2" />
      <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <path
        d="m5 12.5 4.2 4.2L19 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** the header's art: the song's own cover when the page sent one. */
function subjectArt(tracks: UserPlaylistTrack[]): string {
  const art = tracks.find((t) => t.image_url)?.image_url;
  return art ? thumb(art, 'card') : '';
}

/** what the header calls the songs: the title and artist, or "5 songs". */
export function subjectLines(tracks: UserPlaylistTrack[]): { title: string; by: string } {
  if (tracks.length === 1) return { title: tracks[0].track_name, by: tracks[0].artist_name };
  const artists = new Set(tracks.map((t) => t.artist_name));
  const albums = new Set(tracks.map((t) => t.album_name || ''));
  const by =
    albums.size === 1 && tracks[0].album_name
      ? `${tracks[0].album_name} · ${[...artists][0]}`
      : artists.size === 1
        ? [...artists][0]
        : `${artists.size} artists`;
  return { title: `${tracks.length} songs`, by };
}

/** the row's right side: a tick when it already has all of them, a count
 *  when it has some. */
export function memberState(
  playlistId: number,
  membership: PlaylistMembership | null,
): 'all' | 'some' | 'none' {
  const have = membership?.playlists?.[String(playlistId)] ?? 0;
  if (!have || !membership) return 'none';
  return have >= membership.total ? 'all' : 'some';
}

function Picker({ request }: { request: Request }) {
  const ref = useRef<HTMLDivElement>(null);
  const pos = usePopoverPosition({ top: request.top, left: request.left }, ref);
  const [playlists, setPlaylists] = useState<UserPlaylistSummary[] | null>(null);
  const [membership, setMembership] = useState<PlaylistMembership | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [newName, setNewName] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [liking, setLiking] = useState(false);

  usePopoverDismiss({ ref, anchor: request.anchor, onClose: closeAddToPlaylist });

  // focus comes in, so escape and tab act on the picker and not on a dialog
  // underneath; it goes back to the + when the picker closes
  useEffect(() => {
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) el.focus();
    const anchor = request.anchor;
    return () => {
      if (anchor && document.contains(anchor)) anchor.focus();
    };
  }, [request.anchor]);

  useEffect(() => {
    let live = true;
    fetchUserPlaylists()
      .then((list) => live && setPlaylists(list))
      .catch((err: unknown) => live && setError(err instanceof Error ? err.message : String(err)));
    // the ticks are a nicety: a failed check just leaves them off
    fetchMembership(request.tracks)
      .then((m) => live && setMembership(m))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [request.tracks]);

  const pick = useCallback(
    async (playlist: { id: number; name: string }) => {
      setSaving(true);
      // close first: the duplicate prompt is its own modal and shouldn't sit
      // behind a popover
      closeAddToPlaylist();
      try {
        await addAndReport(playlist, request.tracks);
      } catch (err) {
        window.showToast?.(err instanceof Error ? err.message : 'Failed to add', 'error');
      }
    },
    [request.tracks],
  );

  // a ticked playlist already has every one of them, so a click takes them out
  const untick = useCallback(
    async (playlist: { id: number; name: string }) => {
      setSaving(true);
      closeAddToPlaylist();
      try {
        await removeSongsFromUserPlaylist(playlist.id, request.tracks);
        window.showToast?.(`Removed from ${playlist.name}`, 'success');
      } catch (err) {
        window.showToast?.(err instanceof Error ? err.message : 'Failed to remove', 'error');
      }
    },
    [request.tracks],
  );

  const create = useCallback(async () => {
    const name = (newName ?? '').trim();
    if (!name) return;
    setSaving(true);
    try {
      await createUserPlaylist(name, request.tracks);
      closeAddToPlaylist();
      window.showToast?.(`Made ${name} with ${describe(request.tracks)} in it`, 'success');
    } catch (err) {
      setSaving(false);
      window.showToast?.(err instanceof Error ? err.message : 'Failed to make playlist', 'error');
    }
  }, [newName, request.tracks]);

  const likedRow = (playlists ?? []).find((p) => p.liked);
  const liked = likedRow ? memberState(likedRow.id, membership) === 'all' : false;

  // the heart stays open on purpose: liking is a toggle, not a destination
  const toggleLike = useCallback(async () => {
    setLiking(true);
    try {
      if (liked) {
        await unlikeSongs(request.tracks);
        window.showToast?.('Removed from Liked Songs', 'success');
      } else {
        const result = await likeSongs(request.tracks);
        window.showToast?.('Added to Liked Songs', 'success');
        if (!likedRow) {
          setPlaylists((list) => [
            {
              id: result.id,
              name: 'Liked Songs',
              track_count: result.track_count,
              liked: true,
            },
            ...(list ?? []),
          ]);
        }
      }
      setMembership(await fetchMembership(request.tracks));
    } catch (err) {
      window.showToast?.(
        err instanceof Error ? err.message : 'Could not update Liked Songs',
        'error',
      );
    } finally {
      setLiking(false);
    }
  }, [liked, likedRow, request.tracks]);

  // Liked Songs is the heart up top, not a row
  const own = (playlists ?? []).filter((p) => !p.liked);
  const needle = query.trim().toLowerCase();
  const shown = own.filter((p) => !needle || p.name.toLowerCase().includes(needle));
  const art = subjectArt(request.tracks);
  const subject = subjectLines(request.tracks);

  return (
    <div
      ref={ref}
      className={styles.picker}
      style={{ top: `${pos.top}px`, left: `${pos.left}px`, width: `${PICKER_WIDTH}px` }}
      role="dialog"
      aria-label="Add to playlist"
      tabIndex={-1}
      data-popover-layer=""
    >
      {/* the song's own colours bleed through the glass behind the header */}
      <div
        className={styles.glow}
        style={art ? { backgroundImage: `url("${art}")` } : undefined}
        aria-hidden="true"
      />
      <div className={styles.head}>
        {art ? (
          <img className={styles.subjectArt} src={art} alt="" />
        ) : (
          <span className={`${styles.subjectArt} ${styles.subjectBlank}`} aria-hidden="true">
            ♪
          </span>
        )}
        <span className={styles.headText}>
          <span className={styles.eyebrow}>Add to playlist</span>
          <span className={styles.subject}>{subject.title}</span>
          <span className={styles.by}>{subject.by}</span>
        </span>
        <button
          type="button"
          className={`${styles.heart}${liked ? ` ${styles.hearted}` : ''}`}
          aria-pressed={liked}
          aria-label={liked ? 'Remove from Liked Songs' : 'Add to Liked Songs'}
          title={liked ? 'Remove from Liked Songs' : 'Add to Liked Songs'}
          disabled={liking || membership === null}
          onClick={() => void toggleLike()}
        >
          <HeartIcon filled={liked} />
        </button>
      </div>
      {own.length > 5 && (
        <label className={styles.searchWrap}>
          <SearchIcon />
          <input
            className={styles.searchInput}
            type="search"
            placeholder="Find a playlist"
            aria-label="Find a playlist"
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      )}
      {newName === null ? (
        <button
          type="button"
          className={`${styles.row} ${styles.newRow}`}
          disabled={saving}
          onClick={() => setNewName(needle ? query.trim() : '')}
        >
          <span className={`${styles.cover} ${styles.newCover}`}>
            <PlusIcon />
          </span>
          <span className={styles.rowText}>
            <span className={styles.name}>New playlist</span>
            <span className={styles.count}>
              start one with {request.tracks.length === 1 ? 'this song' : 'these songs'}
            </span>
          </span>
        </button>
      ) : (
        <form
          className={styles.create}
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <input
            className={styles.createInput}
            autoFocus
            maxLength={200}
            placeholder="Name your playlist"
            aria-label="New playlist name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button type="submit" className={styles.createBtn} disabled={saving || !newName.trim()}>
            Create
          </button>
        </form>
      )}
      {own.length > 0 && <div className={styles.section}>Your playlists</div>}
      <div className={styles.list}>
        {error ? (
          <div className={styles.note}>{error}</div>
        ) : playlists === null ? (
          <div className={styles.note}>Loading your playlists…</div>
        ) : own.length === 0 ? (
          <div className={styles.note}>No playlists yet. Make one above.</div>
        ) : shown.length === 0 ? (
          <div className={styles.note}>No playlist matches “{query.trim()}”.</div>
        ) : (
          shown.map((p) => {
            const state = memberState(p.id, membership);
            const have = membership?.playlists?.[String(p.id)] ?? 0;
            return (
              <button
                key={p.id}
                type="button"
                className={`${styles.row}${state === 'all' ? ` ${styles.rowIn}` : ''}`}
                disabled={saving}
                title={state === 'all' ? `Already in ${p.name}. Click to take it out` : undefined}
                onClick={() => void (state === 'all' ? untick(p) : pick(p))}
              >
                <PlaylistCover playlist={p} />
                <span className={styles.rowText}>
                  <span className={styles.name}>{p.name}</span>
                  <span className={styles.count}>
                    {state === 'some'
                      ? `${have} of ${membership?.total} already in`
                      : `${p.track_count} song${p.track_count === 1 ? '' : 's'}`}
                  </span>
                </span>
                {state === 'all' ? (
                  <span className={styles.tick} aria-label="Already in this playlist">
                    <CheckIcon />
                  </span>
                ) : (
                  <span className={styles.go} aria-hidden="true">
                    Add
                  </span>
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

export function AddToPlaylistHost() {
  const request = useRequest();
  // a fresh picker per open, so the last one's search and new-name don't linger
  return request ? <Picker key={request.id} request={request} /> : null;
}

const HOST_ID = 'add-to-playlist-root';

/** mount once, and give the vanilla pages the same way in. */
export function mountAddToPlaylistHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  document.body.appendChild(el);
  createRoot(el).render(<AddToPlaylistHost />);
  window.openAddToPlaylist = openAddToPlaylist;
}
