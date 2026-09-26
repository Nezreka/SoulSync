import { useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import {
  albumEnrichServices,
  albumIdBadges,
  albumMatchChips,
  expandedHeaderDetails,
  getAlbumTrackRows,
  queueTrackPayload,
} from '../-artist-detail.enhanced-album';
import { foldUpdatedData, runEnrichmentRequest } from '../-artist-detail.enrich-match';
import {
  deleteLibraryAlbumRequest,
  type DeleteAlbumChoice,
  unlockHandTaggedAlbumRequest,
} from '../-artist-detail.manage-actions';
import { redownloadAlbumFlow } from '../-artist-detail.redownload';
import { refreshReorganizeQueue, reorganizeStateForAlbum } from '../-artist-detail.reorganize';
import { analyzeAlbumReplayGainRequest } from '../-artist-detail.tags-rg';
import { ActionMenu } from './action-menu';
import { AlbumUpgradeModal } from './album-upgrade-modal';
import { ArtPicker } from './art-picker';
import { BatchTagPreviewModal } from './batch-tag-preview-modal';
import {
  DownloadIcon,
  FlagIcon,
  FolderIcon,
  ImageIcon,
  MoreIcon,
  PencilIcon,
  PlayIcon,
  SparkleIcon,
  SwapIcon,
  TagIcon,
  TrashIcon,
  WaveIcon,
} from './lib-icons';
import { ManualMatchModal } from './manual-match-modal';
import { ReassignModal } from './reassign-modal';
import { ReorganizeModal } from './reorganize-modal';
import { SmartDeleteDialog, ALBUM_DELETE_COPY } from './smart-delete-dialog';
import { monogram, SourceHealth } from './source-health';

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
  /** whether the metadata form under the header is open; admin only. */
  editing?: boolean;
  onToggleEdit?: () => void;
  /** the header's own play button; falls back to the shared player when absent. */
  artist?: Record<string, unknown>;
  /** tracks were moved to another artist; this artist's payload is stale. */
  onReassigned?: () => void;
}

/**
 * the expanded album's header: cover, title, one meta line, genres, the
 * source health row, and one action row led by Play.
 *
 * the seven mixed-colour buttons the row used to carry are now Play, Edit
 * details, Enrich, Reorganize and a More menu; the destructive one sits last
 * in that menu behind a separator. every action still stops propagation, the
 * whole row above is a toggle and a bubbling click would fold the panel.
 */
/** set when the user tagged this album by hand from basic search */
function isHandTagged(album: { metadata_locked?: unknown }): boolean {
  return Number(album.metadata_locked) === 1;
}

