import { useEffect, useRef, useState } from 'react';

import type { ArtCandidate, ArtPickerTarget } from '../-artist-detail.manage-actions';

import {
  applyArtRequest,
  releaseArtRequest,
  artistArtAppliedMessage,
  fetchArtOptions,
} from '../-artist-detail.manage-actions';

/**
 * The cover-art / artist-photo picker (openAlbumArtPicker library.js:1669,
 * openArtistArtPicker 1836) — one component, parameterized by target. The
 * artist variant leads its grid with the CURRENT photo as a display-only
 * reference tile (the DB often stores a local cache path, which must never be
 * re-applied as if it were a source URL) and notes that applying writes to
 * SoulSync, the media server, and artist.jpg on disk.
 */

/** `setTimeout(..., 350)` on the custom-URL input (1812). */
export const ART_CUSTOM_URL_DEBOUNCE_MS = 350;

interface Props {
  target: ArtPickerTarget;
  /** The current artist photo for the reference tile (artist variant only). */
  currentUrl?: string | null;
  subtitle: string;
  onApplied: (url: string) => void;
  onClose: () => void;
}

type Grid =
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'loaded'; candidates: ArtCandidate[] };

export function ArtPicker({ target, currentUrl, subtitle, onApplied, onClose }: Props) {
  const [grid, setGrid] = useState<Grid>({ kind: 'loading' });
  /** Tiles whose image failed to load remove themselves (1737-1747). */
  const [dead, setDead] = useState<Set<string>>(() => new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [customUrl, setCustomUrl] = useState('');
  const [customTile, setCustomTile] = useState<string | null>(null);
  const [customError, setCustomError] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchArtOptions(target)
      .then((candidates) => {
        if (!cancelled) setGrid({ kind: 'loaded', candidates });
      })
      .catch(() => {
        if (!cancelled) setGrid({ kind: 'failed' });
      });
    return () => {
      cancelled = true;
    };
    // The target never changes for a mounted picker.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const live = grid.kind === 'loaded' ? grid.candidates.filter((c) => !dead.has(c.url)) : [];
  const allDead = grid.kind === 'loaded' && grid.candidates.length > 0 && live.length === 0;

  const onCustomInput = (value: string) => {
    setCustomUrl(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const url = value.trim();
      setCustomError(false);
      setCustomTile(/^https?:\/\//i.test(url) ? url : null);
    }, ART_CUSTOM_URL_DEBOUNCE_MS);
  };
  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  /**
   * Hand the image back to the media server. Applying a pick locks it so a
   * library sync can't overwrite it; this is the way out, since the picker can
   * legitimately offer zero alternatives to switch to.
   */
  const release = async () => {
    if (applying || releasing) return;
    setReleasing(true);
    try {
      const result = await releaseArtRequest(target);
      if (result && result.success) {
        window.showToast?.(
          target.kind === 'artist'
            ? 'Following your server’s artist photo again'
            : 'Following your server’s cover art again',
          'success',
        );
        onClose();
        return;
      }
      window.showToast?.((result && result.error) || 'Could not release the image', 'error');
    } catch {
      window.showToast?.('Could not release the image', 'error');
    }
    setReleasing(false);
  };

  const apply = async () => {
    if (!selected || applying) return;
    setApplying(true);
    try {
      const result = await applyArtRequest(target, selected);
      if (result && result.success) {
        window.showToast?.(
          target.kind === 'artist' ? artistArtAppliedMessage(result) : 'Cover art updated',
          'success',
        );
        onApplied(selected);
        onClose();
        return;
      }
      window.showToast?.(
        (result && result.error) ||
          (target.kind === 'artist' ? 'Failed to update photo' : 'Failed to update art'),
        'error',
      );
    } catch {
      window.showToast?.(
        target.kind === 'artist' ? 'Failed to update photo' : 'Failed to update art',
        'error',
      );
    }
    setApplying(false);
  };

  const title = target.kind === 'album' ? 'Choose cover art' : 'Choose artist photo';
  const emptyText =
    target.kind === 'album'
      ? 'No alternative covers found for this album.'
      : 'No photos found on your connected sources for this artist.';

  return (
    <div
      id="art-picker-overlay"
      className="art-picker-overlay visible"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="art-picker-modal" role="dialog" aria-modal="true">
        <div className="art-picker-header">
          <div className="art-picker-titles">
            <div className="art-picker-title">{title}</div>
            <div className="art-picker-subtitle">{subtitle}</div>
          </div>
          <button className="art-picker-close" aria-label="Close" type="button" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="art-picker-body">
          {grid.kind === 'loading' ? (
            <div className="art-picker-grid loading">
              {Array.from({ length: target.kind === 'album' ? 10 : 8 }, (_, i) => (
                <div className="art-picker-skel" key={i} />
              ))}
            </div>
          ) : grid.kind === 'failed' ? (
            <div className="art-picker-empty">
              {target.kind === 'album'
                ? "Couldn't load cover options."
                : "Couldn't load photo options."}
            </div>
          ) : allDead ? (
            <div className="art-picker-empty">
              Sources returned photos, but none of the images would load — try again in a minute.
            </div>
          ) : (
            <>
              {grid.candidates.length > 0 ? (
                <div className="art-picker-grid">
                  {target.kind === 'artist' && currentUrl ? (
                    <div className="art-picker-tile art-picker-tile--current">
                      <img loading="lazy" src={currentUrl} alt="" />
                      <span className="art-picker-badge art-picker-badge--current">current</span>
                    </div>
                  ) : null}
                  {live.map((c) => (
                    <button
                      type="button"
                      key={c.url}
                      className={'art-picker-tile' + (selected === c.url ? ' selected' : '')}
                      onClick={() => setSelected(c.url)}
                    >
                      <img
                        loading="lazy"
                        src={c.url}
                        alt=""
                        onError={() => setDead((prev) => new Set(prev).add(c.url))}
                      />
                      <span className="art-picker-badge">{c.source}</span>
                      <span className="art-picker-check">✓</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="art-picker-empty">{emptyText}</div>
              )}
              <div className="art-picker-custom">
                <input
                  type="url"
                  className="art-picker-url"
                  placeholder="…or paste an image URL"
                  autoComplete="off"
                  value={customUrl}
                  onChange={(e) => onCustomInput(e.target.value)}
                />
                <div className="art-picker-custom-slot">
                  {customError ? (
                    <div className="art-picker-custom-err">Couldn't load that image.</div>
                  ) : customTile ? (
                    <button
                      type="button"
                      className={
                        'art-picker-tile art-picker-tile--custom' +
                        (selected === customTile ? ' selected' : '')
                      }
                      onClick={() => setSelected(customTile)}
                    >
                      <img
                        loading="lazy"
                        src={customTile}
                        alt=""
                        onError={() => {
                          setCustomError(true);
                          setCustomTile(null);
                        }}
                      />
                      <span className="art-picker-badge">custom</span>
                      <span className="art-picker-check">✓</span>
                    </button>
                  ) : null}
                </div>
              </div>
            </>
          )}
        </div>
        <div className="art-picker-footer">
          <div className="art-picker-count">
            {grid.kind === 'loaded' && live.length > 0
              ? target.kind === 'album'
                ? `${live.length} option${live.length === 1 ? '' : 's'}`
                : `${live.length} source${live.length === 1 ? '' : 's'}`
              : ''}
          </div>
          <div className="art-picker-actions">
            <button
              className="art-picker-btn art-picker-release"
              type="button"
              disabled={applying || releasing}
              title="Stop overriding this image — the next library sync restores your media server's own art."
              onClick={release}
            >
              {releasing ? 'Releasing…' : 'Use server art'}
            </button>
            <button className="art-picker-btn art-picker-cancel" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className={'art-picker-btn art-picker-apply' + (applying ? ' loading' : '')}
              type="button"
              disabled={!selected || applying}
              onClick={apply}
            >
              {applying ? 'Applying…' : 'Apply'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
