/**
 * A Cover Art finding, laid out so it explains itself (#2169).
 *
 * Four tiles side by side: the cover saved in the library, the cover inside
 * the audio files, the cover file in the album folder, and the cover a source
 * found, each named, each an empty box when there is none. Every tile with a
 * picture can be picked (#2202): Apply Art writes the picked cover where one
 * is missing, the best one there is picked to start with. Under them, what
 * Apply Art does in each place with that pick and the user's settings, read
 * live from the server so it matches the fix. Change cover opens the album
 * page's own picker for any other cover. An artist photo, only suggested
 * when the artist has none, sits apart and is never part of Apply Art.
 */
import { useEffect, useState } from 'react';

import type { CoverFindingState, CoverPick, CoverPlanStep } from '../-tools.api';
import type { RepairFinding } from '../-tools.types';

import { fetchCoverFindingState, resolveFinding } from '../-tools.api';
import { providerName } from '../-tools.finding-copy';
import { AlbumArtPickerModal } from '../../library/-ui/art-picker-modal';
import styles from './cover-art-finding.module.css';

type Details = Record<string, unknown>;

const text = (value: unknown): string =>
  typeof value === 'string' || typeof value === 'number' ? String(value) : '';

function lib2AlbumId(details: Details): number | null {
  const raw = text(details.album_id).replace(/^lib2:/, '');
  const id = Number(raw);
  return raw && Number.isInteger(id) && id > 0 ? id : null;
}

/** which cover a plan step writes, in words */
function coverWords(why: string, source: string): string {
  if (why === 'chosen') return 'the cover you picked';
  if (why === 'library') return "the library's cover";
  if (why === 'files') return 'the cover inside the files';
  if (why === 'folder') return "the album folder's cover";
  return `the ${source || 'found'} cover`;
}

/** an apply target for one tile: 'album:folder' (#2202) */
export type CoverApplyTarget = 'album' | 'artist' | `album:${CoverPick}`;

const ACTION_CLASS: Record<CoverPlanStep['action'], string | undefined> = {
  write: styles.write,
  keep: styles.keep,
  skip: styles.skip,
};

const PLACE_NAMES: Record<CoverPlanStep['place'], string> = {
  library: 'Library',
  files: 'Audio files',
  folder: 'Album folder',
};

/** one line of "what Apply Art does", the same choices the fix makes */
export function coverPlanLine(step: CoverPlanStep, source: string): string {
  const { place, action, why } = step;
  if (place === 'library') {
    if (why === 'chosen') return 'Keeps the cover you picked.';
    if (action === 'keep') return 'Already has a cover. Kept as it is.';
    if (action === 'write' && step.broken)
      return `Its saved cover no longer loads. Saves the ${source || 'found'} cover in its place.`;
    if (action === 'write' && step.replace)
      return `Replaces its cover with the ${source || 'found'} cover, the same as Change cover.`;
    if (action === 'write') return `Saves the ${source || 'found'} cover.`;
    if (why === 'broken')
      return 'Its saved cover no longer loads. Change cover lets you pick a new one.';
    if (why === 'local') return 'Stays without a saved cover. Change cover saves one.';
    return 'Stays without a saved cover: no source had one. Change cover lets you pick one.';
  }
  if (place === 'files') {
    if (why === 'setting_off') return 'Left alone: embedding art is off in Settings.';
    if (why === 'unknown') return 'Not checked yet. The next scan reads them.';
    if (action === 'keep') return 'Every file has a cover. Kept as they are.';
    if (action === 'write') {
      const count = step.count ?? 0;
      return `Embeds ${coverWords(why, source)} into the ${count} file${count === 1 ? '' : 's'} without one.`;
    }
    return 'Nothing to embed.';
  }
  if (why === 'setting_off') return 'Left alone: cover.jpg downloads are off in Settings.';
  if (action === 'keep') return `Already has ${step.file || 'a cover file'}. Kept as it is.`;
  if (why === 'unreachable') return 'Not reachable from SoulSync, so nothing is written there.';
  if (action === 'write') return `Writes cover.jpg from ${coverWords(why, source)}.`;
  return 'Nothing to write.';
}

