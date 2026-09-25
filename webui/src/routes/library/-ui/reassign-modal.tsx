import { useEffect, useState } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';

import type { ReassignAlbum, ReassignArtist, ReassignPreview, ReassignSource } from './reassign';

import styles from './library-v2-page.module.css';
import {
  albumBits,
  applyReassign,
  describeMapping,
  describeMatch,
  fetchReassignAlbums,
  fetchReassignSources,
  previewReassign,
  reassignSubject,
  searchReassignArtists,
} from './reassign';

/**
 * Reassign an album to a different artist.
 *
 * Three steps in the order the user thinks about it: find the ARTIST it should
 * belong to, pick one of THEIR releases, review how the tracks line up.
 *
 * Steps 1 and 2 are the safety property. You never type an artist name — you
 * pick a real one and then a real release of theirs, so the identity handed to
 * the import pipeline is one the source can actually resolve.
 *
 * Step 3 exists because an album is many files. A silent wrong guess is many
 * misfiled tracks, so the mapping is shown — including WHY each pairing was
 * proposed and which files could not be placed — before anything is staged.
 *
 * LAYOUT: this is the album-scale sibling of the re-identify modal and uses its
 * chassis exactly. `.reid-modal` is a flex COLUMN whose children are hero /
 * tabs / search / `.reid-results` / footer as SIBLINGS. `.reid-results` is the
 * scroll region and holds only the step's content — an earlier version nested
 * the tabs and search bar inside it, which scrolled the controls away with the
 * list and is why it read as makeshift.
 *
 * It reaches Library v2 from the album overflow menu. `albumId` is a lib2 row
 * id and is sent as such — see `reassignSubject`.
 */

type Step = 'artist' | 'album' | 'preview';

const STEP_ORDER: Step[] = ['artist', 'album', 'preview'];
const STEP_LABELS: Record<Step, string> = {
  artist: 'Artist',
  album: 'Release',
  preview: 'Review',
};

/** A remote URL is interpolated into a CSS `url('...')` string, so a quote or
 *  a backslash in it would terminate the literal early and let the rest be read
 *  as CSS. Provider image URLs are third-party data (frontend-audit FE-10).
 *  `CSS.escape` is the wrong tool -- it escapes identifiers, not URL strings. */
function cssUrl(url: string): string {
  return `url('${url
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/[\n\r]/g, '')}')`;
}

