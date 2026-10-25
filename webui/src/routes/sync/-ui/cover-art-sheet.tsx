/**
 * "generate cover art" from a playlist's ⋯ menu. opens straight into making
 * one, shows it as a preview, and only "use this cover" puts it on the
 * playlist. try another rotates the art style. the free tier is one image
 * every 45s, so a refusal turns the button into a countdown instead of an
 * error you have to read.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAccessibleModal } from '@/components/dialog';

import {
  applyCoverArt,
  CoverArtError,
  type CoverArtPreview,
  discardCoverArtPreview,
  generateCoverArt,
  setCoverArtName,
  vibeLine,
} from '../../../features/playlists/cover-art';

export interface CoverArtSheetProps {
  playlistId: number;
  playlistName: string;
  onClose: () => void;
  /** the cover is on the playlist now: refetch the detail. */
  onApplied: () => void;
}

/** the server's message without its own "try again in Ns", the button counts down instead. */
export function withoutRetryHint(message: string): string {
  return message.replace(/\s*Try again in \d+s\.?\s*$/i, '').trim();
}

export function CoverArtSheet({
  playlistId,
  playlistName,
  onClose,
  onApplied,
}: CoverArtSheetProps) {
  const [preview, setPreview] = useState<CoverArtPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readyAt, setReadyAt] = useState<number | null>(null);
  // the playlist's name drawn on the cover. a ref too, so generate reads the
  // current choice without being rebuilt (that would re-run the first generate)
  const [showName, setShowName] = useState(true);
  const showNameRef = useRef(true);
  const [renaming, setRenaming] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const attempt = useRef(0);
  const alive = useRef(true);
  // the apply owns the preview from here: closing mid-save must not throw it away
  const saving = useRef(false);
  const madePreview = useRef(false);

  const close = useCallback(() => {
    if (madePreview.current && !saving.current) void discardCoverArtPreview(playlistId);
    onClose();
  }, [onClose, playlistId]);
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(close);

  const generate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await generateCoverArt(playlistId, attempt.current, showNameRef.current);
      if (!alive.current) return;
      madePreview.current = true;
      attempt.current += 1;
      setPreview(result);
      // keyed: no wait. free: the next one is 45s out, show that up front
      const at = Date.now();
      setNow(at);
      setReadyAt(result.keyed ? null : at + 46_000);
    } catch (err) {
      if (!alive.current) return;
      const wait = err instanceof CoverArtError ? err.retryAfter : null;
      setError(withoutRetryHint(err instanceof Error ? err.message : "Couldn't make a cover"));
      const at = Date.now();
      setNow(at);
      setReadyAt(wait ? at + wait * 1000 : null);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [playlistId]);

  // once per open. strict mode runs effects twice, and a second free call
  // inside 45s would just come back refused
  const started = useRef(false);
  useEffect(() => {
    alive.current = true;
    if (!started.current) {
      started.current = true;
      void generate();
    }
    return () => {
      alive.current = false;
    };
  }, [generate]);

  // tick only while there's a countdown to show
  const waiting = readyAt !== null && readyAt > now;
  useEffect(() => {
    if (readyAt === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [readyAt]);
  const secondsLeft = waiting && readyAt !== null ? Math.ceil((readyAt - now) / 1000) : 0;

  const toggleName = async (next: boolean) => {
    showNameRef.current = next;
    setShowName(next);
    if (!preview) return;
    setRenaming(true);
    setError(null);
    try {
      const result = await setCoverArtName(playlistId, next);
      if (!alive.current) return;
      setPreview((current) => (current ? { ...current, ...result } : current));
    } catch (err) {
      if (!alive.current) return;
      showNameRef.current = !next;
      setShowName(!next);
      setError(err instanceof Error ? err.message : "Couldn't change the cover");
    } finally {
      if (alive.current) setRenaming(false);
    }
  };

  const use = async () => {
    if (!preview || applying) return;
    saving.current = true;
    setApplying(true);
    setError(null);
    try {
      await applyCoverArt(playlistId);
      onApplied();
      onClose();
    } catch (err) {
      saving.current = false;
      setError(err instanceof Error ? err.message : "Couldn't save the cover");
    } finally {
      if (alive.current) setApplying(false);
    }
  };

  const made = vibeLine(preview?.vibe);
  const busy = loading || applying || renaming;

  return createPortal(
    <div className="cover-art-overlay" onClick={onBackdropClick}>
      <div
        ref={ref}
        className="cover-art-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cover-art-title"
      >
        <div className="cover-art-head">
          <div>
            <h3 id="cover-art-title">Cover art</h3>
            <p className="cover-art-for">{playlistName}</p>
          </div>
          <button type="button" className="cover-art-close" aria-label="Close" onClick={close}>
            &times;
          </button>
        </div>

        <div className="cover-art-frame" aria-busy={loading || undefined}>
          {preview ? (
            <img
              className={`cover-art-img${loading ? ' cover-art-img--dim' : ''}`}
              src={preview.preview_url}
              alt="Generated cover preview"
            />
          ) : null}
          {loading ? (
            <div className="cover-art-status">
              <span className="cover-art-spin" aria-hidden="true" />
              <span>Painting…</span>
            </div>
          ) : !preview && error ? (
            <div className="cover-art-status cover-art-status--error">{error}</div>
          ) : null}
        </div>

        <p className="cover-art-vibe">
          {preview && error ? (
            <span className="cover-art-inline-error">{error}</span>
          ) : preview ? (
            made ? (
              `Made from ${made}`
            ) : (
              "Made from the playlist's name"
            )
          ) : loading ? (
            'Reading the playlist for genres, mood and era'
          ) : (
            ''
          )}
        </p>

        <label className="cover-art-name">
          <span>Show name on cover</span>
          <input
            type="checkbox"
            role="switch"
            checked={showName}
            disabled={loading || applying || renaming}
            onChange={(event) => void toggleName(event.target.checked)}
          />
        </label>

        <div className="cover-art-actions">
          <button
            type="button"
            className="cover-art-btn cover-art-btn--ghost"
            disabled={busy || waiting}
            onClick={() => void generate()}
          >
            {`${preview ? 'Try another' : 'Try again'}${waiting ? ` in ${secondsLeft}s` : ''}`}
          </button>
          <button
            type="button"
            className="cover-art-btn cover-art-btn--primary"
            disabled={!preview || busy}
            onClick={() => void use()}
          >
            {applying ? 'Saving…' : 'Use this cover'}
          </button>
        </div>

        {preview && !preview.keyed ? (
          <p className="cover-art-note">
            Free images carry a small Pollinations mark and come one every 45 seconds. A key in
            Settings → Pollinations removes both.
          </p>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