function CoverTile({
  label,
  url,
  note,
  empty,
  round,
  picked,
  onPick,
  onBroken,
}: {
  label: string;
  url: string | null;
  note: string;
  empty: string;
  round?: boolean;
  /** set on a tile that can be picked: whether it is the pick */
  picked?: boolean;
  onPick?: () => void;
  onBroken?: () => void;
}) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [url]);
  const shown = url && !broken;
  const pickable = Boolean(shown && onPick);
  const frame = (
    <div className={`${styles.frame}${round ? ` ${styles.round}` : ''}`}>
      {shown ? (
        <img
          src={url}
          alt={`${label}: ${note}`}
          loading="lazy"
          onError={() => {
            setBroken(true);
            onBroken?.();
          }}
        />
      ) : (
        <span className={styles.empty}>{empty}</span>
      )}
      {pickable && picked ? <span className={styles.pickedMark}>Using this</span> : null}
    </div>
  );
  const caption = (
    <figcaption>
      <span className={styles.place}>{label}</span>
      <span className={styles.note}>{shown ? note : ' '}</span>
    </figcaption>
  );
  if (pickable) {
    return (
      <figure className={`${styles.tile} ${styles.pickable}${picked ? ` ${styles.picked}` : ''}`}>
        <button
          type="button"
          role="radio"
          aria-checked={Boolean(picked)}
          aria-label={`Use the cover ${label.charAt(0).toLowerCase()}${label.slice(1)}`}
          className={styles.pick}
          onClick={onPick}
        >
          {frame}
        </button>
        {caption}
      </figure>
    );
  }
  return (
    <figure className={styles.tile}>
      {frame}
      {caption}
    </figure>
  );
}

/** what the scan saw, for a finding the server can no longer describe */
function stateFromDetails(details: Details): CoverFindingState {
  return {
    album_id: lib2AlbumId(details) ?? 0,
    library: {
      has: details.db_missing === false,
      locked: false,
      broken: details.library_cover_broken === true,
      url: null,
    },
    files: {
      with: details.embed_missing === false ? 1 : 0,
      without: details.embed_missing === true ? 1 : 0,
      unknown: 0,
      url: null,
    },
    folder: { file: null, exists: true, url: null },
    found: {
      url: text(details.found_artwork_url) || null,
      source: details.found_artwork_url ? text(details.artwork_source) : null,
    },
    settings: { embed: true, sidecar: true },
    plan: [],
    choices: [],
    pick: null,
    plans: {},
  };
}

