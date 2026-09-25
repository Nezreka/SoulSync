import { useState } from 'react';

import type { EnhancedAlbum, EnhancedData } from '../-artist-detail.enhanced';
import type { ArtistInfo } from '../-artist-detail.types';

import { enhancedStats, qualityBreakdown, statsSentenceParts } from '../-artist-detail.enhanced';
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
import { ActionMenu } from './action-menu';
import { FolderIcon, PencilIcon, RefreshIcon, SparkleIcon } from './lib-icons';
import { ManualMatchModal } from './manual-match-modal';
import { ReorganizeAllModal } from './reorganize-modal';
import { ReorganizeStatusPanel } from './reorganize-status-panel';
import { monogram, SourceHealth } from './source-health';

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
 * the artist card at the top of your library: who this is, what you own of
 * them, how good the files are, and how well the metadata sources know them.
 *
 * one card instead of the old meta panel plus a separate stats bar. the
 * numbers read as a sentence under the name, the format badges hang off a
 * quality bar, and the twelve "Service: status" chips are a row of small
 * monograms with the open/rematch actions behind a click.
 */
export function ArtistMetaPanel({ artist, albums, isAdmin, onReload, onArtistPatched }: Props) {
  const [imageBroken, setImageBroken] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
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

  // the id badges and the match chips describe the same services from two
  // angles (do we hold an id / did the matcher succeed); merged per service
  // so one dot carries both the external link and the rematch action.
  const badges = buildIdBadges(artist);
  const chips = artistMatchChips(artist);
  const sources = chips.map((chip) => {
    const badge = badges.find((b) => b.svc === chip.service);
    return {
      service: chip.service,
      label: chip.label,
      status: chip.status,
      title: chip.title,
      url: badge ? getServiceUrl(badge.svc, 'artist', badge.value) : null,
    };
  });

  const stats = enhancedStats({ albums });
  const sentence = statsSentenceParts(stats);
  const quality = qualityBreakdown(stats.badges);

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

  const name = artistDisplayName(artist);

  return (
    <div className="enhanced-artist-meta lib-artist" id="enhanced-artist-meta">
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

      <div className="enhanced-artist-meta-header lib-artist-head">
        <div className="enhanced-artist-meta-header-left lib-artist-identity">
          {artist.thumb_url && !imageBroken ? (
            <img
              className="enhanced-artist-meta-image"
              src={String(artist.thumb_url)}
              alt={name}
              onError={() => setImageBroken(true)}
            />
          ) : (
            <div className="enhanced-artist-meta-image lib-artist-initial" aria-hidden="true">
              {name.trim().charAt(0).toUpperCase() || '♪'}
            </div>
          )}
          <div className="enhanced-artist-meta-info">
            <div className="lib-eyebrow">In your library</div>
            <div className="enhanced-artist-meta-name">{name}</div>
            <div className="lib-artist-stats" data-testid="library-stats">
              {sentence.map((part, i) => (
                <span className="enhanced-stat-item" key={`${part.label}-${i}`}>
                  {i > 0 ? <span className="lib-sep">·</span> : null}
                  <span className="enhanced-stat-value">{part.value}</span>
                  {part.label ? <span className="enhanced-stat-label"> {part.label}</span> : null}
                </span>
              ))}
            </div>
          </div>
        </div>

        <div className="enhanced-artist-meta-actions lib-artist-actions">
          <ReorganizeStatusPanel artistId={artist.id} onReload={onReload} />

          <button
            className="enhanced-sync-btn lib-btn"
            type="button"
            title="Validate files — removes stale entries for tracks no longer on disk"
            disabled={syncing}
            onClick={(e) => {
              e.stopPropagation();
              void sync();
            }}
          >
            <RefreshIcon className={syncing ? 'lib-spin' : undefined} />
            <span>{syncing ? 'Syncing…' : 'Sync'}</span>
          </button>
          <button
            className="enhanced-sync-btn lib-btn"
            type="button"
            title="Reorganize all albums for this artist using your configured download template"
            onClick={() => setReorganizingAll(true)}
          >
            <FolderIcon />
            <span>Reorganize all</span>
          </button>

          {isAdmin ? (
            <>
              <div className="enhanced-enrich-wrap">
                <ActionMenu
                  items={artistEnrichServices().map((svc) => ({
                    key: svc.id,
                    className: 'enhanced-enrich-menu-item',
                    icon: <span className="lib-menu-mono">{monogram(svc.id, svc.label)}</span>,
                    label: svc.label,
                    onSelect: () =>
                      void runEnrichmentRequest({
                        entityType: 'artist',
                        entityId: artist.id,
                        service: svc.id,
                        name: String(artist.name || ''),
                        artistName: '',
                        artistId: artist.id,
                      }).then(applyOutcome),
                  }))}
                  heading="Pull metadata from"
                  trigger={(t) => (
                    <button
                      className="enhanced-enrich-btn lib-btn"
                      type="button"
                      title="Pull fresh metadata for this artist from one source"
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
              <button
                className={`enhanced-meta-edit-toggle lib-btn${formVisible ? ' active' : ''}`}
                type="button"
                onClick={() => {
                  // Opening re-seeds from the record so a Revert-then-reopen
                  // shows saved values, matching the vanilla's full re-render.
                  if (!formVisible) setFormValues(readForm(artist));
                  setFormVisible((open) => !open);
                }}
              >
                <PencilIcon />
                <span>{formVisible ? 'Done' : 'Edit metadata'}</span>
              </button>
            </>
          ) : null}
        </div>
      </div>

      <div className="lib-artist-foot">
        <div
          className="lib-quality"
          title={`${quality.lossless} lossless · ${quality.lossy} lossy`}
        >
          <div className="lib-quality-bar" aria-hidden="true">
            <span
              className="lib-quality-seg lossless"
              style={{ width: `${quality.losslessPercent}%` }}
            />
          </div>
          <div className="lib-quality-legend enhanced-stats-formats">
            <span className="lib-quality-headline">{quality.label}</span>
            {stats.badges.map((badge) => (
              <span className={`enhanced-format-badge ${badge.className}`} key={badge.format}>
                {badge.format} <span className="lib-badge-count">{badge.count}</span>
              </span>
            ))}
          </div>
        </div>

        <SourceHealth
          entries={sources}
          onRematch={isAdmin ? setMatchingService : undefined}
          className="enhanced-match-status-row"
        />
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
            className="enhanced-meta-cancel-btn lib-btn"
            type="button"
            onClick={() => {
              setFormValues(readForm(artist));
              window.showToast?.('Reverted to saved values', 'success');
            }}
          >
            Revert
          </button>
          <button
            className="enhanced-meta-save-btn lib-btn primary"
            type="button"
            onClick={() => void save()}
          >
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
