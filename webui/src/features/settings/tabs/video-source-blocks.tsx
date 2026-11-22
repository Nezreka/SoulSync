/**
 * The video side's groups in the torrent and usenet sheets ("For video"):
 * the seeding goals with their per-indexer overrides, and what happens to a
 * usenet job once imported. (Grabbing the next release when one fails moved
 * to Downloads > Behaviour, Video.) Were video-settings.js; they are
 * keys of the video downloads config (video-downloads.ts), saved whole when a
 * field is left or a box ticked or a mode picked. The page saved the four
 * seeding goals only when another of these fields saved (nothing listened to
 * them), so an edit to one alone could be lost: each saves now.
 */

import { useEffect, useState } from 'react';

import {
  ActionRow,
  Btn,
  FieldNote,
  Item,
  Items,
  NumberField,
  Row,
  Select,
  Switch,
  itemClass,
} from '../ui';
import { useCommit } from './commit';
import { useVideoDownloads, videoDownloads } from './video-downloads';

/** the seeding goals' help, said once for music and once for video */
export const SEED_MORE = (
  <>
    Both at 0 means a torrent is never removed. <strong>SoulSync</strong>: SoulSync watches the
    client and removes the torrent when a goal is met (your imported library copy is separate and
    never touched). <strong>Torrent client</strong>: SoulSync writes the ratio and time limit into
    the client, which enforces them itself. It shows in the client's share-limit dialog and works
    even when SoulSync is off.
  </>
);

export const SEED_MODES = [
  { value: 'soulsync', label: 'SoulSync (watches the client and removes)' },
  { value: 'client', label: 'Torrent client (writes its own seed limit)' },
];

/** what happens to a usenet job once imported: music and video say the same */
export const USENET_REMOVE = [
  { value: 'off', label: 'Leave it in the client' },
  { value: 'history', label: "Remove it from the client's history" },
  { value: 'files', label: "Remove it and delete what's left in its folder" },
];

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
  // shown on the Sources tab of both sides: the config it edits loads with
  // it (once a page), not only with the Library tab or the video side
  useEffect(() => void videoDownloads.load(), []);
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
        setNote("Prowlarr isn't set up yet (Downloads, Sources, Indexers).");
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

  const num = (idx: Indexer, field: string, ph: string, step: string, name: string) => {
    const r = rules[String(idx.id)] || {};
    const v = r[field];
    return (
      <NumberField
        min="0"
        step={step}
        placeholder={ph}
        aria-label={`${name} for ${idx.name}`}
        data-vseed-idx={idx.id}
        data-vseed-field={field}
        key={`${field}-${loads}`}
        defaultValue={v === undefined || v === null ? '' : v}
        onInput={(event) => setRule(idx.id, field, (event.target as HTMLInputElement).value)}
      />
    );
  };

  return (
    <div id="video-seeding-goals">
      <Row
        field
        label="Seed until a ratio of"
        htmlFor="video-seed-ratio"
        help="Remove a finished torrent once it reaches this ratio or the hours below, whichever comes first."
        more={SEED_MORE}
      >
        <NumberField
          id="video-seed-ratio"
          min="0"
          step="0.1"
          placeholder="0"
          ref={saveRatio}
          value={config ? str(config.seed_ratio_goal ?? 0) : ''}
          onChange={(event) => videoDownloads.edit({ seed_ratio_goal: event.target.value })}
        />
      </Row>
      <Row field sub label="Or hours seeded" htmlFor="video-seed-hours">
        <NumberField
          id="video-seed-hours"
          unit="hours"
          min="0"
          step="1"
          placeholder="0"
          ref={saveHours}
          value={config ? str(config.seed_time_goal_hours ?? 0) : ''}
          onChange={(event) => videoDownloads.edit({ seed_time_goal_hours: event.target.value })}
        />
      </Row>
      <Row sub label="Also delete the client's copy" htmlFor="video-seed-remove-data">
        <Switch
          id="video-seed-remove-data"
          checked={config ? config.seed_remove_data !== false : true}
          onChange={(event) =>
            void videoDownloads.change({ seed_remove_data: event.target.checked })
          }
        />
      </Row>
      <Row field sub label="Enforced by" htmlFor="video-seed-mode">
        <Select
          id="video-seed-mode"
          value={config ? str(config.seed_mode) || 'soulsync' : 'soulsync'}
          onChange={(event) => void videoDownloads.change({ seed_mode: event.target.value })}
          options={SEED_MODES}
        />
      </Row>
      <div id="video-seed-per-indexer">
        <ActionRow
          label="Per-indexer goals"
          help="A private tracker wants a ratio or a week of seeding, a public one wants nothing. Leave a box blank to use the goal above; type 0 to exempt that tracker."
        >
          <Btn small id="video-seed-idx-refresh" onClick={() => void loadIndexers()}>
            Load indexers
          </Btn>
        </ActionRow>
        <FieldNote>
          Sonarr and Radarr put seed rules on the indexer, not globally. <strong>Pack hours</strong>{' '}
          is the longer window season packs get (blank means the same as Hours).
        </FieldNote>
        <Items id="video-seed-idx-rows" ref={saveRules}>
          {note !== null ? (
            <FieldNote>{note}</FieldNote>
          ) : indexers === null ? (
            <FieldNote>Load indexers to list the torrent indexers from Prowlarr.</FieldNote>
          ) : !indexers.length ? (
            <FieldNote>
              No torrent indexers in Prowlarr. Usenet never seeds, so only torrent indexers show
              here.
            </FieldNote>
          ) : (
            indexers.map((idx) => (
              <Item key={idx.id} data-indexer="">
                <span className={itemClass.dim}>#{idx.id}</span>
                <span className={itemClass.main}>{idx.name}</span>
                {idx.privacy ? <span className={itemClass.dim}>{idx.privacy}</span> : null}
                <span className={itemClass.cells}>
                  <span className={itemClass.dim}>Ratio</span>
                  {num(idx, 'ratio', 'inherit', '0.1', 'Ratio')}
                  <span className={itemClass.dim}>Hours</span>
                  {num(idx, 'hours', 'inherit', '1', 'Hours')}
                  <span className={itemClass.dim}>Pack hours</span>
                  {num(idx, 'pack_hours', 'same', '1', 'Pack hours')}
                </span>
              </Item>
            ))
          )}
        </Items>
      </div>
    </div>
  );
}

export function VideoUsenetBlocks() {
  const { config } = useVideoDownloads();
  useEffect(() => void videoDownloads.load(), []);
  return (
    <Row
      stack
      id="video-usenet-remove-completed-group"
      label="When a download has imported"
      htmlFor="video-usenet-remove-completed"
      help="SoulSync already clears the file it imported; this also takes the job out of the client and, with the last option, deletes what it left behind (nfo, sample). Failed and import-failed downloads keep their job for Manual import, and a season pack only goes when every episode in it imported."
    >
      <Select
        id="video-usenet-remove-completed"
        value={config ? str(config.usenet_remove_completed) || 'off' : 'off'}
        onChange={(event) =>
          void videoDownloads.change({ usenet_remove_completed: event.target.value })
        }
        options={USENET_REMOVE}
      />
    </Row>
  );
}