export function ExpandedAlbumHeader({
  album,
  rows,
  artistId,
  artistName,
  isAdmin,
  onArtApplied,
  onAlbumDeleted,
  onAlbumPatched,
  editing = false,
  onToggleEdit,
  artist,
  onReassigned,
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
  // one entry per service: the match state from the chip, the outbound link
  // from the id badge when we hold an id for it
  const sources = chips.map((chip) => ({
    service: chip.service,
    label: chip.label,
    status: chip.status,
    title: chip.title,
    url: badges.find((b) => b.service === chip.service)?.url ?? null,
  }));
  const albumTitle = String(album.title || 'Unknown');

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

  const playAlbum = () => {
    const owned = getAlbumTrackRows(album).filter(
      (t) => !(t as { _missingExpected?: boolean })._missingExpected,
    );
    if (!owned.length) {
      window.showToast?.(`No tracks found for ${albumTitle}`, 'info');
      return;
    }
    const tracks = owned.map((track) =>
      queueTrackPayload(track, album, artist ?? { id: artistId, name: artistName }),
    );
    void window.playTrackList?.(tracks, albumTitle);
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
        role="button"
        tabIndex={0}
        onClick={(e) => {
          e.stopPropagation();
          setPickingArt(true);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setPickingArt(true);
          }
        }}
      >
        {/* The vanilla hid a broken cover rather than removing it, so the
            wrap keeps its size and the click target does not collapse. */}
        <img
          className="enhanced-expanded-art"
          src={album.thumb_url ? String(album.thumb_url) : undefined}
          // no alt text without a source, or the browser paints the title
          // over the placeholder note
          alt={album.thumb_url ? String(album.title || '') : ''}
          style={{ visibility: artBroken ? 'hidden' : undefined }}
          onError={() => setArtBroken(true)}
        />
        {!album.thumb_url || artBroken ? (
          <div className="lib-art-empty" aria-hidden="true">
            ♪
          </div>
        ) : null}
        <div className="enhanced-art-edit-overlay">
          <ImageIcon size={22} />
          <span>Change cover</span>
        </div>
      </div>

      <div className="enhanced-expanded-info">
        <div className="lib-eyebrow">
          {String(album.record_type || 'album')}
          {isHandTagged(album) ? ' · hand-tagged' : ''}
        </div>
        <div className="enhanced-expanded-title">{albumTitle}</div>
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

        <SourceHealth
          entries={sources}
          onRematch={isAdmin ? setMatchingService : undefined}
          className="enhanced-match-status-row compact"
        />

        <div className="enhanced-expanded-actions">
          <button
            type="button"
            className="lib-btn primary lib-play-album"
            onClick={(e) => {
              e.stopPropagation();
              playAlbum();
            }}
          >
            <PlayIcon />
            <span>Play</span>
          </button>

          {isAdmin ? (
            <AdminAlbumActions
              album={album}
              artistId={artistId}
              artistName={artistName}
              editing={editing}
              onToggleEdit={onToggleEdit}
              onDelete={() => setConfirmingDelete(true)}
              onReassigned={onReassigned ?? onAlbumDeleted}
              onEnrichOutcome={applyOutcome}
              onReport={() =>
                window.showReportIssueModal?.(
                  'album',
                  album.id,
                  String(album.title || ''),
                  artistName,
                )
              }
            />
          ) : (
            // Reporting an issue is open to every user, not just admins.
            <button
              type="button"
              className="enhanced-report-issue-btn lib-btn quiet"
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
              <FlagIcon />
              <span>Report issue</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function AdminAlbumActions({
  album,
  artistId,
  artistName,
  editing,
  onToggleEdit,
  onDelete,
  onReassigned,
  onEnrichOutcome,
  onReport,
}: {
  album: EnhancedAlbum;
  artistId: unknown;
  artistName: string;
  editing: boolean;
  onToggleEdit?: () => void;
  onDelete: () => void;
  onReassigned: () => void;
  onEnrichOutcome: (outcome: {
    updatedData: import('../-artist-detail.enhanced').EnhancedData | null;
  }) => void;
  onReport: () => void;
}) {
  const [reassigning, setReassigning] = useState(false);
  const [taggingTracks, setTaggingTracks] = useState<unknown[] | null>(null);
  const [rgBusy, setRgBusy] = useState(false);
  const [reorganizing, setReorganizing] = useState(false);
  const [redownloadBusy, setRedownloadBusy] = useState(false);
  const [upgradingAlbum, setUpgradingAlbum] = useState(false);
  const upgradable = Number(album.upgradable_count) || 0;

  const writeAllTags = () => {
    // writeAlbumTags (5449): only tracks that actually have a file.
    const withFiles = (album.tracks ?? [])
      .filter((t) => (t as { file_path?: string }).file_path)
      .map((t) => t.id);
    if (withFiles.length === 0) {
      window.showToast?.('No tracks with files in this album', 'error');
      return;
    }
    setTaggingTracks(withFiles);
  };

  const analyzeReplayGain = () => {
    if (rgBusy) return;
    setRgBusy(true);
    void analyzeAlbumReplayGainRequest(album.id, () => setRgBusy(false));
  };

  const unlockHandTagged = async () => {
    const ok = await window.showConfirmDialog?.({
      title: 'Unlock hand-tagged details?',
      message:
        'You tagged this album yourself. Unlocking lets enrichment and the maintenance jobs match it to a release and change it.',
      confirmText: 'Unlock',
      cancelText: 'Keep locked',
    });
    if (!ok) return;
    if (await unlockHandTaggedAlbumRequest(album.id)) {
      window.showToast?.('Unlocked. Enrichment can change this album again.', 'success');
      onReassigned();
    } else {
      window.showToast?.('Could not unlock this album', 'error');
    }
  };

  const redownload = () => {
    if (redownloadBusy) return;
    setRedownloadBusy(true);
    // #911: pulls the album's CANONICAL edition, then hands off to the
    // shared Download Missing modal (redownloadLibraryAlbum's port).
    void redownloadAlbumFlow(album, artistName)
      .catch((error: Error) => {
        console.error('Redownload album error:', error);
        window.showToast?.(`Error: ${error.message}`, 'error');
      })
      .finally(() => setRedownloadBusy(false));
  };

  return (
    <>
      {onToggleEdit ? (
        <button
          type="button"
          className={`lib-btn lib-edit-details${editing ? ' active' : ''}`}
          title="Edit the album's title, year, label, genres and more"
          onClick={(e) => {
            e.stopPropagation();
            onToggleEdit();
          }}
        >
          <PencilIcon />
          <span>{editing ? 'Done' : 'Edit details'}</span>
        </button>
      ) : null}

      <div className="enhanced-enrich-wrap">
        <ActionMenu
          heading="Pull metadata from"
          items={albumEnrichServices().map((service) => ({
            key: service.id,
            className: 'enhanced-enrich-menu-item',
            icon: <span className="lib-menu-mono">{monogram(service.id, service.label)}</span>,
            label: service.label,
            onSelect: () =>
              void runEnrichmentRequest({
                entityType: 'album',
                entityId: album.id,
                service: service.id,
                name: String(album.title || ''),
                artistName,
                artistId,
              }).then(onEnrichOutcome),
          }))}
          trigger={(t) => (
            <button
              type="button"
              className="enhanced-enrich-btn lib-btn"
              title="Pull fresh metadata for this album from one source"
              {...t}
            >
              <SparkleIcon />
              <span>Enrich</span>
              <span className="lib-btn-caret" aria-hidden="true">
                ▾
              </span>
            </button>
          )}
        />
      </div>

      {upgradable > 0 ? (
        <button
          type="button"
          className="enhanced-upgrade-album-btn lib-btn"
          title="Tracks on this album are below your quality profile. Find better copies."
          onClick={(e) => {
            e.stopPropagation();
            setUpgradingAlbum(true);
          }}
        >
          <span aria-hidden="true">↑</span>
          <span>Upgrade {upgradable}</span>
        </button>
      ) : null}

      <button
        type="button"
        className="enhanced-reorganize-album-btn lib-btn"
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
        <FolderIcon />
        <span>Reorganize</span>
      </button>

      <ActionMenu
        items={[
          {
            key: 'tags',
            className: 'enhanced-write-tags-album-btn',
            icon: <TagIcon />,
            label: 'Write all tags to files',
            title: 'Write DB metadata to file tags for all tracks in this album',
            onSelect: writeAllTags,
          },
          {
            key: 'rg',
            className: 'enhanced-rg-album-btn',
            icon: <WaveIcon />,
            label: rgBusy ? 'Analyzing ReplayGain…' : 'Analyze ReplayGain',
            title: 'Analyze ReplayGain for all tracks in this album (writes track + album gain)',
            disabled: rgBusy,
            data: { 'album-id': String(album.id) },
            onSelect: analyzeReplayGain,
          },
          {
            key: 'redownload',
            className: 'enhanced-redownload-album-btn',
            icon: <DownloadIcon />,
            label: redownloadBusy ? 'Loading…' : 'Redownload album',
            title: 'Redownload this album (opens Download Missing modal with force-download)',
            disabled: redownloadBusy,
            onSelect: redownload,
          },
          {
            key: 'reassign',
            className: 'enhanced-reassign-album-btn',
            icon: <SwapIcon />,
            label: 'Move to another artist…',
            title: 'Move this album to a different artist',
            data: { 'album-id': String(album.id) },
            onSelect: () => setReassigning(true),
          },
          ...(isHandTagged(album)
            ? [
                {
                  key: 'unlock-manual',
                  className: 'enhanced-unlock-manual-btn',
                  icon: <TagIcon />,
                  label: 'Unlock hand-tagged details…',
                  title: 'Let enrichment and maintenance jobs change this album again',
                  onSelect: () => void unlockHandTagged(),
                },
              ]
            : []),
          {
            key: 'report',
            className: 'enhanced-report-issue-btn',
            icon: <FlagIcon />,
            label: 'Report issue',
            title: 'Report a problem with this album',
            onSelect: onReport,
          },
          {
            key: 'delete',
            className: 'enhanced-delete-album-btn',
            icon: <TrashIcon />,
            label: 'Delete album…',
            danger: true,
            onSelect: onDelete,
          },
        ]}
        trigger={(t) => (
          <button
            type="button"
            className="lib-btn icon lib-more"
            title="More actions"
            aria-label="More album actions"
            {...t}
          >
            <MoreIcon />
          </button>
        )}
      />

      {taggingTracks ? (
        <BatchTagPreviewModal
          trackIds={taggingTracks}
          albumTitle={String(album.title || '')}
          onClose={() => setTaggingTracks(null)}
        />
      ) : null}
      {reorganizing ? (
        <ReorganizeModal album={album} onClose={() => setReorganizing(false)} />
      ) : null}
      {upgradingAlbum ? (
        <AlbumUpgradeModal
          album={album}
          artistName={artistName}
          onReload={onReassigned}
          onClose={() => setUpgradingAlbum(false)}
        />
      ) : null}
      {reassigning ? (
        <ReassignModal
          albumId={album.id}
          albumTitle={String(album.title || '')}
          currentArtist={artistName}
          imageUrl={String(album.thumb_url || '')}
          onClose={() => setReassigning(false)}
          // the album's files are on their way to a different artist, so this
          // artist's view of it is stale: refetch. this used to call onDelete,
          // which opened the delete confirmation dialog over a finished reassign
          onApplied={onReassigned}
        />
      ) : null}
    </>
  );
}
