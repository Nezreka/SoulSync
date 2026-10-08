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

import { usePopoverDismiss } from '@/routes/sync/-ui/use-popover-dismiss';
import { usePopoverPosition } from '@/routes/sync/-ui/use-popover-position';

import type { UserPlaylistSummary, UserPlaylistTrack } from './user-playlists';

import styles from './add-to-playlist.module.css';
import { addToUserPlaylist, createUserPlaylist, fetchUserPlaylists } from './user-playlists';

declare global {
  interface Window {
    /** open the add-to-playlist picker. anchor is the button it pops from. */
    openAddToPlaylist?: (
      tracks: UserPlaylistTrack | UserPlaylistTrack[],
      anchor?: HTMLElement | null,
    ) => void;
  }
}

const PICKER_WIDTH = 300;

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

/** add, ask about duplicates, say what happened. exported for the tests. */
export async function addAndReport(
  playlist: { id: number; name: string },
  tracks: UserPlaylistTrack[],
): Promise<void> {
  const result = await addToUserPlaylist(playlist.id, tracks);
  const dupes = result.duplicates ?? [];
  if (dupes.length > 0) {
    const which =
      dupes.length === 1 ? `"${dupes[0].track_name}" is` : `${dupes.length} of these songs are`;
    const again = await window.showConfirmDialog?.({
      title: 'Already added',
      message: `${which} already in ${playlist.name}.`,
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

function Picker({ request }: { request: Request }) {
  const ref = useRef<HTMLDivElement>(null);
  const pos = usePopoverPosition({ top: request.top, left: request.left }, ref);
  const [playlists, setPlaylists] = useState<UserPlaylistSummary[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [newName, setNewName] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
    return () => {
      live = false;
    };
  }, []);

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

  const needle = query.trim().toLowerCase();
  const shown = (playlists ?? []).filter((p) => !needle || p.name.toLowerCase().includes(needle));

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
      <div className={styles.head}>
        <span className={styles.title}>Add to playlist</span>
        <span className={styles.subject}>{describe(request.tracks)}</span>
      </div>
      {(playlists?.length ?? 0) > 5 && (
        <input
          className={styles.search}
          type="search"
          placeholder="Find a playlist"
          aria-label="Find a playlist"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
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
          <span className={styles.name}>New playlist</span>
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
            className={styles.search}
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
      <div className={styles.list}>
        {error ? (
          <div className={styles.note}>{error}</div>
        ) : playlists === null ? (
          <div className={styles.note}>Loading your playlists…</div>
        ) : playlists.length === 0 ? (
          <div className={styles.note}>No playlists yet. Make one above.</div>
        ) : shown.length === 0 ? (
          <div className={styles.note}>No playlist matches “{query.trim()}”.</div>
        ) : (
          shown.map((p) => (
            <button
              key={p.id}
              type="button"
              className={styles.row}
              disabled={saving}
              onClick={() => void pick(p)}
            >
              {p.image_url ? (
                <img className={styles.cover} src={p.image_url} alt="" loading="lazy" />
              ) : (
                <span className={styles.cover} aria-hidden="true">
                  {p.name.slice(0, 1).toUpperCase()}
                </span>
              )}
              <span className={styles.rowText}>
                <span className={styles.name}>{p.name}</span>
                <span className={styles.count}>
                  {p.track_count} song{p.track_count === 1 ? '' : 's'}
                </span>
              </span>
            </button>
          ))
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
