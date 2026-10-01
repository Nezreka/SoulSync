import { useMemo, useState } from 'react';

import {
  DISCOVER_ZONES,
  type DiscoverLayoutSection,
  type DiscoverSectionId,
  type DiscoverZoneId,
} from '../-discover.layout';

/**
 * The Layout customizer modal: enable checkboxes plus up/down ordering per
 * zone, no drag-and-drop. The working copy is zone-grouped; saving rebuilds
 * per-zone positions from the edited order and PUTs the whole layout.
 */

const SECTION_LABELS: Record<DiscoverSectionId, string> = {
  'your-mixes-section': 'Your Mixes',
  'mood-mixes-section': 'Moods',
  'adv-wave': 'Adventurousness Wave',
  'listening-recs-section': 'Listening Recommendations',
  'recommended-artists-section': 'Recommended Artists',
  'discover-bylt-sections': 'Because You Listen To',
  'recent-releases': 'Recent Releases',
  'cache-genre-releases': 'Genre Releases',
  'seasonal-albums-section': 'Seasonal Albums',
  'cache-undiscovered': 'Undiscovered Gems',
  'cache-label-explorer': 'Label Explorer',
  'your-albums-section': 'Your Albums',
  'your-artists-section': 'Your Artists',
  'year-mixes-section': 'Year Mixes',
  'cache-deep-cuts': 'Deep Cuts',
  'cache-genre-explorer': 'Genre Explorer',
  'lastfm-radio': 'Last.fm Radio',
  listenbrainz: 'ListenBrainz',
  'deezer-editorial': 'Deezer Editorial',
  'build-a-playlist': 'Build a Playlist',
};

export interface DiscoverLayoutModalProps {
  entries: DiscoverLayoutSection[];
  saving: boolean;
  onSave: (sections: DiscoverLayoutSection[]) => void;
  onClose: () => void;
}

export function DiscoverLayoutModal({
  entries,
  saving,
  onSave,
  onClose,
}: DiscoverLayoutModalProps) {
  const [working, setWorking] = useState<DiscoverLayoutSection[]>(() =>
    entries.map((e) => ({ ...e })),
  );

  const byZone = useMemo(() => {
    const grouped: Record<DiscoverZoneId, DiscoverLayoutSection[]> = {
      'for-you': [],
      'new-missing': [],
      library: [],
      tools: [],
    };
    for (const entry of working) {
      if (grouped[entry.zone]) grouped[entry.zone].push(entry);
    }
    for (const zone of DISCOVER_ZONES) {
      grouped[zone.id].sort((a, b) => a.position - b.position);
    }
    return grouped;
  }, [working]);

  const toggle = (id: DiscoverSectionId) =>
    setWorking((prev) => prev.map((e) => (e.id === id ? { ...e, enabled: !e.enabled } : e)));

  const move = (id: DiscoverSectionId, delta: -1 | 1) =>
    setWorking((prev) => {
      const entry = prev.find((e) => e.id === id);
      if (!entry) return prev;
      const zoneIds = prev
        .filter((e) => e.zone === entry.zone)
        .sort((a, b) => a.position - b.position)
        .map((e) => e.id);
      const idx = zoneIds.indexOf(id);
      const swapIdx = idx + delta;
      if (idx < 0 || swapIdx < 0 || swapIdx >= zoneIds.length) return prev;
      const next = prev.map((e) => ({ ...e }));
      const a = next.find((e) => e.id === zoneIds[idx])!;
      const b = next.find((e) => e.id === zoneIds[swapIdx])!;
      const tmp = a.position;
      a.position = b.position;
      b.position = tmp;
      return next;
    });

  const handleSave = () => {
    const saved: DiscoverLayoutSection[] = [];
    for (const zone of DISCOVER_ZONES) {
      const ordered = [...byZone[zone.id]].sort((a, b) => a.position - b.position);
      ordered.forEach((entry, position) => {
        saved.push({ id: entry.id, zone: zone.id, enabled: entry.enabled, position });
      });
    }
    onSave(saved);
  };

  return (
    <div
      id="discover-layout-modal-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="discover-layout-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Customize discover layout"
      >
        <div className="discover-layout-modal-header">
          <h2>Customize Layout</h2>
          <p>Choose which sections appear on Discover and in what order.</p>
          <button type="button" className="watch-all-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        <div className="discover-layout-modal-body">
          {DISCOVER_ZONES.map((zone) => (
            <section key={zone.id} className="discover-layout-zone">
              <h3>{zone.label}</h3>
              <ul>
                {byZone[zone.id].map((entry, idx) => (
                  <li
                    key={entry.id}
                    className={'discover-layout-row' + (entry.enabled ? '' : ' is-disabled')}
                  >
                    <label className="discover-layout-check">
                      <input
                        type="checkbox"
                        checked={entry.enabled}
                        onChange={() => toggle(entry.id)}
                        aria-label={`Show ${SECTION_LABELS[entry.id]}`}
                      />
                      <span>{SECTION_LABELS[entry.id]}</span>
                    </label>
                    <span className="discover-layout-movers">
                      <button
                        type="button"
                        disabled={idx === 0}
                        onClick={() => move(entry.id, -1)}
                        aria-label={`Move ${SECTION_LABELS[entry.id]} up`}
                        title="Move up"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        disabled={idx === byZone[zone.id].length - 1}
                        onClick={() => move(entry.id, 1)}
                        aria-label={`Move ${SECTION_LABELS[entry.id]} down`}
                        title="Move down"
                      >
                        ↓
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <div className="discover-layout-modal-footer">
          <button type="button" className="lib-btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="lib-btn primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save layout'}
          </button>
        </div>
      </div>
    </div>
  );
}
