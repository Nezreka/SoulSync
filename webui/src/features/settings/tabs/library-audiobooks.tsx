/**
 * The Library tab's Audiobooks kind (settings-bic phase 4): the folders (the
 * folder, more folders, the spoken-word artists), the naming template, what
 * happens on import (completeness, staging, chapters, M4B, the wishlist, the
 * recycle bin) and what is written into a book (tags, covers, sidecars). The
 * three download options (Audible marketplace, downloader category, Prowlarr
 * categories) are drawn on Downloads > Sources, Audiobooks (DESIGN 3.3), by
 * AudiobookDownloadOptions here. Same ids and keys.
 *
 * The hidden "Audiobook Download Sources" select and its empty hybrid rows
 * went (DESIGN 3.3): the audiobook order on Downloads writes that key
 * (audiobooks.download_source, sources-model.ts).
 *
 * audiobooks.quality and audiobooks.download_source are dicts: each key is
 * saved alone as a dotted key, the rest of the dict kept (settings-store.ts).
 */

import { useEffect, useState } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  Btn,
  Card,
  Items,
  NumberField,
  Row,
  SectionLabel,
  Select,
  Switch,
  TemplateField,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
import {
  FolderRows,
  LibHero,
  LibPath,
  checkAttrs,
  int,
  isDocker,
  shell,
  toast,
  useLoadedDraft,
  useLoads,
  useSettings,
} from './library-card';
import { useTemplateField } from './library-organization';
import {
  audiobookFoldersSentence,
  audiobookImportSentence,
  audiobookNamingSentence,
  audiobookTagsSentence,
} from './library/words';
import { templatesOf } from './organization';
import { text } from './source-panels';

type Section = Record<string, unknown>;

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

const on = (value: unknown) => value !== false;

// ── Folders > Audiobooks ──

type Route = { label?: string; route_key: string };

/** the spoken-word artists, loaded with each settings load (the hero counts them) */
let routesNow: Route[] | 'error' | null = null;
const routeListeners = new Set<() => void>();
function setRoutes(next: Route[] | 'error') {
  routesNow = next;
  for (const listener of routeListeners) listener();
}
async function loadRoutes() {
  try {
    const { artists = [] } = (await (await fetch('/api/audiobook-routes')).json()) as {
      artists?: Route[];
    };
    setRoutes(artists);
  } catch {
    setRoutes('error');
  }
}
function useRoutes() {
  const [, bump] = useState(0);
  useEffect(() => {
    const listener = () => bump((n) => n + 1);
    routeListeners.add(listener);
    return () => {
      routeListeners.delete(listener);
    };
  }, []);
  return routesNow;
}
/** for tests: the next page asks again */
export function resetRoutes() {
  routesNow = null;
}

/** the spoken-word artists list (loadAudiobookRoutes, audiobookRouteAction) */
function RoutesList({ store }: { store: SettingsStore }) {
  const loads = useLoads(store);
  const routes = useRoutes();
  useEffect(() => {
    if (loads) void loadRoutes();
  }, [loads]);

  async function act(action: string, key: string, label: string) {
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
    try {
      if (action === 'remove') {
        // a route taken off is a choice someone made on an artist's page
        const confirm = shell().showConfirmDialog;
        if (confirm) {
          const ok = await confirm({
            title: `Stop filing ${label} as audiobooks?`,
            message:
              'New downloads by this artist go to the music library again. Albums already moved stay where they are.',
            confirmText: 'Stop',
            cancelText: 'Keep it',
            destructive: true,
          });
          if (!ok) return;
        }
        await call('', { method: 'DELETE' });
        await loadRoutes();
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
        <p className={s.listEmpty}>Could not load the list.</p>
      ) : routes === null ? null : routes.length ? (
        <Items>
          {routes.map((a) => {
            const label = a.label || a.route_key;
            return (
              <div className={s.routeRow} key={a.route_key} data-route-row={a.route_key}>
                <span className={s.routeName}>{label}</span>
                <Btn
                  small
                  data-route={a.route_key}
                  data-action="move"
                  onClick={() => void act('move', a.route_key, label)}
                >
                  Move existing…
                </Btn>
                <Btn
                  kind="ghost"
                  small
                  iconOnly
                  icon="x"
                  data-route={a.route_key}
                  data-action="remove"
                  aria-label={`Stop filing ${label} as audiobooks`}
                  onClick={() => void act('remove', a.route_key, label)}
                />
              </div>
            );
          })}
        </Items>
      ) : (
        <p className={s.listEmpty}>
          No artists routed. Use &ldquo;Treat as audiobook&rdquo; on an artist&apos;s page.
        </p>
      )}
    </div>
  );
}