export function CoverArtFinding({
  finding,
  onApplyCoverArt,
  onChanged,
}: {
  finding: RepairFinding;
  onApplyCoverArt: (findingId: number, target: CoverApplyTarget) => void;
  /** after Change cover applied a pick: the finding is done, reload the list */
  onChanged?: () => void;
}) {
  const details = (finding.details || {}) as Details;
  const albumId = lib2AlbumId(details);
  const pending = finding.status === undefined || finding.status === 'pending';
  const [live, setLive] = useState<CoverFindingState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoaded(false);
    void fetchCoverFindingState(finding.id).then((state) => {
      if (!alive) return;
      setLive(state);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, [finding.id]);

  const [chosen, setChosen] = useState<CoverPick | null>(null);
  const [unloaded, setUnloaded] = useState<CoverPick[]>([]);
  useEffect(() => {
    setChosen(null);
    setUnloaded([]);
  }, [finding.id]);

  const state = live ?? stateFromDetails(details);
  const source = providerName(state.found.source || details.artwork_source);
  // #2202: every tile with a picture is a pick; one that fails to load is not
  const choices = state.choices.filter((pick) => !unloaded.includes(pick));
  const pick: CoverPick | null =
    chosen && choices.includes(chosen)
      ? chosen
      : state.pick && choices.includes(state.pick)
        ? state.pick
        : (choices[0] ?? null);
  const plan = (pick && state.plans[pick]) || state.plan;
  const nothingToApply = Boolean(live) && choices.length === 0;
  const tile = (place: CoverPick) =>
    pending && choices.includes(place)
      ? {
          picked: pick === place,
          onPick: () => setChosen(place),
          onBroken: () => setUnloaded((gone) => (gone.includes(place) ? gone : [...gone, place])),
        }
      : {};
  const fileTotal = state.files.with + state.files.without;
  const foundLabel = state.found.url && source ? `Found on ${source}` : 'Found by a source';

  const artistPhoto = text(details.found_artist_url);
  // an artist that has a photo gets none suggested (older findings did)
  const suggestArtist = Boolean(artistPhoto) && !text(details.artist_thumb_url);

  return (
    <div className={styles.panel}>
      <div
        className={styles.tiles}
        role={choices.length && pending ? 'radiogroup' : undefined}
        aria-label={choices.length && pending ? 'Cover Apply Art uses' : undefined}
      >
        <CoverTile
          label="In the library"
          url={state.library.url}
          note={state.library.locked ? 'The cover you picked' : 'Saved cover'}
          empty={
            state.library.broken
              ? 'Saved cover no longer loads'
              : state.library.has && !live
                ? 'Has a cover'
                : 'No saved cover'
          }
          {...tile('library')}
        />
        <CoverTile
          label="In the audio files"
          url={state.files.url}
          note={
            fileTotal
              ? `In ${state.files.with} of ${fileTotal} file${fileTotal === 1 ? '' : 's'}`
              : 'Embedded cover'
          }
          empty={state.files.with && !live ? 'Has a cover' : 'No cover inside'}
          {...tile('files')}
        />
        <CoverTile
          label="In the album folder"
          url={state.folder.url}
          note={state.folder.file || 'Cover file'}
          empty={state.folder.exists ? 'No cover file' : 'Folder not reachable'}
          {...tile('folder')}
        />
        <CoverTile
          label={foundLabel}
          url={state.found.url}
          note={source ? `From ${source}` : 'Found cover'}
          empty={state.library.has ? 'Not searched: the library has one' : 'Nothing found'}
          {...tile('found')}
        />
      </div>

      {pending ? (
        <section className={styles.plan} aria-label="What Apply Art does">
          <h4>What Apply Art does</h4>
          {plan.length ? (
            <ul>
              {plan.map((step) => (
                <li key={step.place} className={ACTION_CLASS[step.action]}>
                  <span className={styles.planPlace}>{PLACE_NAMES[step.place]}</span>
                  <span>{coverPlanLine(step, source)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.planQuiet}>
              {loaded
                ? 'This album is no longer in the library as it was. A rescan brings the finding up to date.'
                : 'Checking the library, the files and the folder…'}
            </p>
          )}
          <div className={styles.actions}>
            <button
              className="repair-art-apply-btn"
              type="button"
              disabled={nothingToApply}
              onClick={() => onApplyCoverArt(finding.id, pick ? `album:${pick}` : 'album')}
            >
              Apply Art
            </button>
            {albumId ? (
              <button
                className="repair-art-apply-btn"
                type="button"
                onClick={() => setPicking(true)}
              >
                Change cover
              </button>
            ) : null}
            <span className={styles.actionsNote}>
              {nothingToApply
                ? 'There is no cover here to apply. '
                : choices.length > 1
                  ? 'Click a cover above to use it instead. '
                  : ''}
              Change cover picks any other cover, the same as on the album page. It replaces the
              library cover and is written to the files and the folder too.
            </span>
          </div>
        </section>
      ) : null}

      {suggestArtist ? (
        <section className={styles.artist} aria-label="Suggested artist photo">
          <CoverTile
            label="Suggested artist photo"
            url={artistPhoto}
            note={
              details.artist_artwork_source
                ? `From ${providerName(details.artist_artwork_source)}`
                : 'Found photo'
            }
            empty="No photo"
            round
          />
          <div className={styles.artistText}>
            <p>
              {text(details.artist) || 'This artist'} has no photo in the library. This one was
              found while looking for the cover. Apply Art never uses it; it is only set when you
              choose so.
            </p>
            {pending ? (
              <button
                className="repair-art-apply-btn"
                type="button"
                onClick={() => onApplyCoverArt(finding.id, 'artist')}
              >
                Use for artist
              </button>
            ) : null}
          </div>
        </section>
      ) : null}

      {picking && albumId ? (
        <AlbumArtPickerModal
          albumId={albumId}
          albumTitle={text(details.album_title) || 'Album'}
          onClose={() => setPicking(false)}
          onApplied={() => {
            // the pick landed through the album page's own path: the
            // finding is answered
            void resolveFinding(finding.id).finally(() => onChanged?.());
          }}
        />
      ) : null}
    </div>
  );
}
