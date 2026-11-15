/**
 * Library > Audiobooks (SETTINGS_PORT.md, step 8c): the Audiobooks panel of
 * the Folders card (the folder, more folders, the spoken-word artists) and of
 * the Organization card (the template, the recycle bin, quality, downloads and
 * import). Same markup, classes and ids.
 *
 * The audiobook download chain is a settings key (audiobooks.download_source):
 * the chain widget on the Downloads tab reads and writes it through the store
 * (sources-model.ts). The hidden mode select here is the old transport, kept.
 *
 * audiobooks.quality and audiobooks.download_source are dicts: each key is
 * saved alone as a dotted key, the rest of the dict kept (settings-store.ts).
 */

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import { HintGroup, int, isDocker, LIBRARY_TAB, LockedPath, useLoadedDraft } from './library-card';
import { text } from './source-panels';

const TAB = LIBRARY_TAB;
type Section = Record<string, unknown>;

type Shell = Window & {
  SoulSyncFolderCheck?: { refresh: (group: string) => void };
  showToast?: (message: string, type?: string) => void;
  showConfirmDialog?: (options: {
    title: string;
    message: string;
    confirmText: string;
  }) => Promise<boolean>;
};
const shell = () => window as Shell;

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}
function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

/** the audiobooks folder as loadSettingsData filled it: Docker's own default
 *  replaces the bare './audiobooks' */
export function audiobooksPath(settings: Record<string, Record<string, unknown> | undefined>) {
  const fallback = isDocker(settings) ? '/app/audiobooks' : './audiobooks';
  const current =
    text(settings.audiobooks?.download_path) || text(settings.library?.audiobooks_path);
  return isDocker(settings) && (!current || current === './audiobooks')
    ? fallback
    : current || fallback;
}

const hint = (children: ReactNode) => <small className="settings-hint">{children}</small>;

// ── Folders > Audiobooks ──