export function ReassignModal({
  albumId,
  albumTitle,
  currentArtist,
  onClose,
  onApplied,
}: {
  /** Library-v2 album row id. */
  albumId: number;
  albumTitle: string;
  currentArtist: string;
  imageUrl?: string;
  onClose: () => void;
  onApplied?: () => void;
}) {
  const [sources, setSources] = useState<ReassignSource[] | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [step, setStep] = useState<Step>('artist');

  const [query, setQuery] = useState('');
  const [artists, setArtists] = useState<ReassignArtist[] | null>(null);
  const [artist, setArtist] = useState<ReassignArtist | null>(null);

  const [albums, setAlbums] = useState<ReassignAlbum[] | null>(null);
  const [album, setAlbum] = useState<ReassignAlbum | null>(null);

  const [preview, setPreview] = useState<ReassignPreview | null>(null);
  const [replace, setReplace] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchReassignSources().then((list) => {
      if (!alive) return;
      // The endpoint flags the active source — pre-select THAT rather than
      // whichever happens to be first.
      setSources(list);
      setSource((current) => current ?? list.find((s) => s.active)?.id ?? list[0]?.id ?? null);
    });
    return () => {
      alive = false;
    };
  }, []);

  const runSearch = async () => {
    if (!source || !query.trim()) return;
    setBusy(true);
    try {
      setArtists(await searchReassignArtists(source, query));
    } finally {
      setBusy(false);
    }
  };

  const pickArtist = async (picked: ReassignArtist) => {
    setArtist(picked);
    setAlbums(null);
    setStep('album');
    setBusy(true);
    try {
      setAlbums(await fetchReassignAlbums(source as string, picked.id));
    } finally {
      setBusy(false);
    }
  };

  const pickAlbum = async (picked: ReassignAlbum) => {
    setAlbum(picked);
    setPreview(null);
    setStep('preview');
    setBusy(true);
    try {
      setPreview(
        await previewReassign({
          source: source as string,
          local_album_id: reassignSubject(albumId),
          album_id: picked.id,
        }),
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!album || !artist || !source) return;
    setBusy(true);
    try {
      const result = await applyReassign({
        source,
        local_album_id: reassignSubject(albumId),
        album_id: album.id,
        album_name: album.name,
        artist_id: artist.id,
        artist_name: artist.name,
        album_type: album.album_type ?? null,
        replace,
        // Only ever true because the preview above showed the user exactly
        // which tracks would be left behind.
        allow_partial: (preview?.unmapped_count ?? 0) > 0,
      });
      if (!result.success) {
        window.showToast?.(result.error || 'Reassign failed', 'error');
        return;
      }
      const moved = result.staged?.length ?? 0;
      const left = (result.failed?.length ?? 0) + (result.skipped?.length ?? 0);
      window.showToast?.(
        left
          ? `Staged ${moved} tracks — ${left} left where they were. Import them to finish.`
          : `Staged ${moved} tracks. Import them to finish the reassign.`,
        left ? 'info' : 'success',
      );
      onApplied?.();
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const stepIndex = STEP_ORDER.indexOf(step);
  const canApply =
    step === 'preview' && !busy && Boolean(preview?.success) && Boolean(preview?.mapped_count);

  return (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={`reid-modal ${styles.modalFramed} ${styles.reassignDialog}`}
    >
      <DialogHeader title={`Reassign Album — ${albumTitle || 'Album'}`} closeLabel="Close" compact>
        <span className={styles.toolDescription}>Currently filed under {currentArtist}</span>
      </DialogHeader>
      {/* Where you are in the flow. Three steps is enough to lose your place
            in, and the release/review steps have no other chrome. */}
      <div className="reassign-steps">
        {STEP_ORDER.map((s, index) => (
          <div
            key={s}
            className={`reassign-step${index === stepIndex ? ' active' : ''}${
              index < stepIndex ? ' done' : ''
            }`}
          >
            <span className="reassign-step-num">{index < stepIndex ? '✓' : index + 1}</span>
            {STEP_LABELS[s]}
          </div>
        ))}
      </div>

      {step === 'artist' ? (
        <>
          <div className="reid-tabs">
            {sources && sources.length === 0 ? (
              <span className="reid-tab active">No metadata sources available</span>
            ) : (
              (sources ?? []).map((s) => (
                <div
                  key={s.id}
                  className={`reid-tab${s.id === source ? ' active' : ''}`}
                  onClick={() => setSource(s.id)}
                >
                  {s.label}
                </div>
              ))
            )}
          </div>

          <div className="reid-search">
            <svg className="reid-search-icon" viewBox="0 0 24 24" width="18" height="18">
              <path
                fill="currentColor"
                d="M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"
              />
            </svg>
            <input
              type="text"
              className="reid-search-input"
              placeholder="Search for the correct artist…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void runSearch();
              }}
            />
            <button type="button" className="reid-search-btn" onClick={() => void runSearch()}>
              Search
            </button>
          </div>
        </>
      ) : null}

      <div className="reid-results">
        {step === 'artist' && busy ? (
          <div className="reid-state">
            <div className="reid-spinner" />
          </div>
        ) : null}

        {step === 'artist' && !busy && artists === null ? (
          <div className="reid-state">
            <div className="reid-state-icon">🎤</div>
            <p>
              Search for the artist this album should belong to — a featured artist is often picked
              up as the album artist by mistake.
            </p>
          </div>
        ) : null}

        {step === 'artist' && !busy && artists?.length === 0 ? (
          <div className="reid-state">
            <div className="reid-state-icon">🔍</div>
            <p>No artists matched that search on this source.</p>
          </div>
        ) : null}

        {step === 'artist' && !busy
          ? (artists ?? []).map((row) => (
              <div key={row.id} className="reid-result" onClick={() => void pickArtist(row)}>
                <div
                  className={`reid-result-art${row.image_url ? '' : ' empty'}`}
                  style={row.image_url ? { backgroundImage: cssUrl(row.image_url) } : undefined}
                />
                <div className="reid-result-info">
                  <div className="reid-result-title">{row.name}</div>
                  <div className="reid-result-release">Pick to see their releases</div>
                </div>
                <div className="reid-result-meta">
                  <span className="reid-result-check" />
                </div>
              </div>
            ))
          : null}

        {step === 'album' && busy ? (
          <div className="reid-state">
            <div className="reid-spinner" />
          </div>
        ) : null}

        {step === 'album' && !busy && albums?.length === 0 ? (
          <div className="reid-state">
            <div className="reid-state-icon">💿</div>
            <p>No releases found for {artist?.name} on this source.</p>
          </div>
        ) : null}

        {step === 'album' && !busy
          ? (albums ?? []).map((row) => (
              <div key={row.id} className="reid-result" onClick={() => void pickAlbum(row)}>
                <div
                  className={`reid-result-art${row.image_url ? '' : ' empty'}`}
                  style={row.image_url ? { backgroundImage: cssUrl(row.image_url) } : undefined}
                />
                <div className="reid-result-info">
                  <div className="reid-result-title">{row.name}</div>
                  <div className="reid-result-release">{albumBits(row) || 'release'}</div>
                </div>
                <div className="reid-result-meta">
                  <span className="reid-result-check" />
                </div>
              </div>
            ))
          : null}

        {step === 'preview' && busy ? (
          <div className="reid-state">
            <div className="reid-spinner" />
          </div>
        ) : null}

        {step === 'preview' && !busy && preview && !preview.success ? (
          <div className="reid-state">
            <div className="reid-state-icon">⚠️</div>
            <p>{preview.error}</p>
          </div>
        ) : null}

        {step === 'preview' && !busy && preview?.success ? (
          <>
            <div className={`reassign-summary${preview.unmapped_count ? ' warn' : ''}`}>
              {describeMapping(preview)}
            </div>
            {(preview.pairings ?? []).map((p, index) => (
              <div
                key={`${String(p.local_id)}-${index}`}
                className={`reassign-row${p.mapped ? '' : ' unmapped'}`}
              >
                <span className="reassign-row-local">
                  {p.local_track_number ? `${p.local_track_number}. ` : ''}
                  {p.local_title}
                </span>
                <span className="reassign-row-arrow">{p.mapped ? '→' : '✕'}</span>
                <span className="reassign-row-target">
                  {p.mapped ? p.target_title : 'stays with the current artist'}
                </span>
                <span className="reassign-row-why">{describeMatch(p)}</span>
              </div>
            ))}
          </>
        ) : null}
      </div>

      <div className="reid-footer">
        <label className="reid-replace">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          <span className="reid-replace-box" />
          <span className="reid-replace-text">Replace the originals after the re-import</span>
        </label>
        <div className="reid-footer-actions">
          {step !== 'artist' ? (
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => setStep(step === 'preview' ? 'album' : 'artist')}
            >
              Back
            </button>
          ) : null}
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canApply}
            onClick={() => void apply()}
          >
            {busy ? 'Working…' : 'Reassign'}
          </button>
        </div>
      </div>
    </DialogFrame>
  );
}
