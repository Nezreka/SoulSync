import { useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import {
  albumEnrichServices,
  albumIdBadges,
  albumMatchChips,
  expandedHeaderDetails,
} from '../-artist-detail.enhanced-album';
import { foldUpdatedData, runEnrichmentRequest } from '../-artist-detail.enrich-match';
import {
  deleteLibraryAlbumRequest,
  type DeleteAlbumChoice,
} from '../-artist-detail.manage-actions';
import { refreshReorganizeQueue, reorganizeStateForAlbum } from '../-artist-detail.reorganize';
import { analyzeAlbumReplayGainRequest } from '../-artist-detail.tags-rg';
import { ArtPicker } from './art-picker';
import { BatchTagPreviewModal } from './batch-tag-preview-modal';
import { ManualMatchModal } from './manual-match-modal';
import { ReorganizeModal } from './reorganize-modal';
import { SmartDeleteDialog, ALBUM_DELETE_COPY } from './smart-delete-dialog';

interface Props {
  album: EnhancedAlbum;
  /** Already merged owned + expected-missing rows. */
  rows: EnhancedTrack[];
  artistId: unknown;
  artistName: string;
  /** The admin action row is hidden for everyone else. */
  isAdmin: boolean;
  /** A chosen cover propagates to the album record in the panel's state. */
  onArtApplied: (url: string) => void;
  /** A fresh server copy of this album (after a match/enrich) re-renders the panel. */
  onAlbumPatched: (album: Record<string, unknown>) => void;
  /** The album was deleted — the view drops it and its selections. */
  onAlbumDeleted: () => void;
}

/**
 * The expanded album panel's header (renderExpandedAlbumHeader, library.js:3783).
 *
 * Every action still lives in library.js and is invoked through window; the
 * modals slice ports them. Two of them are handed the button element itself,
 * because they render progress onto it.
 */
export function ExpandedAlbumHeader({
  album,
  rows,
  artistId,
  artistName,
  isAdmin,
  onArtApplied,
  onAlbumDeleted,
  onAlbumPatched,
}: Props) {
  const [artBroken, setArtBroken] = useState(false);
  const [pickingArt, setPickingArt] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [matchingService, setMatchingService] = useState<string | null>(null);

  /**
   * A match/enrich hands back the whole payload; fold it into the loaded data
   * (the mirror IS the object the view renders from) and re-render this panel
   * from its fresh album.
   */
  const applyOutcome = (outcome: {
    updatedData: import('../-artist-detail.enhanced').EnhancedData | null;
  }) => {
    if (!outcome.updatedData) return;
    const fresh = foldUpdatedData(
      (window.artistDetailPageState?.enhancedData ?? null) as
        | import('../-artist-detail.enhanced').EnhancedData
        | null,
      outcome.updatedData,
      album.id,
    );
    if (fresh) onAlbumPatched(fresh);
  };
  const genres = Array.isArray(album.genres) ? album.genres : [];
  const badges = albumIdBadges(album);
  const chips = albumMatchChips(album);

  /** deleteLibraryAlbum (library.js:4020): request, toast, then drop the album. */
  const performDelete = async (choice: DeleteAlbumChoice) => {
    setConfirmingDelete(false);
    try {
      const toast = await deleteLibraryAlbumRequest(album.id, choice);
      window.showToast?.(toast.message, toast.tone);
      onAlbumDeleted();
    } catch (error) {
      window.showToast?.(`Delete failed: ${(error as Error).message}`, 'error');
    }
  };

  return (
    <div className="enhanced-expanded-header">
      {pickingArt ? (
        <ArtPicker
          target={{
            kind: 'album',
            id: album.id,
            artistName,
            albumTitle: String(album.title || ''),
          }}
          subtitle={String(album.title || '') + (artistName ? ' · ' + artistName : '')}
          onApplied={(url) => {
            setArtBroken(false);
            onArtApplied(url);
          }}
          onClose={() => setPickingArt(false)}
        />
      ) : null}
      {matchingService ? (
        <ManualMatchModal
          entityType="album"
          entityId={album.id}
          service={matchingService}
          defaultQuery={String(album.title || '')}
          artistId={artistId}
          onUpdated={applyOutcome}
          onClose={() => setMatchingService(null)}
        />
      ) : null}
      {confirmingDelete ? (
        <SmartDeleteDialog
          copy={ALBUM_DELETE_COPY}
          onChoose={(choice) => void performDelete(choice as DeleteAlbumChoice)}
          onClose={() => setConfirmingDelete(false)}
        />
      ) : null}
      <div
        className="enhanced-expanded-art-wrap"
        title="Change cover art"
        onClick={() => setPickingArt(true)}
      >
        {/* The vanilla hid a broken cover rather than removing it, so the
            wrap keeps its size and the click target does not collapse. */}
        <img
          className="enhanced-expanded-art"
          src={album.thumb_url ? String(album.thumb_url) : undefined}
          alt={String(album.title || '')}
          style={{ visibility: artBroken ? 'hidden' : undefined }}
          onError={() => setArtBroken(true)}
        />
        <div className="enhanced-art-edit-overlay">
          <svg
            viewBox="0 0 24 24"
            width="26"
            height="26"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <path d="m21 15-5-5L5 21" />
          </svg>
          <span>Change cover</span>
        </div>
      </div>

      <div className="enhanced-expanded-info">
        <div className="enhanced-expanded-title">{String(album.title || 'Unknown')}</div>
        <div className="enhanced-expanded-meta">{expandedHeaderDetails(album, rows)}</div>

        {genres.length > 0 ? (
          <div className="enhanced-expanded-genres">
            {genres.map((genre) => (
              <span className="enhanced-genre-tag" key={String(genre)}>
                {String(genre)}
              </span>
            ))}
          </div>
        ) : null}

        {badges.length > 0 ? (
          <div className="enhanced-expanded-ids">
            {badges.map((badge) =>
              badge.url ? (
                <a
                  key={badge.service}
                  className={badge.className}
                  href={badge.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={badge.title}
                  // Without this the row's own click handler collapses the
                  // album out from under the link.
                  onClick={(e) => e.stopPropagation()}
                >
                  {badge.label}
                </a>
              ) : (
                <span key={badge.service} className={badge.className} title={badge.title}>
                  {badge.label}
                </span>
              ),
            )}
          </div>
        ) : null}

        <div className="enhanced-match-status-row compact">
          {chips.map((chip) => (
            <span
              key={chip.service}
              className={chip.className}
              title={chip.title}
              onClick={(e) => {
                e.stopPropagation();
                setMatchingService(chip.service);
              }}
            >
              {chip.label}: {chip.status}
            </span>
          ))}
        </div>

        <div className="enhanced-expanded-actions">
          {isAdmin ? (
            <AdminAlbumActions
              album={album}
              artistId={artistId}
              artistName={artistName}
              onDelete={() => setConfirmingDelete(true)}
              onEnrichOutcome={applyOutcome}
            />
          ) : null}

          {/* Reporting an issue is open to every user, not just admins. */}
          <button
            type="button"
            className="enhanced-report-issue-btn"
            title="Report a problem with this album"
            onClick={(e) => {
              e.stopPropagation();
              window.showReportIssueModal?.(
                'album',
                album.id,
                String(album.title || ''),
                artistName,
              );
            }}
          >
            ⚑ Report Issue
          </button>
        </div>
      </div>
    </div>
  );
}

function AdminAlbumActions({
  album,
  artistId,
  artistName,
  onDelete,
  onEnrichOutcome,
}: {
  album: EnhancedAlbum;
  artistId: unknown;
  artistName: string;
  onDelete: () => void;
  onEnrichOutcome: (outcome: {
    updatedData: import('../-artist-detail.enhanced').EnhancedData | null;
  }) => void;
}) {
  const [enrichOpen, setEnrichOpen] = useState(false);
  const [taggingTracks, setTaggingTracks] = useState<unknown[] | null>(null);
  const [rgBusy, setRgBusy] = useState(false);
  const [reorganizing, setReorganizing] = useState(false);

  return (
    <>
      <div className="enhanced-enrich-wrap">
        <button
          type="button"
          className="enhanced-enrich-btn small"
          onClick={(e) => {
            e.stopPropagation();
            setEnrichOpen((open) => !open);
          }}
        >
          Enrich Album ▾
        </button>
        <div className={`enhanced-enrich-menu${enrichOpen ? ' visible' : ''}`}>
          {albumEnrichServices().map((service) => (
            <div
              className="enhanced-enrich-menu-item"
              key={service.id}
              onClick={(e) => {
                e.stopPropagation();
                setEnrichOpen(false);
                void runEnrichmentRequest({
                  entityType: 'album',
                  entityId: album.id,
                  service: service.id,
                  name: String(album.title || ''),
                  artistName,
                  artistId,
                }).then(onEnrichOutcome);
              }}
            >
              {service.icon} {service.label}
            </div>
          ))}
        </div>
      </div>

      <button
        type="button"
        className="enhanced-write-tags-album-btn"
        title="Write DB metadata to file tags for all tracks in this album"
        onClick={(e) => {
          e.stopPropagation();
          // writeAlbumTags (5449): only tracks that actually have a file.
          const withFiles = (album.tracks ?? [])
            .filter((t) => (t as { file_path?: string }).file_path)
            .map((t) => t.id);
          if (withFiles.length === 0) {
            window.showToast?.('No tracks with files in this album', 'error');
            return;
          }
          setTaggingTracks(withFiles);
        }}
      >
        ✎ Write All Tags
      </button>
      {taggingTracks ? (
        <BatchTagPreviewModal
          trackIds={taggingTracks}
          albumTitle={String(album.title || '')}
          onClose={() => setTaggingTracks(null)}
        />
      ) : null}

      <button
        type="button"
        className="enhanced-rg-album-btn"
        title="Analyze ReplayGain for all tracks in this album (writes track + album gain)"
        data-album-id={String(album.id)}
        onClick={(e) => {
          e.stopPropagation();
          if (rgBusy) return;
          setRgBusy(true);
          void analyzeAlbumReplayGainRequest(album.id, () => setRgBusy(false));
        }}
      >
        {rgBusy ? '♫ Analyzing…' : '♫ ReplayGain'}
      </button>

      <button
        type="button"
        className="enhanced-reorganize-album-btn"
        title="Reorganize album files using your configured download template"
        data-album-id={String(album.id)}
        onClick={(e) => {
          e.stopPropagation();
          // Already queued/running: opening the modal would be misleading —
          // the apply click would just dedupe (showReorganizeModal, 5846).
          const queuedState = reorganizeStateForAlbum(album.id);
          if (queuedState) {
            window.showToast?.(
              queuedState === 'running'
                ? 'Reorganize already running for this album'
                : 'Album already queued for reorganize',
              'info',
            );
            void refreshReorganizeQueue();
            return;
          }
          setReorganizing(true);
        }}
      >
        📁 Reorganize
      </button>
      {reorganizing ? (
        <ReorganizeModal album={album} onClose={() => setReorganizing(false)} />
      ) : null}

      <button
        type="button"
        className="enhanced-redownload-album-btn"
        title="Redownload this album (opens Download Missing modal with force-download)"
        onClick={(e) => {
          e.stopPropagation();
          window.redownloadLibraryAlbum?.(album, artistName, e.currentTarget);
        }}
      >
        ↻ Redownload
      </button>

      <button
        type="button"
        className="enhanced-delete-album-btn"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        Delete Album
      </button>
    </>
  );
}