/** More audiobook folders (addAudiobookExtraPathRow and friends) */
function ExtraFolders({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [rows, setRows] = useState<string[]>([]);
  const [focusLast, setFocusLast] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!loads) return;
    const saved = settings.library?.audiobooks_additional_paths;
    setRows(Array.isArray(saved) ? saved.map((p) => text(p)) : []);
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!focusLast) return;
    setFocusLast(false);
    const inputs = list.current?.querySelectorAll<HTMLInputElement>('.audiobook-extra-input');
    inputs?.[inputs.length - 1]?.focus();
  }, [focusLast]);
  const refresh = () => setTimeout(() => shell().SoulSyncFolderCheck?.refresh('audiobooks'), 0);
  // a list that never loaded is never saved over the stored one
  const save = (next: string[]) => {
    if (loads)
      store.set(
        'library',
        'audiobooks_additional_paths',
        next.map((r) => r.trim()).filter(Boolean),
      );
  };
  return (
    <>
      <div id="audiobooks-extra-paths" ref={list}>
        {rows.map((value, i) => (
          <div
            key={i}
            className="audiobook-extra-row"
            data-folder-row=""
            style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px' }}
          >
            <input
              type="text"
              className="audiobook-extra-input"
              value={value}
              placeholder="/app/audiobooks2"
              style={{ flex: '1', minWidth: '0' }}
              aria-label="Extra audiobook folder"
              data-folder-check="audiobooks"
              data-folder-label="Extra audiobook folder"
              data-folder-role="existing"
              onChange={(event) => {
                const next = rows.map((r, k) => (k === i ? event.target.value : r));
                setRows(next);
                save(next);
              }}
            />
            <button
              type="button"
              className="test-button"
              aria-label="Remove this folder"
              onClick={(event) => {
                // folder-check.js's status line for this row goes with it
                const status =
                  event.currentTarget.closest('.audiobook-extra-row')?.nextElementSibling;
                if (status?.classList.contains('folder-check')) status.remove();
                const next = rows.filter((_, k) => k !== i);
                setRows(next);
                save(next);
                refresh();
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        className="test-button"
        onClick={() => {
          setRows([...rows, '']);
          setFocusLast(true);
          refresh();
        }}
      >
        + Add folder
      </button>
    </>
  );
}

type Route = { label?: string; route_key: string };

/** the spoken-word artists list (loadAudiobookRoutes, audiobookRouteAction) */
function RoutesList({ store }: { store: SettingsStore }) {
  const loads = useLoads(store);
  const [routes, setRoutes] = useState<Route[] | 'error' | null>(null);

  async function load() {
    try {
      const { artists = [] } = (await (await fetch('/api/audiobook-routes')).json()) as {
        artists?: Route[];
      };
      setRoutes(artists);
    } catch {
      setRoutes('error');
    }
  }
  useEffect(() => {
    if (loads) void load();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(action: string, key: string) {
    const url = '/api/audiobook-routes/' + encodeURIComponent(key);
    const call = (path: string, init: RequestInit) =>
      fetch(url + path, init).then(async (r) => {
        const data = (await r.json()) as {
          success?: boolean;
          error?: string;
          items?: Array<{ action: string }>;
          moved?: number;
          failed?: number;
        };
        if (!data.success) throw new Error(data.error || 'failed');
        return data;
      });
    const post = (apply: boolean) =>
      call('/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply }),
      });
    const toast = (message: string, type: string) => shell().showToast?.(message, type);
    try {
      if (action === 'remove') {
        await call('', { method: 'DELETE' });
        await load();
        return;
      }
      const items = (await post(false)).items || [];
      const todo = items.filter((i) => i.action === 'move').length;
      const clash = items.filter((i) => i.action === 'exists').length;
      if (!todo) {
        toast(
          clash
            ? `Nothing to move: ${clash} album(s) already exist in the audiobook folder.`
            : 'No albums of this artist in the music library.',
          'info',
        );
        return;
      }
      const confirmed = await shell().showConfirmDialog?.({
        title: 'Move albums',
        message:
          `Move ${todo} album folder(s) from the music library to the audiobook folder? ` +
          `${clash ? `${clash} album(s) already exist there and stay as they are. ` : ''}` +
          'Your media server needs a library scan afterwards.',
        confirmText: 'Move',
      });
      if (!confirmed) return;
      const done = await post(true);
      toast(
        `Moved ${done.moved} album(s)` + (done.failed ? `, ${done.failed} failed` : ''),
        done.failed ? 'error' : 'success',
      );
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), 'error');
    }
  }

  return (
    <div id="audiobook-routes-list">
      {routes === 'error' ? (
        'Could not load the list.'
      ) : routes === null ? null : routes.length ? (
        routes.map((a) => (
          <div className="abr-row" key={a.route_key}>
            <span>{a.label || a.route_key}</span>{' '}
            <button
              className="test-button"
              type="button"
              data-route={a.route_key}
              data-action="move"
              onClick={() => void act('move', a.route_key)}
            >
              Move existing…
            </button>{' '}
            <button
              className="test-button abr-remove"
              type="button"
              data-route={a.route_key}
              data-action="remove"
              onClick={() => void act('remove', a.route_key)}
            >
              &times;
            </button>
          </div>
        ))
      ) : (
        <div className="abr-empty">
          No artists routed. Use “Treat as audiobook” on an artist’s page.
        </div>
      )}
    </div>
  );
}

export function AudiobookFoldersPanel({
  store,
  hidden,
}: {
  store: SettingsStore;
  hidden: boolean;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const path = loads ? audiobooksPath(settings) : '';
  return (
    <div
      id="folders-audiobooks-panel"
      role="tabpanel"
      aria-labelledby="folders-audiobooks-tab"
      className="stg-media-panel"
      hidden={hidden}
      tabIndex={0}
    >
      <div className="stg-media-context">
        <strong>Audiobook folders</strong>
        <span className="stg-media-summary">Where finished books live</span>
      </div>

      <div className="settings-group" data-stg={TAB}>
        <div className="form-group">
          <label>Audiobooks Folder (output):</label>
          <LockedPath
            id="audiobooks-path"
            check={{ 'data-folder-check': 'audiobooks', 'data-folder-label': 'Audiobooks folder' }}
            placeholder={loads && isDocker(settings) ? '/app/audiobooks' : './audiobooks'}
            value={path}
            onChange={(v) => {
              // one folder, saved under both keys, as the collector did
              store.set('audiobooks', 'download_path', v || './audiobooks');
              store.set('library', 'audiobooks_path', v || './audiobooks');
            }}
          />
          {hint(
            'Where downloaded audiobooks are saved and organized. Keep this separate from your music library so a media server does not index chapter files as albums.',
          )}
        </div>
        <div className="form-group">
          <label>More audiobook folders:</label>
          <ExtraFolders store={store} />
          {hint(
            "An existing collection on another drive. SoulSync scans and manages it, but new books always land in the folder above. In Docker, mount it at its own container path, like /app/audiobooks2. If a drive is unplugged, its books stay in your library until it's back.",
          )}
        </div>
      </div>

      <div className="settings-group">
        <div className="form-group">
          <label>Spoken-word artists (music downloads filed as audiobooks):</label>
          <RoutesList store={store} />
          {hint(
            <>
              Children's audio plays and similar releases are found by the music search, not the
              audiobook search. Downloads by the artists listed here are filed in the audiobooks
              folder above instead of the music library, and count as owned so playlist syncs do not
              fetch them again. Add an artist with <strong>Treat as audiobook</strong> on the
              artist's page or in the watchlist settings. Routing only affects new downloads;{' '}
              <em>Move existing…</em> relocates albums already in the music library.
            </>,
          )}
        </div>
      </div>
    </div>
  );
}

// ── Organization > Audiobooks ──

const FORMATS: Array<[string, string]> = [
  ['m4b', 'M4B'],
  ['m4a', 'M4A'],
  ['mp3', 'MP3'],
  ['opus', 'Opus'],
  ['ogg', 'OGG'],
  ['flac', 'FLAC'],
];
const LAYOUTS: Array<[string, string]> = [
  ['single', 'Single file (whole book)'],
  ['multiple', 'Multiple files (chapters or parts)'],
];
const MARKETS: Array<[string, string]> = [
  ['us', 'United States (audible.com)'],
  ['uk', 'United Kingdom (audible.co.uk)'],
  ['de', 'Germany (audible.de)'],
  ['fr', 'France (audible.fr)'],
  ['ca', 'Canada (audible.ca)'],
  ['au', 'Australia (audible.com.au)'],
  ['it', 'Italy (audible.it)'],
  ['es', 'Spain (audible.es)'],
  ['in', 'India (audible.in)'],
  ['jp', 'Japan (audible.co.jp)'],
];

/** the format and layout switches as loadSettingsData set them: an empty
 *  allowed list is every format; "any" layout is both switches */
export function qualitySwitches(quality: Section) {
  const allowed = Array.isArray(quality.allowed_formats)
    ? (quality.allowed_formats as unknown[]).map((f) => text(f))
    : [];
  const layout = text(quality.file_layout) || 'any';
  return {
    formats: FORMATS.map(([v]) => v).filter((v) => !allowed.length || allowed.includes(v)),
    layouts: LAYOUTS.map(([v]) => v).filter((v) => layout === 'any' || layout === v),
  };
}

export function AudiobookOrganizationPanel({
  store,
  hidden,
  template,
}: {
  store: SettingsStore;
  hidden: boolean;
  /** the audiobook template field: the card owns every template (Reset) */
  template: { value: string; onChange: (event: { target: { value: string } }) => void };
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const ab = (settings.audiobooks || {}) as Section;
  const source = (ab.download_source || {}) as Section;
  const quality = (ab.quality || {}) as Section;
  const mode = text(source.mode) || 'hybrid';
  const set = (key: string, value: unknown) => store.set('audiobooks', key, value);
  const on = (value: unknown) => value !== false;

  const order =
    Array.isArray(quality.format_order) && quality.format_order.length
      ? (quality.format_order as unknown[]).map((f) => text(f))
      : FORMATS.map(([v]) => v);

  // the switches keep what was clicked (every box off is allowed, and means
  // all), and save what it means; a load puts the saved state back
  const [switches, setSwitches] = useState(() => qualitySwitches(quality));
  useEffect(() => setSwitches(qualitySwitches(quality)), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  function toggleFormat(value: string, checked: boolean) {
    const formats = FORMATS.map(([v]) => v).filter((v) =>
      v === value ? checked : switches.formats.includes(v),
    );
    setSwitches({ ...switches, formats });
    // every format on is stored as [] ("no restriction")
    set('quality.allowed_formats', formats.length === FORMATS.length ? [] : formats);
  }
  function toggleLayout(value: string, checked: boolean) {
    const layouts = LAYOUTS.map(([v]) => v).filter((v) =>
      v === value ? checked : switches.layouts.includes(v),
    );
    setSwitches({ ...switches, layouts });
    // exactly one on picks it; both on, or both off, means either
    set('quality.file_layout', layouts.length === 1 ? layouts[0] : 'any');
  }

  const keepDays = useLoadedDraft(store, text(ab.recycle_keep_days ?? 7), (raw) =>
    set('recycle_keep_days', Math.min(365, Math.max(0, int(raw, 0)))),
  );
  const minRate = useLoadedDraft(store, text(quality.min_bitrate_kbps ?? 0), (raw) =>
    set('quality.min_bitrate_kbps', Math.max(0, int(raw, 0))),
  );
  const maxRate = useLoadedDraft(store, text(quality.max_bitrate_kbps ?? 0), (raw) =>
    set('quality.max_bitrate_kbps', Math.max(0, int(raw, 0))),
  );
  const category = useLoadedDraft(store, text(ab.torrent_category) || 'audiobooks', (raw) => {
    // one category covers both clients, as before
    const value = raw.trim() || 'audiobooks';
    store.patch('audiobooks', { torrent_category: value, usenet_category: value });
  });
  const prowlarr = useLoadedDraft(
    store,
    (Array.isArray(ab.prowlarr_categories) ? (ab.prowlarr_categories as unknown[]) : [3030])
      .map((c) => text(c))
      .join(', '),
    (raw) =>
      set(
        'prowlarr_categories',
        raw
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean)
          .map((n) => parseInt(n, 10))
          .filter((n) => Number.isFinite(n)),
      ),
  );
  const tolerance = useLoadedDraft(
    store,
    text(
      Math.round(
        (typeof ab.completeness_tolerance === 'number' ? ab.completeness_tolerance : 0.92) * 100,
      ),
    ),
    (raw) => set('completeness_tolerance', Math.min(1, Math.max(0.1, int(raw, 92) / 100))),
  );
  const staging = useLoadedDraft(store, text(ab.staging_days ?? 7), (raw) =>
    set('staging_days', Math.min(90, Math.max(1, int(raw, 7)))),
  );

  const check = (
    id: string,
    checked: boolean,
    save: (value: boolean) => void,
    label: string,
    help: ReactNode,
  ) => (
    <HintGroup help={help}>
      <label className="checkbox-label">
        <input
          type="checkbox"
          id={id}
          checked={checked}
          onChange={(event) => save(event.target.checked)}
        />{' '}
        <span>{label}</span>
      </label>
    </HintGroup>
  );
  const number = (
    id: string,
    draft: { value: string; onChange: (raw: string) => void },
    attrs: { min: string; max: string; placeholder: string },
  ) => (
    <input
      type="number"
      id={id}
      min={attrs.min}
      max={attrs.max}
      step="1"
      placeholder={attrs.placeholder}
      value={loads ? draft.value : ''}
      onChange={(event) => draft.onChange(event.target.value)}
    />
  );
  const unit = (text: string) => <span className="settings-unit">{text}</span>;

  return (
    <div
      id="organization-audiobooks-panel"
      role="tabpanel"
      aria-labelledby="organization-audiobooks-tab"
      className="stg-media-panel"
      hidden={hidden}
      tabIndex={0}
    >
      <div className="stg-media-context">
        <strong>Audiobook organization</strong>
        <span className="stg-media-summary">Naming, quality, tags &amp; recycle</span>
      </div>

      <div className="settings-group" data-stg={TAB}>
        <HintGroup
          help={
            <>
              Variables: <code>$author</code>, <code>$authorletter</code>, <code>$narrator</code>,{' '}
              <code>$title</code>, <code>$series</code>, <code>$seriespos</code> (01),{' '}
              <code>$year</code>, <code>$asin</code>. Default:{' '}
              <code>$author/$series/$seriespos - $title</code>. Books with no series collapse the
              series folder and its number cleanly, the same way podcast season folders do. Use{' '}
              <code>{'${var}'}</code> to append text.
            </>
          }
        >
          <label>Audiobook Path Template:</label>
          <input
            type="text"
            id="template-audiobook-path"
            placeholder="$author/$series/$seriespos - $title"
            {...template}
          />
        </HintGroup>

        {check(
          'audiobook-recycle-deletes',
          on(ab.recycle_deletes),
          (v) => set('recycle_deletes', v),
          'Recycle deleted audiobooks',
          <>
            Deleting a book MOVES it to a hidden <code>.deleted</code> folder inside your audiobook
            library instead of erasing it, so a mis-click can be undone. The folder is hidden so
            Audiobookshelf, Plex and Jellyfin do not keep showing books you removed. Note the space
            is not freed until the bin is emptied. Off, deletes are permanent immediately.
          </>,
        )}

        <HintGroup
          help={
            <>
              How long a deleted book stays recoverable before the daily cleanup erases it for good.{' '}
              <strong>0 turns the bin off</strong> &mdash; it does not mean "erase everything now",
              because purging on that reading would destroy what was recycled while the bin was
              still on. Default: <code>7</code>
            </>
          }
        >
          <label>Empty The Recycle Bin After:</label>
          {number('audiobook-recycle-keep-days', keepDays, {
            min: '0',
            max: '365',
            placeholder: '7',
          })}
          {unit('days')}
        </HintGroup>

        <h4 className="settings-subheading">Audiobook Quality</h4>
        {hint(
          <>
            What counts as a good file. One profile for every audiobook &mdash; the per-author card
            carries the settings that differ by author.
          </>,
        )}

        <HintGroup
          help={
            <>
              Ranked first when several releases are otherwise equal; the rest keep their usual
              order behind it. <strong>M4B</strong> is the only format built for audiobooks &mdash;
              chapters, bookmarks, and one file instead of ninety. Pick <strong>MP3</strong> if your
              player cannot read M4B. Format never outweighs having the right book and the right
              narrator.
            </>
          }
        >
          <label>Preferred Format:</label>
          <select
            id="audiobook-format-first"
            className="form-select"
            value={order[0] || 'm4b'}
            onChange={(event) => {
              // the chosen format leads; the default order follows behind it
              const first = event.target.value;
              set('quality.format_order', [
                first,
                ...FORMATS.map(([v]) => v).filter((f) => f !== first),
              ]);
            }}
          >
            {FORMATS.map(([v, label]) => (
              <option key={v} value={v}>
                {v === 'm4b' ? 'M4B (recommended)' : label}
              </option>
            ))}
          </select>
        </HintGroup>

        <HintGroup help="Releases in a format that is switched off are never shown or downloaded. Preferred Format only ranks, so with M4B preferred an MP3 is still grabbed when no M4B turns up; switch MP3 off here to wait for an M4B instead. A release whose format cannot be told from its title is still allowed. Switching every format off is the same as allowing them all.">
          <label>Allowed Formats:</label>
          <div
            id="audiobook-allowed-formats"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, minmax(0, max-content))',
              gap: '0 24px',
            }}
          >
            {FORMATS.map(([v, label]) => (
              <label className="checkbox-label" key={v}>
                <input
                  type="checkbox"
                  value={v}
                  checked={loads ? switches.formats.includes(v) : false}
                  onChange={(event) => toggleFormat(v, event.target.checked)}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </HintGroup>

        <HintGroup
          help={
            <>
              Whether a book should arrive as <strong>one file</strong> or as{' '}
              <strong>several smaller files</strong>, usually one per chapter. Switch one off and
              releases laid out that way are skipped; leave both on (or both off) for either. Only
              Soulseek says how many files a release has before it is downloaded; torrents and
              usenet do not, so they are always allowed.
            </>
          }
        >
          <label>File Layout:</label>
          <div
            id="audiobook-file-layout"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, minmax(0, max-content))',
              gap: '0 24px',
            }}
          >
            {LAYOUTS.map(([v, label]) => (
              <label className="checkbox-label" key={v}>
                <input
                  type="checkbox"
                  value={v}
                  checked={loads ? switches.layouts.includes(v) : false}
                  onChange={(event) => toggleLayout(v, event.target.checked)}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </HintGroup>

        <HintGroup
          help={
            <>
              Hides releases below this bitrate. <strong>0 means no opinion.</strong> This is taste,
              and separate from the completeness floor above: that one asks whether a release could
              be the whole book at all, this one asks whether you want it. Audible's own files are
              32 kbps mono and 64 kbps stereo, so 32 is a reasonable floor and anything above 128 is
              more than speech needs.
            </>
          }
        >
          <label>Reject Below:</label>
          {number('audiobook-min-bitrate', minRate, { min: '0', max: '512', placeholder: '0' })}
          {unit('kbps')}
        </HintGroup>

        <HintGroup
          help={
            <>
              Hides releases above this bitrate, for when disk space matters more than headroom.{' '}
              <strong>0 means no opinion.</strong> Speech gains nothing audible above about 128
              kbps, so a 45-hour book at 240 kbps is roughly 4GB for no benefit.
            </>
          }
        >
          <label>Reject Above:</label>
          {number('audiobook-max-bitrate', maxRate, { min: '0', max: '2000', placeholder: '0' })}
          {unit('kbps')}
        </HintGroup>

        {check(
          'audiobook-allow-dramatized',
          on(quality.allow_dramatized),
          (v) => set('quality.allow_dramatized', v),
          'Show dramatised adaptations',
          <>
            GraphicAudio and similar full-cast productions with music and sound effects. They are a
            different product from the audiobook &mdash; different cast, different running time,
            usually sold in several separately-downloaded parts. Left on they are shown but ranked
            below real readings and clearly labelled; turned off they are hidden entirely.
          </>,
        )}

        <h4 className="settings-subheading">Downloads &amp; Import</h4>
        {hint(
          <>
            Books use the same torrent, usenet and Soulseek clients as music &mdash; set those up
            under Sources.
          </>,
        )}

        <HintGroup
          help={
            <>
              Which Audible storefront the audiobook catalogue pages, author scans and follow checks
              query. Some titles are exclusive to one country's store, so picking the store you buy
              from finds books the US store never lists. Default: <code>us</code>
            </>
          }
        >
          <label>Audible Marketplace:</label>
          <select
            id="audiobook-marketplace"
            className="form-select"
            value={text(ab.marketplace) || 'us'}
            onChange={(event) => set('marketplace', event.target.value)}
          >
            {MARKETS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </HintGroup>

        {/* the Audiobooks tab of the chain widget (Settings > Downloads) IS this
            setting. Kept hidden, as before */}
        <HintGroup
          hidden
          help={
            <>
              Which sources a book search asks. Separate from the music chain on purpose &mdash;
              Tidal, Qobuz, HiFi and Deezer carry no audiobooks at all, so a book on the music chain
              would burn an attempt on each of them before it could succeed. The clients themselves
              are the same ones music uses; only the order and the category differ.
            </>
          }
        >
          <label>Audiobook Download Sources:</label>
          <select
            id="audiobook-download-mode"
            className="form-select"
            value={loads ? mode : 'soulseek'}
            onChange={(event) => set('download_source.mode', event.target.value)}
          >
            <option value="soulseek">Soulseek Only</option>
            <option value="torrent">Torrent Only (via Prowlarr)</option>
            <option value="usenet">Usenet Only (via Prowlarr)</option>
            <option value="hybrid">Hybrid (Primary + Fallback)</option>
          </select>
        </HintGroup>

        {/* the arrow-button list the chain widget replaced: kept empty and
            hidden, as the old one only ever drew into a hidden list */}
        <div
          id="audiobook-hybrid-container"
          style={{ display: loads && mode === 'hybrid' ? '' : 'none' }}
        >
          <div className="hybrid-source-list" id="audiobook-hybrid-rows" hidden></div>
        </div>

        <HintGroup
          help={
            <>
              The category SoulSync tags audiobook torrents and NZBs with in your download client.
              Keep it different from your music category so a finished book is never picked up by
              anything watching for music. Default: <code>audiobooks</code>
            </>
          }
        >
          <label>Audiobook Downloader Category:</label>
          <input
            type="text"
            id="audiobook-torrent-category"
            placeholder="audiobooks"
            value={loads ? category.value : ''}
            onChange={(event) => category.onChange(event.target.value)}
          />
        </HintGroup>

        <HintGroup
          help={
            <>
              Newznab category IDs searched for audiobooks. <code>3030</code> is the standard
              audiobook category and is deliberately kept OUT of music searches, so asking for it
              here costs the music side nothing. Only change this if your indexer uses a
              non-standard ID.
            </>
          }
        >
          <label>Audiobook Prowlarr Categories:</label>
          <input
            type="text"
            id="audiobook-prowlarr-categories"
            placeholder="3030"
            value={loads ? prowlarr.value : ''}
            onChange={(event) => prowlarr.onChange(event.target.value)}
          />
        </HintGroup>

        <HintGroup
          help={
            <>
              How much of the runtime Audible publishes has to actually be on disk before a book is
              filed into your library. A download client reports "complete" when the files it was{' '}
              <em>asked</em> for finished, which is not the same as the book being whole &mdash; and
              a book missing its last chapters plays perfectly until you run out of it. Short books
              are held in staging, never failed outright. Default: <code>92</code>
            </>
          }
        >
          <label>Import When Runtime Reaches:</label>
          {number('audiobook-completeness-tolerance', tolerance, {
            min: '10',
            max: '100',
            placeholder: '92',
          })}
          {unit('%')}
        </HintGroup>

        <HintGroup
          help={
            <>
              How long an incomplete book is kept staged before it is failed and sent back to the
              wishlist. Torrents finish late and uploaders repair releases, so patience is right
              here; forever would mean one broken release holds a wishlist row for good. Default:{' '}
              <code>7</code>
            </>
          }
        >
          <label>Give Up On A Short Book After:</label>
          {number('audiobook-staging-days', staging, { min: '1', max: '90', placeholder: '7' })}
          {unit('days')}
        </HintGroup>

        {check(
          'audiobook-renumber-chapters',
          on(ab.renumber_chapters),
          (v) => set('renumber_chapters', v),
          'Renumber chapter files on import',
          "Chapter files arrive named however the uploader left them. On by default: a book whose files sort wrong plays wrong. Turn this off only if you trust your releases' own numbering.",
        )}
        {check(
          'audiobook-merge-m4b',
          ab.merge_m4b === true,
          (v) => set('merge_m4b', v),
          'Merge multi-file books into one M4B on import',
          <>
            Off by default: when a book arrives as many files, combine them into a single{' '}
            <code>.m4b</code> with one chapter marker per file during import. Needs{' '}
            <code>ffmpeg</code> on the server (the Docker image ships it). The originals are only
            removed after the merged file verifies, so a failed merge never loses the book.
          </>,
        )}
        {check(
          'audiobook-remove-owned-from-wishlist',
          ab.remove_owned_from_wishlist === true,
          (v) => set('remove_owned_from_wishlist', v),
          "Remove books from the wishlist once they're in the library",
          'Off by default: a book that is downloaded or found in your library stays on the wishlist, marked "In library". Turn this on to drop it instead, the way the music wishlist does.',
        )}
        {check(
          'audiobook-embed-metadata',
          on(ab.embed_metadata),
          (v) => set('embed_metadata', v),
          'Write tags into audiobook files',
          'Embeds title, author, series and narrator into each file on import. Narrator goes in the COMPOSER field, which is the convention every audiobook server settled on. Without this a book keeps whatever the uploader\'s ripper wrote, which is usually chapter 4 of a 27-hour book tagged as a track on an album called "audiobook".',
        )}
        {check(
          'audiobook-embed-artwork',
          on(ab.embed_artwork),
          (v) => set('embed_artwork', v),
          'Embed cover art in audiobook files',
          'Embeds the cover into every chapter file. A cover the release already shipped is preferred over fetching one, so this still works offline.',
        )}
        {check(
          'audiobook-save-artwork',
          on(ab.save_artwork),
          (v) => set('save_artwork', v),
          'Save cover.jpg beside audiobooks',
          <>
            Writes <code>cover.jpg</code> into the book folder. Plex, Jellyfin, Audiobookshelf and
            Booksonic all read it from there.
          </>,
        )}
        {check(
          'audiobook-write-nfo',
          on(ab.write_nfo),
          (v) => set('write_nfo', v),
          'Write audiobook metadata sidecars',
          <>
            Writes <code>metadata.opf</code> (Audiobookshelf, Plex, Calibre) and{' '}
            <code>book.nfo</code> (Kodi, Jellyfin, Emby). Both, because the servers genuinely
            disagree about which to look for and they are a few hundred bytes each. These also carry
            the ASIN, which is what lets the daily library scan recognise a book you imported months
            ago.
          </>,
        )}
      </div>
    </div>
  );
}
