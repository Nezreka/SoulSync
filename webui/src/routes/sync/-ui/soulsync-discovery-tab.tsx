/**
 * The SoulSync Discovery tab: your personalized playlists (daily mixes, time
 * machine, hidden gems and the rest), each one ready to import.
 *
 * Their tracks already carry provider ids, so there's no identify step:
 * importing one builds it (the kind's generator), saves it as a mirror with
 * the matches inline, and lands you on it under Mirrored. Once it's in, the
 * card opens it, and the mirror's Refresh from source regenerates it (the
 * automation does too, on its schedule).
 */

import { useCallback, useEffect, useState } from 'react';

import type { ImportSpec } from '../-sync.importer';
import type { SsdRecord } from '../-sync.lb-tabs';

import { postMirrorPlaylist } from '../-sync.api';
import {
  buildSsdMirrorPayload,
  fetchSsdRecords,
  postSsdRefresh,
  soulsyncSyntheticId,
} from '../-sync.lb-tabs';
import { ImportCard, ImportGrid, ImportIntro } from './import-card';
import { useImporter } from './use-importer';

const SSD_SOURCE = 'soulsync_discovery';

function recordName(record: SsdRecord): string {
  return record.name || `${record.kind} ${record.variant || ''}`.trim();
}

/** What importing one personalized playlist takes. Exported for the tests. */
export function ssdImportSpec(
  record: SsdRecord,
  onBuilt?: (record: SsdRecord, trackCount: number) => void,
): ImportSpec {
  const variant = record.variant || '';
  return {
    source: SSD_SOURCE,
    sourceId: soulsyncSyntheticId(record.kind, variant || undefined),
    name: recordName(record),
    save: async () => {
      if (!record.kind) throw new Error('Missing kind');
      const data = await postSsdRefresh(record.kind, variant);
      if (!data.success) throw new Error(data.error || 'Generator failed');
      const built = (data.playlist ?? {}) as SsdRecord;
      const tracks = data.tracks ?? [];
      const name = built.name || recordName(record);
      if (tracks.length === 0) {
        throw new Error(
          `'${name}' generated 0 tracks. Try widening the playlist's config in Discover.`,
        );
      }
      const res = await postMirrorPlaylist(
        buildSsdMirrorPayload(record.kind, variant, name, tracks),
      );
      if (!res.success || typeof res.playlist_id !== 'number') {
        throw new Error(res.error || `Couldn't import ${name}`);
      }
      onBuilt?.({ ...record, ...built }, tracks.length);
      return res.playlist_id;
    },
  };
}

/** runs of records sharing a group, in order */
function ssdSections(
  records: SsdRecord[],
): { key: string; group?: string; records: SsdRecord[] }[] {
  const sections: { key: string; group?: string; records: SsdRecord[] }[] = [];
  for (const record of records) {
    const last = sections[sections.length - 1];
    if (last && last.group === record.group) last.records.push(record);
    else {
      sections.push({
        key: `section-${sections.length}-${record.group ?? ''}`,
        group: record.group,
        records: [record],
      });
    }
  }
  return sections;
}

export function SoulsyncDiscoveryTab() {
  const [records, setRecords] = useState<SsdRecord[] | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading SoulSync Discovery playlists...');
  const [refreshing, setRefreshing] = useState(false);
  const importer = useImporter();

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading SoulSync Discovery playlists...');
    setRecords(null);
    setRefreshing(true);
    try {
      const { records: rows, error } = await fetchSsdRecords();
      if (error) {
        setPlaceholder(`❌ ${error}`);
        return;
      }
      setRecords(rows);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown error';
      setPlaceholder(`❌ Error: ${message}`);
      window.showToast?.(`Error loading SoulSync Discovery: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** a built playlist's card shows its real count without a reload */
  const onBuilt = useCallback((built: SsdRecord, trackCount: number) => {
    setRecords((current) =>
      (current ?? []).map((row) =>
        row.kind === built.kind && (row.variant || '') === (built.variant || '')
          ? { ...row, ...built, track_count: trackCount, is_stale: false, _never_generated: false }
          : row,
      ),
    );
  }, []);

  const remaining = (records ?? []).filter(
    (r) =>
      importer.statusOf(SSD_SOURCE, soulsyncSyntheticId(r.kind, r.variant || undefined)) === 'new',
  );

  return (
    <div>
      <div className="playlist-header">
        <h3>SoulSync Discovery Playlists</h3>
        <button
          type="button"
          className="refresh-button soulsync-discovery"
          id="soulsync-discovery-sync-refresh-btn"
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container">
        {records === null || records.length === 0 ? (
          <div className="playlist-placeholder">
            {records === null
              ? placeholder
              : 'No SoulSync Discovery playlists yet. Open the Discover page and generate a few personalized playlists first.'}
          </div>
        ) : (
          <>
            <ImportIntro
              remaining={remaining.length}
              onImportAll={() =>
                void importer.importAll(remaining.map((r) => ssdImportSpec(r, onBuilt)))
              }
            />
            {/* a heading wherever the section changes: 41 cards in one run read
                as a pile. each section is its own grid under its heading */}
            {ssdSections(records).map((section) => (
              <div key={section.key}>
                {section.group ? <div className="ssd-group-title">{section.group}</div> : null}
                <ImportGrid>
                  {section.records.map((r) => {
                    const rid = soulsyncSyntheticId(r.kind, r.variant || undefined);
                    const count = r.track_count || 0;
                    const what = r.description || r.kind;
                    return (
                      <ImportCard
                        key={rid}
                        id={`soulsync-discovery-sync-card-${rid}`}
                        name={recordName(r)}
                        meta={
                          r._never_generated
                            ? `Not built yet · ${what}`
                            : `${count} track${count === 1 ? '' : 's'} · ${what}`
                        }
                        glyph="✨"
                        status={importer.statusOf(SSD_SOURCE, rid)}
                        onAction={() => void importer.importOrOpen(ssdImportSpec(r, onBuilt))}
                      />
                    );
                  })}
                </ImportGrid>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
