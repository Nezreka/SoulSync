/**
 * What's in a playlist before you bring it in (#1934). The source card opens
 * this instead of importing on any click: the songs, then Import or keep your
 * own copy. Nothing is saved until one of those is pressed.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAccessibleModal } from '@/components/dialog';

import { formatDuration } from '../-sync.core';
import styles from './import-preview-sheet.module.css';
import { PlaylistArt } from './playlist-art';

export interface PreviewTrack {
  name: string;
  artist: string;
  album: string;
  durationMs: number | null;
}

/** one row from whatever shape a source's tracks come in */
export function previewTrack(raw: unknown): PreviewTrack {
  const t = (raw ?? {}) as Record<string, unknown>;
  const text = (v: unknown) => (typeof v === 'string' ? v : '');
  const named = (v: unknown) =>
    typeof v === 'string' ? v : text((v as { name?: unknown } | null)?.name);
  const artists = Array.isArray(t.artists)
    ? t.artists.map(named).filter(Boolean).join(', ')
    : text(t.artists);
  const duration = Number(t.duration_ms);
  return {
    name: text(t.track_name) || text(t.name) || text(t.title) || 'Untitled',
    artist: text(t.artist_name) || artists || text(t.artist),
    album: text(t.album_name) || named(t.album),
    durationMs: Number.isFinite(duration) && duration > 0 ? duration : null,
  };
}

export interface ImportPreviewSheetProps {
  name: string;
  meta: string;
  artUrl?: string | null;
  glyph?: string;
  load: () => Promise<PreviewTrack[]>;
  onImport: () => void;
  onCopy?: () => void;
  onClose: () => void;
}

export function ImportPreviewSheet({
  name,
  meta,
  artUrl,
  glyph,
  load,
  onImport,
  onCopy,
  onClose,
}: ImportPreviewSheetProps) {
  const [tracks, setTracks] = useState<PreviewTrack[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const close = useCallback(() => onClose(), [onClose]);
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(close);

  // once per opening: the card hands a fresh loader on every render
  const loadRef = useRef(load);
  useEffect(() => {
    let alive = true;
    loadRef
      .current()
      .then((rows) => alive && setTracks(rows))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "Couldn't load the songs");
      });
    return () => {
      alive = false;
    };
  }, []);

  return createPortal(
    // a portal's events still bubble to the card that opened it, which would
    // open it again: they stop here
    <div
      className={styles.overlay}
      onClick={(e) => {
        e.stopPropagation();
        onBackdropClick(e);
      }}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <div
        ref={ref}
        className={styles.sheet}
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-preview-title"
      >
        <header className={styles.head}>
          <span className={styles.art}>
            <PlaylistArt url={artUrl} glyph={glyph ?? '🎵'} />
          </span>
          <div className={styles.titles}>
            <h3 id="import-preview-title">{name}</h3>
            <p>{tracks ? `${tracks.length} ${tracks.length === 1 ? 'song' : 'songs'}` : meta}</p>
          </div>
          <button type="button" className={styles.close} aria-label="Close" onClick={close}>
            &times;
          </button>
        </header>

        <div className={styles.body} aria-busy={tracks === null && !error ? true : undefined}>
          {error ? (
            <p className={styles.state}>{error}</p>
          ) : tracks === null ? (
            <p className={styles.state}>
              <span className={styles.spinner} aria-hidden="true" />
              Loading the songs…
            </p>
          ) : tracks.length === 0 ? (
            <p className={styles.state}>This playlist is empty.</p>
          ) : (
            <ol className={styles.list}>
              {tracks.map((t, i) => (
                <li key={i} className={styles.row}>
                  <span className={styles.num}>{i + 1}</span>
                  <span className={styles.track}>
                    <span className={styles.trackName}>{t.name}</span>
                    <span className={styles.trackSub}>
                      {[t.artist, t.album].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <span className={styles.time}>
                    {t.durationMs ? formatDuration(t.durationMs) : ''}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>

        <footer className={styles.foot}>
          {onCopy ? (
            <button
              type="button"
              className={styles.secondary}
              title="Keep your own copy in My Playlists. It won't refresh from the source, so it's yours to edit."
              onClick={() => {
                onCopy();
                onClose();
              }}
            >
              Keep my own copy
            </button>
          ) : null}
          <button
            type="button"
            className={styles.primary}
            onClick={() => {
              onImport();
              onClose();
            }}
          >
            Import
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