export function AudiobookFoldersHero({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const routes = useRoutes();
  const extra = ((settings.library?.audiobooks_additional_paths as unknown[] | undefined) || [])
    .map((p) => text(p).trim())
    .filter(Boolean).length;
  return (
    <LibHero
      parts={
        loads
          ? audiobookFoldersSentence({
              path: audiobooksPath(settings),
              extra,
              routed: Array.isArray(routes) ? routes.length : null,
            })
          : [{ text: 'Loading your folders.' }]
      }
    />
  );
}

export function AudiobookFolders({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [rows, setRows] = useState<string[]>([]);
  useEffect(() => {
    if (!loads) return;
    const saved = settings.library?.audiobooks_additional_paths;
    setRows(Array.isArray(saved) ? saved.map((p) => text(p)) : []);
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  // a list that never loaded is never saved over the stored one
  const save = (next: string[]) => {
    setRows(next);
    if (loads)
      store.set(
        'library',
        'audiobooks_additional_paths',
        next.map((r) => r.trim()).filter(Boolean),
      );
  };
  return (
    <>
      <Card>
        <Row
          wide
          label="Audiobooks folder"
          htmlFor="audiobooks-path"
          help="Where downloaded audiobooks are saved and organized."
          more="Keep it apart from your music library so a media server does not index chapter files as albums."
        >
          <LibPath
            store={store}
            id="audiobooks-path"
            check={checkAttrs('audiobooks', 'Audiobooks folder')}
            placeholder={loads && isDocker(settings) ? '/app/audiobooks' : './audiobooks'}
            value={loads ? audiobooksPath(settings) : ''}
            onChange={(v) => {
              // one folder, saved under both keys, as the collector did
              store.set('audiobooks', 'download_path', v || './audiobooks');
              store.set('library', 'audiobooks_path', v || './audiobooks');
            }}
          />
        </Row>
        <Row
          stack
          label="More audiobook folders"
          help="An existing collection on another drive. SoulSync scans and manages it; new books always land in the folder above."
          more="In Docker, mount it at its own container path, like /app/audiobooks2. If a drive is unplugged, its books stay in your library until it's back."
        >
          <FolderRows
            id="audiobooks-extra-paths"
            rows={rows}
            onRows={save}
            placeholder="/app/audiobooks2"
            itemLabel="Extra audiobook folder"
            check={checkAttrs('audiobooks', 'Extra audiobook folder', 'existing')}
          />
        </Row>
      </Card>

      <SectionLabel
        title="Spoken-word artists"
        hint="Music downloads by these artists are filed as audiobooks"
      />
      <Card>
        <div className={s.cardPad}>
          <RoutesList store={store} />
        </div>
        <p className={s.cardFoot}>
          Children&apos;s audio plays and similar releases are found by the music search, not the
          audiobook search. Downloads by the artists here go to the audiobooks folder instead of the
          music library, and count as owned so playlist syncs don&apos;t fetch them again. Add one
          with <strong>Treat as audiobook</strong> on the artist&apos;s page or in the watchlist
          settings. Only new downloads follow; <em>Move existing…</em> moves albums already in the
          music library.
        </p>
      </Card>
    </>
  );
}

// ── Naming > Audiobooks ──

export function AudiobookNamingHero({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  return (
    <LibHero
      parts={
        loads
          ? audiobookNamingSentence(templatesOf(settings).audiobook_path)
          : [{ text: 'Loading your naming templates.' }]
      }
    />
  );
}

export function AudiobookNaming({ store }: { store: SettingsStore }) {
  const field = useTemplateField(store, 'audiobook_path');
  return (
    <Card>
      <Row
        stack
        label="Audiobook path template"
        htmlFor="template-audiobook-path"
        help="A book with no series drops the series folder and its number cleanly."
        more={
          <>
            Variables: <code>$author</code>, <code>$authorletter</code>, <code>$narrator</code>,{' '}
            <code>$title</code>, <code>$series</code>, <code>$seriespos</code> (01),{' '}
            <code>$year</code>, <code>$asin</code>. Default:{' '}
            <code>$author/$series/$seriespos - $title</code>. Use <code>{'${var}'}</code> to put
            text right after a variable.
          </>
        }
      >
        <TemplateField
          id="template-audiobook-path"
          placeholder="$author/$series/$seriespos - $title"
          value={field.value}
          onChange={field.onChange}
        />
      </Row>
    </Card>
  );
}

// ── Import and files > Audiobooks ──

function useAudiobookImport(store: SettingsStore) {
  const settings = useSettings(store);
  const ab = (settings.audiobooks || {}) as Section;
  const percent = Math.round(
    (typeof ab.completeness_tolerance === 'number' ? ab.completeness_tolerance : 0.92) * 100,
  );
  return { ab, percent };
}

export function AudiobookImportHero({ store }: { store: SettingsStore }) {
  const loads = useLoads(store);
  const { ab, percent } = useAudiobookImport(store);
  return (
    <LibHero
      parts={
        loads
          ? audiobookImportSentence({
              percent,
              days: int(text(ab.staging_days ?? 7), 7),
              merge: ab.merge_m4b === true,
            })
          : [{ text: 'Loading.' }]
      }
    />
  );
}

export function AudiobookImport({ store }: { store: SettingsStore }) {
  const { ab, percent } = useAudiobookImport(store);
  const set = (key: string, value: unknown) => store.set('audiobooks', key, value);
  const tolerance = useLoadedDraft(store, text(percent), (raw) =>
    set('completeness_tolerance', Math.min(1, Math.max(0.1, int(raw, 92) / 100))),
  );
  const staging = useLoadedDraft(store, text(ab.staging_days ?? 7), (raw) =>
    set('staging_days', Math.min(90, Math.max(1, int(raw, 7)))),
  );
  const keepDays = useLoadedDraft(store, text(ab.recycle_keep_days ?? 7), (raw) =>
    set('recycle_keep_days', Math.min(365, Math.max(0, int(raw, 0)))),
  );
  const recycle = on(ab.recycle_deletes);
  return (
    <>
      <Card title="When a book arrives">
        <Row
          field
          label="Import when the runtime reaches"
          htmlFor="audiobook-completeness-tolerance"
          help="How much of Audible's runtime has to be on disk before a book is filed into your library. Short books wait in staging, never failed outright."
          more={
            <>
              A download client says &ldquo;complete&rdquo; when the files it was <em>asked</em> for
              finished, which is not the same as the book being whole, and a book missing its last
              chapters plays perfectly until you run out of it. Default: <code>92</code>
            </>
          }
        >
          <NumberField
            id="audiobook-completeness-tolerance"
            min="10"
            max="100"
            step="1"
            placeholder="92"
            unit="%"
            value={tolerance.value}
            onChange={(event) => tolerance.onChange(event.target.value)}
          />
        </Row>
        <Row
          field
          label="Give up on a short book after"
          htmlFor="audiobook-staging-days"
          help="How long an incomplete book waits in staging before it fails and goes back to the wishlist."
          more={
            <>
              Torrents finish late and uploaders repair releases, so patience is right here; forever
              would mean one broken release holds a wishlist row for good. Default: <code>7</code>
            </>
          }
        >
          <NumberField
            id="audiobook-staging-days"
            min="1"
            max="90"
            step="1"
            placeholder="7"
            unit="days"
            value={staging.value}
            onChange={(event) => staging.onChange(event.target.value)}
          />
        </Row>
        <Row
          label="Renumber chapter files on import"
          htmlFor="audiobook-renumber-chapters"
          help="Chapter files arrive named however the uploader left them; a book whose files sort wrong plays wrong."
          more="On by default. Turn it off only if you trust your releases' own numbering."
        >
          <Switch
            id="audiobook-renumber-chapters"
            checked={on(ab.renumber_chapters)}
            onChange={(event) => set('renumber_chapters', event.target.checked)}
          />
        </Row>
        <Row
          label="Merge multi-file books into one M4B on import"
          htmlFor="audiobook-merge-m4b"
          help="A book in many files becomes one .m4b with a chapter marker per file. Needs ffmpeg on the server."
          more="Off by default. The Docker image ships ffmpeg. The originals are only removed after the merged file checks out, so a failed merge never loses the book."
        >
          <Switch
            id="audiobook-merge-m4b"
            checked={ab.merge_m4b === true}
            onChange={(event) => set('merge_m4b', event.target.checked)}
          />
        </Row>
        <Row
          label="Remove books from the wishlist once they're in the library"
          htmlFor="audiobook-remove-owned-from-wishlist"
          help={
            'Off by default: a book you have stays on the wishlist, marked "In library". On drops it, the way the music wishlist does.'
          }
        >
          <Switch
            id="audiobook-remove-owned-from-wishlist"
            checked={ab.remove_owned_from_wishlist === true}
            onChange={(event) => set('remove_owned_from_wishlist', event.target.checked)}
          />
        </Row>
      </Card>

      <Card title="Deleting">
        <Row
          label="Recycle deleted audiobooks"
          htmlFor="audiobook-recycle-deletes"
          help="A deleted book moves to a hidden .deleted folder in your audiobook library, so a mis-click can be undone."
          more="The folder is hidden so Audiobookshelf, Plex and Jellyfin don't keep showing books you removed. The space is freed when the bin is emptied. Off, deletes are permanent at once."
        >
          <Switch
            id="audiobook-recycle-deletes"
            checked={recycle}
            onChange={(event) => set('recycle_deletes', event.target.checked)}
          />
        </Row>
        <Row
          sub
          field
          off={!recycle}
          label="Empty the recycle bin after"
          htmlFor="audiobook-recycle-keep-days"
          help="How long a deleted book can be brought back before the daily cleanup erases it. 0 turns the bin off."
          more={
            <>
              0 does not mean &ldquo;erase everything now&rdquo;: that would destroy what was
              recycled while the bin was still on. Default: <code>7</code>
            </>
          }
        >
          <NumberField
            id="audiobook-recycle-keep-days"
            min="0"
            max="365"
            step="1"
            placeholder="7"
            unit="days"
            value={keepDays.value}
            onChange={(event) => keepDays.onChange(event.target.value)}
          />
        </Row>
      </Card>
    </>
  );
}

// ── Tags and artwork > Audiobooks ──

export function AudiobookTagsHero({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const ab = (settings.audiobooks || {}) as Section;
  return (
    <LibHero
      parts={
        loads
          ? audiobookTagsSentence({
              tags: on(ab.embed_metadata),
              embed: on(ab.embed_artwork),
              cover: on(ab.save_artwork),
              sidecars: on(ab.write_nfo),
            })
          : [{ text: 'Loading.' }]
      }
    />
  );
}

export function AudiobookTags({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const ab = (settings.audiobooks || {}) as Section;
  const set = (key: string, value: unknown) => store.set('audiobooks', key, value);
  return (
    <Card>
      <Row
        label="Write tags into audiobook files"
        htmlFor="audiobook-embed-metadata"
        help="Title, author, series and narrator go into each file on import. The narrator goes in COMPOSER, as every audiobook server expects."
        more={
          'Without this a book keeps whatever the uploader\'s ripper wrote, which is usually chapter 4 of a 27-hour book tagged as a track on an album called "audiobook".'
        }
      >
        <Switch
          id="audiobook-embed-metadata"
          checked={on(ab.embed_metadata)}
          onChange={(event) => set('embed_metadata', event.target.checked)}
        />
      </Row>
      <Row
        label="Embed cover art in audiobook files"
        htmlFor="audiobook-embed-artwork"
        help="The cover goes into every chapter file. A cover the release shipped is used first, so this works offline."
      >
        <Switch
          id="audiobook-embed-artwork"
          checked={on(ab.embed_artwork)}
          onChange={(event) => set('embed_artwork', event.target.checked)}
        />
      </Row>
      <Row
        label="Save cover.jpg beside audiobooks"
        htmlFor="audiobook-save-artwork"
        help="Plex, Jellyfin, Audiobookshelf and Booksonic all read it from the book folder."
      >
        <Switch
          id="audiobook-save-artwork"
          checked={on(ab.save_artwork)}
          onChange={(event) => set('save_artwork', event.target.checked)}
        />
      </Row>
      <Row
        label="Write audiobook metadata sidecars"
        htmlFor="audiobook-write-nfo"
        help="metadata.opf (Audiobookshelf, Plex, Calibre) and book.nfo (Kodi, Jellyfin, Emby)."
        more="Both, because the servers disagree about which to look for, and they are a few hundred bytes each. They also carry the ASIN, which lets the daily library scan recognise a book you imported months ago."
      >
        <Switch
          id="audiobook-write-nfo"
          checked={on(ab.write_nfo)}
          onChange={(event) => set('write_nfo', event.target.checked)}
        />
      </Row>
    </Card>
  );
}

// ── Downloads > Sources > Audiobooks: where books come from ──

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

export function AudiobookDownloadOptions({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const ab = (settings.audiobooks || {}) as Section;
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
      store.set(
        'audiobooks',
        'prowlarr_categories',
        raw
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean)
          .map((n) => parseInt(n, 10))
          .filter((n) => Number.isFinite(n)),
      ),
  );
  return (
    <Card
      title="Audiobook download options"
      hint="Books use the same torrent, usenet and Soulseek clients as music; only these differ"
      id="audiobook-download-options"
    >
      <Row
        field
        label="Audible marketplace"
        htmlFor="audiobook-marketplace"
        help="The Audible store the catalogue pages, author scans and follow checks ask."
        more="Some titles are only in one country's store, so the store you buy from finds books the US store never lists. Default: us"
      >
        <Select
          id="audiobook-marketplace"
          value={text(ab.marketplace) || 'us'}
          onChange={(event) => store.set('audiobooks', 'marketplace', event.target.value)}
          options={MARKETS.map(([value, label]) => ({ value, label }))}
        />
      </Row>
      <Row
        field
        label="Audiobook downloader category"
        htmlFor="audiobook-torrent-category"
        help="The category audiobook torrents and NZBs get in your download client."
        more="Keep it different from your music category so a finished book is never picked up by anything watching for music. Default: audiobooks"
      >
        <TextField
          id="audiobook-torrent-category"
          placeholder="audiobooks"
          value={loads ? category.value : ''}
          onChange={(event) => category.onChange(event.target.value)}
        />
      </Row>
      <Row
        field
        label="Audiobook Prowlarr categories"
        htmlFor="audiobook-prowlarr-categories"
        help="Newznab category IDs searched for audiobooks, separated by commas."
        more="3030 is the standard audiobook category and is kept out of music searches on purpose, so asking for it here costs music nothing. Change it only if your indexer uses another ID."
      >
        <TextField
          id="audiobook-prowlarr-categories"
          placeholder="3030"
          value={loads ? prowlarr.value : ''}
          onChange={(event) => prowlarr.onChange(event.target.value)}
        />
      </Row>
    </Card>
  );
}

/** the format and layout switches: Quality > Audiobooks has them now */
export { qualitySwitches } from './quality/audiobooks-view';
