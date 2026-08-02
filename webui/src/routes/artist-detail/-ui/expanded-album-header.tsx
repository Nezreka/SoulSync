import { useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import {
  albumEnrichServices,
  albumIdBadges,
  albumMatchChips,
  expandedHeaderDetails,
} from '../-artist-detail.enhanced-album';
import {
  deleteLibraryAlbumRequest,
  type DeleteAlbumChoice,
} from '../-artist-detail.manage-actions';
import { ArtPicker } from './art-picker';
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
}: Props) {
  const [artBroken, setArtBroken] = useState(false);
  const [pickingArt, setPickingArt] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
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
                window.openManualMatchModal?.(
                  'album',
                  album.id,
                  chip.service,
                  String(album.title || ''),
                  artistId,
                );
              }}
            >
              {chip.label}: {chip.status}
            </span>
          ))}
        </div>

        <div className="enhanced-expanded-actions">
          {isAdmin ? (
            <AdminAlbumActions album={album} artistId={artistId} artistName={artistName} />
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
}: {
  album: EnhancedAlbum;
  artistId: unknown;
  artistName: string;
}) {
  const [enrichOpen, setEnrichOpen] = useState(false);

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
                window.runEnrichment?.(
                  'album',
                  album.id,
                  service.id,
                  String(album.title || ''),
                  artistName,
                  artistId,
                );
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
          window.writeAlbumTags?.(album.id);
        }}
      >
        ✎ Write All Tags
      </button>

      <button
        type="button"
        className="enhanced-rg-album-btn"
        title="Analyze ReplayGain for all tracks in this album (writes track + album gain)"
        data-album-id={String(album.id)}
        onClick={(e) => {
          e.stopPropagation();
          window.analyzeAlbumReplayGain?.(album.id, e.currentTarget);
        }}
      >
        ♫ ReplayGain
      </button>

      <button
        type="button"
        className="enhanced-reorganize-album-btn"
        title="Reorganize album files using your configured download template"
        data-album-id={String(album.id)}
        onClick={(e) => {
          e.stopPropagation();
          window.showReorganizeModal?.(album.id);
        }}
      >
        📁 Reorganize
      </button>

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
          window.deleteLibraryAlbum?.(album.id);
        }}
      >
        Delete Album
      </button>
    </>
  );
}
