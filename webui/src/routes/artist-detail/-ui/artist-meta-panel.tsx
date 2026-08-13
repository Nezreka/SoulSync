import { useState } from 'react';

import type { EnhancedAlbum, EnhancedData } from '../-artist-detail.enhanced';
import type { ArtistInfo } from '../-artist-detail.types';

import { getServiceUrl } from '../-artist-detail.enhanced-album';
import { foldUpdatedData, runEnrichmentRequest } from '../-artist-detail.enrich-match';
import {
  artistDisplayName,
  artistEditValue,
  artistEnrichServices,
  artistMatchChips,
  ARTIST_EDIT_FIELDS,
  buildIdBadges,
  collectArtistMetaUpdates,
  syncResultMessage,
} from '../-artist-detail.meta';
import { ManualMatchModal } from './manual-match-modal';
import { ReorganizeAllModal } from './reorganize-modal';
import { ReorganizeStatusPanel } from './reorganize-status-panel';

interface Props {
  artist: ArtistInfo;
  albums: EnhancedAlbum[];
  /** Edit form + enrich menu are admin-only; Sync and Reorganize All are not. */
  isAdmin: boolean;
  /** Sync found changes / a reorganize batch finished — re-fetch the payload. */
  onReload: () => void;
  /** The artist record changed in place (save/match/enrich) — re-render. */
  onArtistPatched: () => void;
}

/**
 * The Enhanced view's artist metadata card (renderArtistMetaPanel,
 * library.js:1174): image + name + id badges on the left; the reorganize
 * status panel and the action buttons on the right; the match-status chip row;
 * and the collapsible edit form.
 */
