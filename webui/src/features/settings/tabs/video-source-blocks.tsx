/**
 * The video side's blocks in the Sources tab's torrent and usenet panels
 * (SETTINGS_PORT.md, video pass V2a): the seeding goals with their
 * per-indexer overrides, what happens to a usenet job once imported, and
 * grabbing the next release when one fails. Were video-settings.js; they are
 * keys of the video downloads config (video-downloads.ts), saved whole when a
 * field is left or a box ticked or a mode picked. The page saved the four
 * seeding goals only when another of these fields saved (nothing listened to
 * them), so an edit to one alone could be lost: each saves now.
 */

import { useState } from 'react';

import { useCommit } from './commit';
import { useVideoDownloads, videoDownloads } from './video-downloads';

type Rules = Record<string, Record<string, number>>;
interface Indexer {
  id: number;
  name: string;
  privacy?: string;
  protocol?: string;
}

const str = (v: unknown) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');

export function VideoSeedingGoals() {
  const { config, loads } = useVideoDownloads();
  const [indexers, setIndexers] = useState<Indexer[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const rules = (config?.seed_overrides || {}) as Rules;
  // one hook each: a hook's ref follows one element
  const saveRatio = useCommit<HTMLInputElement>(() => void videoDownloads.save());
  const saveHours = useCommit<HTMLInputElement>(() => void videoDownloads.save());
  const saveRules = useCommit<HTMLDivElement>(() => void videoDownloads.save());

  /** settings.js loadSeedIndexers: Prowlarr's torrent indexers */
  async function loadIndexers() {
    setNote('Loading…');
    try {
      const r = await fetch('/api/prowlarr/indexers', { headers: { Accept: 'application/json' } });
      const d = r.ok ? ((await r.json()) as { success?: boolean; indexers?: Indexer[] }) : null;
      if (!d || !d.success) {
        setNote("Prowlarr isn't configured yet (Settings → Indexers & Downloaders).");
        return;
      }
      // usenet has no seeding, so no rules that can never fire
      setIndexers((d.indexers || []).filter((i) => i.protocol !== 'usenet'));
      setNote(null);
    } catch {
      setNote("Couldn't reach Prowlarr.");
    }
  }

  /** a rule typed: blank inherits the goal, 0 exempts the tracker */
  function setRule(id: number, field: string, raw: string) {
    const all: Rules = { ...rules };
    const rule = { ...all[String(id)] };
    const v = raw.trim();
    if (v === '') delete rule[field];
    else rule[field] = field === 'ratio' ? parseFloat(v) : parseInt(v, 10);
    if (Object.keys(rule).length) all[String(id)] = rule;
    else delete all[String(id)];
    videoDownloads.edit({ seed_overrides: all });
  }

  const num = (idx: Indexer, field: string, ph: string, step: string) => {
    const r = rules[String(idx.id)] || {};
    const v = r[field];
    return (
      <input
        type="number"
        min="0"
        step={step}
        placeholder={ph}
        data-vseed-idx={idx.id}
        data-vseed-field={field}
        key={`${field}-${loads}`}
        defaultValue={v === undefined || v === null ? '' : v}
        style={{ maxWidth: '78px' }}
        onInput={(event) => setRule(idx.id, field, (event.target as HTMLInputElement).value)}
      />
    );
  };

  return (
    <div className="form-group" id="video-seeding-goals">
      <label>Seeding goals (torrents):</label>
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="settings-hint">Ratio</span>{' '}
        <input
          type="number"
          id="video-seed-ratio"
          min="0"
          step="0.1"
          style={{ maxWidth: '90px' }}
          placeholder="0"
          ref={saveRatio}
          value={config ? str(config.seed_ratio_goal ?? 0) : ''}
          onChange={(event) => videoDownloads.edit({ seed_ratio_goal: event.target.value })}
        />{' '}
        <span className="settings-hint">or hours seeded</span>{' '}
        <input
          type="number"
          id="video-seed-hours"
          min="0"
          step="1"
          style={{ maxWidth: '90px' }}
          placeholder="0"
          ref={saveHours}
          value={config ? str(config.seed_time_goal_hours ?? 0) : ''}
          onChange={(event) => videoDownloads.edit({ seed_time_goal_hours: event.target.value })}
        />{' '}
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12.5px' }}>
          <input
            type="checkbox"
            id="video-seed-remove-data"
            checked={config ? config.seed_remove_data !== false : true}
            onChange={(event) =>
              void videoDownloads.change({ seed_remove_data: event.target.checked })
            }
          />{' '}
          also delete the client's copy
        </label>
      </div>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px' }}>
        <span className="settings-hint">Enforced by</span>{' '}
        <select
          id="video-seed-mode"
          className="form-select"
          style={{ maxWidth: '260px' }}
          value={config ? str(config.seed_mode) || 'soulsync' : 'soulsync'}
          onChange={(event) => void videoDownloads.change({ seed_mode: event.target.value })}
        >
          <option value="soulsync">SoulSync (watches the client + removes)</option>
          <option value="client">Torrent client (writes its own seed limit)</option>
        </select>
      </div>
      <small className="settings-hint">
        Both at 0 = never remove (the old behavior). <strong>SoulSync</strong>: SS polls the client
        and removes when a goal is hit (your imported library copy is separate and never touched).{' '}
        <strong>Torrent client</strong>: SS writes the ratio/time limit into the client (arr-style)
        and lets it enforce — it shows in the client's share-limit dialog and works even when SS is
        off.
      </small>
      <div id="video-seed-per-indexer" style={{ marginTop: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <span className="settings-hint">Per-indexer overrides</span>{' '}
          <button
            className="test-button"
            type="button"
            id="video-seed-idx-refresh"
            onClick={() => void loadIndexers()}
          >
            Load indexers
          </button>
        </div>
        <small className="settings-hint" style={{ display: 'block', margin: '6px 0 8px' }}>
          Sonarr/Radarr put seed rules on the indexer, not globally — a private tracker needs a
          ratio or a week of seeding, a public one needs nothing. Leave a box <strong>blank</strong>{' '}
          to inherit the goal above; type <strong>0</strong> to exempt that tracker from it.{' '}
          <strong>Pack hrs</strong> is the longer window season packs get (blank = same as Hours).
        </small>
        <div id="video-seed-idx-rows" className="ind-indexer-list" ref={saveRules}>
          {note !== null ? (
            <em className="settings-hint">{note}</em>
          ) : indexers === null ? (
            <em className="settings-hint">
              Click Load indexers to list the torrent indexers from Prowlarr.
            </em>
          ) : !indexers.length ? (
            <em className="settings-hint">
              No torrent indexers in Prowlarr. Usenet never seeds, so only torrent indexers show
              here.
            </em>
          ) : (
            indexers.map((idx) => (
              <div
                key={idx.id}
                className="ind-indexer-card"
                style={{ gap: '8px', flexWrap: 'wrap' }}
              >
                <span className="ind-indexer-card-id">#{idx.id}</span>
                <span className="ind-indexer-card-name">{idx.name}</span>
                {idx.privacy ? (
                  <span className="ind-indexer-card-privacy">{idx.privacy}</span>
                ) : null}
                <span
                  style={{ marginLeft: 'auto', display: 'flex', gap: '6px', alignItems: 'center' }}
                >
                  <span className="settings-hint">Ratio</span>
                  {num(idx, 'ratio', 'inherit', '0.1')}
                  <span className="settings-hint">Hours</span>
                  {num(idx, 'hours', 'inherit', '1')}
                  <span className="settings-hint">Pack hrs</span>
                  {num(idx, 'pack_hours', 'same', '1')}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export function VideoUsenetBlocks() {
  const { config } = useVideoDownloads();
  return (
    <>
      <div className="form-group" id="video-usenet-remove-completed-group">
        <label>When a usenet download has imported (video):</label>
        <select
          id="video-usenet-remove-completed"
          className="form-select"
          value={config ? str(config.usenet_remove_completed) || 'off' : 'off'}
          onChange={(event) =>
            void videoDownloads.change({ usenet_remove_completed: event.target.value })
          }
        >
          <option value="off">Leave it in the client</option>
          <option value="history">Remove it from the client's history</option>
          <option value="files">Remove it and delete what's left in its folder</option>
        </select>
        <div className="setting-help-text">
          SoulSync already clears the file it imported; this also takes the job out of the client
          and, with the last option, deletes what it left behind (nfo, sample). Failed and
          import-failed downloads keep their job for Manual import, and a season pack only goes when
          every episode in it imported.
        </div>
      </div>
      <div className="form-group" id="video-redownload-failed-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            id="video-redownload-failed"
            checked={config ? config.redownload_failed !== false : true}
            onChange={(event) =>
              void videoDownloads.change({ redownload_failed: event.target.checked })
            }
          />{' '}
          When a video download fails, grab the next best release
        </label>
        <div className="setting-help-text">
          When SABnzbd or the torrent client fails a movie or episode, or a torrent stalls, that
          release is blocklisted and the next one your quality profile ranks is grabbed right away.
          It stops after 5 failed releases of the same title in 2 days, and you get the failed
          notification when nothing else could be taken.
        </div>
      </div>
    </>
  );
}