export function ArtistMetaPanel({ artist, albums, isAdmin, onReload, onArtistPatched }: Props) {
  const [imageBroken, setImageBroken] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [enrichOpen, setEnrichOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [reorganizingAll, setReorganizingAll] = useState(false);
  const [matchingService, setMatchingService] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>(() => readForm(artist));

  /** A match/enrich hands back the whole payload; fold it in and re-render. */
  const applyOutcome = (outcome: { updatedData: EnhancedData | null }) => {
    if (!outcome.updatedData) return;
    foldUpdatedData(
      (window.artistDetailPageState?.enhancedData ?? null) as EnhancedData | null,
      outcome.updatedData,
    );
    onArtistPatched();
  };

  const badges = buildIdBadges(artist);
  const chips = artistMatchChips(artist);

  const sync = async () => {
    setSyncing(true);
    try {
      const response = await fetch(`/api/library/artist/${artist.id}/sync`, { method: 'POST' });
      const data = await response.json();
      if (data.success) {
        const { message, tone, changed } = syncResultMessage(data);
        window.showToast?.(message, tone);
        if (changed) onReload();
      } else {
        window.showToast?.(`Sync failed: ${data.error}`, 'error');
      }
    } catch (error) {
      window.showToast?.(`Sync failed: ${(error as Error).message}`, 'error');
    }
    setSyncing(false);
  };

  const save = async () => {
    const updates = collectArtistMetaUpdates(artist, formValues);
    if (Object.keys(updates).length === 0) {
      window.showToast?.('No changes to save', 'error');
      return;
    }
    try {
      const response = await fetch(`/api/library/artist/${artist.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error);
      // The loaded payload's artist is the render source — patch it in place,
      // as updateLocalEnhancedData did, and let the parent re-render.
      for (const [field, value] of Object.entries(updates)) {
        (artist as Record<string, unknown>)[field] = value;
      }
      onArtistPatched();
      window.showToast?.(
        `Artist metadata saved (${(result.updated_fields || []).join(', ')})`,
        'success',
      );
    } catch (error) {
      window.showToast?.(`Failed to save: ${(error as Error).message}`, 'error');
    }
  };

  return (
    <div className="enhanced-artist-meta" id="enhanced-artist-meta">
      {matchingService ? (
        <ManualMatchModal
          entityType="artist"
          entityId={artist.id}
          service={matchingService}
          defaultQuery={String(artist.name || '')}
          artistId={artist.id}
          onUpdated={applyOutcome}
          onClose={() => setMatchingService(null)}
        />
      ) : null}
      {reorganizingAll ? (
        <ReorganizeAllModal
          albums={albums}
          artistId={artist.id}
          artistName={String(artist.name || 'Artist')}
          onClose={() => setReorganizingAll(false)}
        />
      ) : null}

      <div className="enhanced-artist-meta-header">
        <div className="enhanced-artist-meta-header-left">
          {artist.thumb_url && !imageBroken ? (
            <img
              className="enhanced-artist-meta-image"
              src={String(artist.thumb_url)}
              alt={String(artist.name || '')}
              onError={() => setImageBroken(true)}
            />
          ) : null}
          <div className="enhanced-artist-meta-info">
            <div className="enhanced-artist-meta-name">{artistDisplayName(artist)}</div>
            <div className="enhanced-artist-id-badges">
              {badges.map((badge) => {
                const cls = `enhanced-id-badge ${badge.svc === 'musicbrainz' ? 'mb' : badge.svc}`;
                const url = getServiceUrl(badge.svc, 'artist', badge.value);
                return url ? (
                  <a
                    className={cls}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`${badge.label}: ${badge.value} (click to open)`}
                    onClick={(e) => e.stopPropagation()}
                    key={badge.key}
                  >
                    {badge.label}
                  </a>
                ) : (
                  <span className={cls} title={`${badge.label}: ${badge.value}`} key={badge.key}>
                    {badge.label}
                  </span>
                );
              })}
            </div>
          </div>
        </div>

        <div className="enhanced-artist-meta-actions">
          <ReorganizeStatusPanel artistId={artist.id} onReload={onReload} />

          {isAdmin ? (
            <>
              <button
                className={`enhanced-meta-edit-toggle${formVisible ? ' active' : ''}`}
                type="button"
                onClick={() => {
                  // Opening re-seeds from the record so a Revert-then-reopen
                  // shows saved values, matching the vanilla's full re-render.
                  if (!formVisible) setFormValues(readForm(artist));
                  setFormVisible((open) => !open);
                }}
              >
                {formVisible ? 'Hide Editor' : 'Edit Metadata'}
              </button>
              <div className="enhanced-enrich-wrap">
                <button
                  className="enhanced-enrich-btn"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEnrichOpen((open) => !open);
                  }}
                >
                  Enrich ▾
                </button>
                <div className={`enhanced-enrich-menu${enrichOpen ? ' visible' : ''}`}>
                  {artistEnrichServices().map((svc) => (
                    <div
                      className="enhanced-enrich-menu-item"
                      key={svc.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        setEnrichOpen(false);
                        void runEnrichmentRequest({
                          entityType: 'artist',
                          entityId: artist.id,
                          service: svc.id,
                          name: String(artist.name || ''),
                          artistName: '',
                          artistId: artist.id,
                        }).then(applyOutcome);
                      }}
                    >
                      {svc.icon} {svc.label}
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : null}

          <button
            className="enhanced-sync-btn"
            type="button"
            title="Validate files — removes stale entries for tracks no longer on disk"
            disabled={syncing}
            onClick={(e) => {
              e.stopPropagation();
              void sync();
            }}
          >
            {syncing ? 'Syncing...' : '🔄 Sync'}
          </button>
          <button
            className="enhanced-sync-btn"
            type="button"
            title="Reorganize all albums for this artist using your configured download template"
            onClick={() => setReorganizingAll(true)}
          >
            📁 Reorganize All
          </button>
        </div>
      </div>

      <div className="enhanced-match-status-row">
        {chips.map((chip) => (
          <span
            className={chip.className}
            title={chip.title}
            key={chip.service}
            onClick={() => setMatchingService(chip.service)}
          >
            {chip.label}: {chip.status}
          </span>
        ))}
      </div>

      <div
        className={`enhanced-artist-meta-form${formVisible ? '' : ' hidden'}`}
        id="enhanced-artist-meta-form"
      >
        <div className="enhanced-artist-meta-grid">
          {ARTIST_EDIT_FIELDS.map((field) => (
            <div className={`enhanced-meta-field${field.wide ? ' wide' : ''}`} key={field.key}>
              <label className="enhanced-meta-field-label">{field.label}</label>
              {field.textarea ? (
                <textarea
                  className="enhanced-meta-field-input"
                  data-field={field.key}
                  placeholder={`${field.label}...`}
                  value={formValues[field.key] ?? ''}
                  onChange={(e) =>
                    setFormValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                  }
                />
              ) : (
                <input
                  type="text"
                  className="enhanced-meta-field-input"
                  data-field={field.key}
                  placeholder={`${field.label}...`}
                  value={formValues[field.key] ?? ''}
                  onChange={(e) =>
                    setFormValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                  }
                />
              )}
            </div>
          ))}
        </div>
        <div className="enhanced-artist-form-actions">
          <button
            className="enhanced-meta-cancel-btn"
            type="button"
            onClick={() => {
              setFormValues(readForm(artist));
              window.showToast?.('Reverted to saved values', 'success');
            }}
          >
            Revert
          </button>
          <button className="enhanced-meta-save-btn" type="button" onClick={() => void save()}>
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
}

function readForm(artist: ArtistInfo): Record<string, string> {
  const values: Record<string, string> = {};
  for (const field of ARTIST_EDIT_FIELDS) values[field.key] = artistEditValue(artist, field);
  return values;
}
