/**
 * Library > Naming, Music and Podcasts (settings-bic phase 4, mockup 03): the
 * naming templates, each with the path it produces right under it ("Saves
 * as", the server's own path builder against an example release, #1868),
 * the example switcher, the variables, the folder details and Library
 * Organize. Also the pieces DESIGN 3.3 moved out of the old Organization
 * card: the three tag options (Tags and artwork), the wishlist rules
 * (Downloads > Behaviour), the podcast media format and the re-download rule
 * (Import and files). Same ids and keys.
 *
 * Templates are one dict on the server (file_organization.templates), and so
 * are the tag options (metadata_enhancement.tags): each key is saved alone as
 * a dotted key, the rest of the dict kept (settings-store.ts).
 *
 * A template that would not work is not saved: the host holds only that key
 * back (templateFieldErrors in organization.ts), every other setting saves
 * as usual. Its field says why and that it is not saved, and the save
 * status names it and goes to it.
 */

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  Btn,
  Card,
  More,
  NumberField,
  Row,
  SectionLabel,
  Segmented,
  Select,
  Switch,
  type TemplatePreview,
  TemplateField,
} from '../ui';
import s from '../ui/settings.module.css';
import { LibHero, shell, useLoadedDraft, useLoads, useSettings } from './library-card';
import { musicNamingSentence, podcastImportSentence, podcastNamingSentence } from './library/words';
import {
  PREVIEW_SCENARIOS,
  RESET_TEMPLATES,
  type Scenario,
  SCENARIO_KEY,
  type TemplateKey,
  VALID_VARS,
  playlistItemError,
  previewBody,
  savedScenario,
  templateFieldErrors,
  templateWaits,
  templatesOf,
} from './organization';
import { text } from './source-panels';

type Section = Record<string, unknown>;

// ── the template fields: what is typed shows; a load or Reset shows the saved ──

let resets = 0;
const resetListeners = new Set<() => void>();
function resetTyped() {
  resets += 1;
  for (const listener of resetListeners) listener();
}
function useResets() {
  return useSyncExternalStore(
    (listener) => {
      resetListeners.add(listener);
      return () => {
        resetListeners.delete(listener);
      };
    },
    () => resets,
  );
}

/**
 * A naming template's field: it shows what was typed, an emptied one too; a
 * load or Reset shows the saved one again (an empty saved one shows the
 * default, as loadSettingsData filled it). `onTyped` hears each keystroke.
 */
export function useTemplateField(
  store: SettingsStore,
  key: TemplateKey,
  onTyped?: (value: string) => void,
) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const resetCount = useResets();
  const [typed, setTyped] = useState<string | null>(null);
  useEffect(() => setTyped(null), [loads, resetCount]);
  const value = typed ?? templatesOf(settings)[key];
  return {
    value: loads ? value : '',
    onChange: (next: string) => {
      setTyped(next);
      store.set('file_organization', `templates.${key}`, next);
      onTyped?.(next);
    },
  };
}

/** a template's problems, read off the settings as they are now. A new
 *  value that would not work is not saved (the host holds that key back),
 *  and the field says so */
function useTemplateError(store: SettingsStore, key: TemplateKey): string | null {
  const settings = useSettings(store);
  const errors = templateFieldErrors(settings)[key];
  const problem =
    key === 'playlist_item'
      ? playlistItemError(settings)
      : errors?.length
        ? errors.join('. ')
        : null;
  if (!problem) return null;
  return templateWaits(store, key)
    ? `${problem.replace(/\.$/, '')}. Not saved until it is fixed.`
    : problem;
}

// ── the path preview ──

type PreviewAnswer = {
  paths?: Record<string, unknown>;
  disc_label_used?: boolean;
};
type Preview = { data: PreviewAnswer } | { error: string } | null;

/** the server's answer for one template, as folders and the file */
export function previewOf(
  preview: Preview,
  key: string,
): {
  preview: TemplatePreview | null;
  note?: string;
  path?: string;
} {
  if (!preview) return { preview: null };
  if ('error' in preview) return { preview: null, note: preview.error };
  const result = preview.data.paths?.[key];
  if (!result || typeof result === 'object')
    return {
      preview: null,
      note: (result as { error?: string } | null)?.error || 'Nothing to preview',
    };
  const path = text(result);
  const parts = path.split('/').filter(Boolean);
  return {
    preview: { folders: parts.slice(0, -1), file: parts[parts.length - 1] },
    path,
  };
}

/** what each template makes, rendered by the server's real path builder
 *  against a made-up release; refreshed as the fields change */
function usePathPreview(
  store: SettingsStore,
  on: boolean,
  shown: () => Record<TemplateKey, string>,
) {
  const loads = useLoads(store);
  const [scenario, setScenario] = useState<Scenario>(savedScenario);
  const [preview, setPreview] = useState<Preview>(null);
  const latest = useRef({ scenario, on });
  latest.current = { scenario, on };
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  async function fetchNow() {
    // the field values right now, not the ones this render saw
    const now = { ...latest.current, settings: store.getState().settings };
    if (!now.on) return;
    const mine = ++seq.current;
    try {
      const response = await fetch('/api/settings/path-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(previewBody(now.settings, now.scenario, shown())),
      });
      const data = (await response.json()) as PreviewAnswer & { success?: boolean; error?: string };
      if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
      // a slower answer to an older keystroke never paints over a newer one
      if (mine !== seq.current) return;
      setPreview({ data });
    } catch (error) {
      if (mine !== seq.current) return;
      setPreview({
        error: `Preview unavailable: ${error instanceof Error ? error.message : String(error)}`,
      });
    }
  }

  function refresh(delay = 250) {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void fetchNow(), delay);
  }

  // the loaded values, each time they arrive
  useEffect(() => {
    if (loads) refresh(0);
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => clearTimeout(timer.current), []);

  function pick(key: Scenario) {
    setScenario(key);
    latest.current.scenario = key;
    try {
      localStorage.setItem(SCENARIO_KEY, key);
    } catch {
      // fine
    }
    refresh(0);
  }
  return { scenario, pick, preview, refresh };
}

/** the album template's last answer, for the hero (one page, one preview) */
let albumPreview: string | null = null;
const albumListeners = new Set<() => void>();
function setAlbumPreview(path: string | null) {
  if (path === albumPreview) return;
  albumPreview = path;
  for (const listener of albumListeners) listener();
}
function useAlbumPreview() {
  return useSyncExternalStore(
    (listener) => {
      albumListeners.add(listener);
      return () => {
        albumListeners.delete(listener);
      };
    },
    () => albumPreview,
  );
}

export function MusicNamingHero({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const album = useAlbumPreview();
  const org = (settings.file_organization || {}) as Section;
  const on = org.enabled !== false;
  const templates = templatesOf(settings);
  const errors = templateFieldErrors(settings);
  const count = Object.keys(errors).length;
  return (
    <LibHero
      stats={
        loads
          ? [
              { value: 6, label: 'Templates' },
              count
                ? { value: count, label: 'To fix' }
                : { value: on ? 'On' : 'Off', label: 'Organizing' },
            ]
          : undefined
      }
      parts={
        !loads
          ? [{ text: 'Loading your naming templates.' }]
          : errors.album_path
            ? [
                { text: 'The album path template ' },
                { text: 'would not work', kind: 'warn' },
                { text: ', so it is not saved until it is fixed below.' },
              ]
            : musicNamingSentence({ on, album, template: templates.album_path })
      }
    />
  );
}

/** the variables each template can use, in one place */
function Variables() {
  const group = (title: string, vars: readonly string[], extra?: string) => (
    <div className={s.vars} key={title}>
      <span className={s.varsTitle}>{title}</span>
      <span className={s.varsList}>
        {vars.map((v) => (
          <code key={v}>{v}</code>
        ))}
        {extra ? <span className={s.varsMore}>{extra}</span> : null}
      </span>
    </div>
  );
  return (
    <div className={s.cardPad} id="template-variables">
      {group('Album, compilation', VALID_VARS.album, '$track_disambiguation in the file name')}
      {group('Single', VALID_VARS.single)}
      {group('Playlist file naming', ['$position', '$artist', '$album', '$track', '$title'])}
      {group('Music video', ['$artist', '$artistletter', '$title', '$year'])}
      {group('Sample', VALID_VARS.sample)}
      <p className={s.varsNote}>
        Use <code>{'${var}'}</code> to put text right after a variable:{' '}
        <code>{'${albumtype}'}s</code> gives Albums. Variables are lowercase.
      </p>
    </div>
  );
}

const ALBUM_MORE = (
  <>
    <code>$disc</code> (01), <code>$discnum</code> (1) and <code>$cdnum</code> (CD01) only appear on
    a multi-disc album, so a single-disc album isn&apos;t stamped 01- on every file;{' '}
    <code>$discalways</code> and <code>$discnumalways</code> appear on every album. They work in a
    folder as well as the file name: <code>$albumartist/$album/Disc $discnum/$track - $title</code>{' '}
    gives a disc folder only where there is more than one disc, and replaces the automatic disc
    folder from Multi-disc folder label. <code>$quality</code> is for the file name only.{' '}
    <code>$disambiguation</code> is the release&apos;s edition note (like &ldquo;baby punk
    version&rdquo;) and <code>$track_disambiguation</code> the recording&apos;s version note (like
    &ldquo;acoustic&rdquo;); each is added on its own when the template leaves it out, unless
    Automatically add disambiguation is off. <code>$explicit</code> gives [E] on explicit tracks,{' '}
    <code>$contentrating</code> [E] or [C], <code>$contentratingfull</code> [Explicit] or [Clean];
    empty when the source doesn&apos;t say, and in a folder they follow the album. To tell
    same-named artists apart: <code>$albumartistdisambiguation</code> (MusicBrainz&apos;s note, like
    &ldquo;US grunge band&rdquo;), <code>$albumartistcountry</code>, <code>$albumartistgenre</code>,{' '}
    <code>$albumartistinfo</code> (the first of those three the artist has),{' '}
    <code>$albumartistcomposite</code> (the note, else country and genre together) and{' '}
    <code>$albumartistclash</code> (info, but only when another artist in your library shares the
    name). The <code>$artist</code> versions describe the track artist. They come from enrichment,
    so an artist not matched to MusicBrainz yet renders nothing.
  </>
);

export function MusicNaming({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const org = (settings.file_organization || {}) as Section;
  const library = (settings.library || {}) as Section;
  const on = org.enabled !== false;

  // the fields as they show now, typed or saved: what the preview is asked
  const shown = useRef<Record<TemplateKey, string>>(templatesOf(settings));
  const { scenario, pick, preview, refresh } = usePathPreview(store, on, () => shown.current);
  const typed = (delay?: number) => () => refresh(delay);
  const fields = {
    album_path: useTemplateField(store, 'album_path', typed()),
    compilation_path: useTemplateField(store, 'compilation_path', typed()),
    single_path: useTemplateField(store, 'single_path', typed()),
    playlist_path: useTemplateField(store, 'playlist_path', typed()),
    playlist_item: useTemplateField(store, 'playlist_item', typed()),
    video_path: useTemplateField(store, 'video_path', typed()),
    sample_path: useTemplateField(store, 'sample_path', typed()),
  };
  shown.current = {
    ...templatesOf(settings),
    ...Object.fromEntries(Object.entries(fields).map(([k, f]) => [k, f.value])),
  } as Record<TemplateKey, string>;
  const errors = {
    album_path: useTemplateError(store, 'album_path'),
    single_path: useTemplateError(store, 'single_path'),
    playlist_path: useTemplateError(store, 'playlist_path'),
    playlist_item: useTemplateError(store, 'playlist_item'),
    sample_path: useTemplateError(store, 'sample_path'),
  };

  const album = previewOf(preview, 'album_path');
  useEffect(() => setAlbumPreview(on ? (album.path ?? null) : null), [album.path, on]);

  const setOrg = (key: string, value: unknown) => {
    store.set('file_organization', key, value);
    refresh(0);
  };

  async function resetTemplates() {
    const confirm = shell().showConfirmDialog;
    if (confirm) {
      const ok = await confirm({
        title: 'Put the templates back?',
        message:
          'The album, single, music video, sample, podcast and audiobook templates go back to the defaults, and downloads are organized like before. The compilation template and playlist file naming stay as they are.',
        confirmText: 'Put them back',
        cancelText: 'Keep mine',
      });
      if (!ok) return;
    }
    const values: Section = {};
    for (const [key, value] of Object.entries(RESET_TEMPLATES)) values[`templates.${key}`] = value;
    // the podcast and audiobook templates too, on their own kinds
    store.patch('file_organization', values);
    resetTyped();
    refresh(0);
  }

  const template = (
    key: keyof typeof fields,
    id: string,
    label: string,
    placeholder: string,
    help: ReactNode,
    more?: ReactNode,
  ) => {
    const answer = previewOf(preview, key);
    const error = key in errors ? errors[key as keyof typeof errors] : null;
    return (
      <Row
        stack
        label={label}
        htmlFor={id}
        help={help}
        more={more}
        error={error}
        hidden={key === 'playlist_path' && !error}
      >
        <TemplateField
          id={id}
          value={fields[key].value}
          onChange={fields[key].onChange}
          placeholder={placeholder}
          invalid={!!error}
          preview={on && !error ? answer.preview : null}
          previewNote={on && !error && !answer.preview ? answer.note : undefined}
        />
        {key === 'single_path' &&
        on &&
        preview &&
        'data' in preview &&
        preview.data.paths?.single_as_album ? (
          <div className={s.fieldNote}>
            A release the source calls a single uses the album template until you change this one:{' '}
            <code>{text(preview.data.paths.single_as_album)}</code>
          </div>
        ) : null}
      </Row>
    );
  };

  const disc = text(org.disc_label) || 'Disc';
  const discAnswer = previewOf(preview, 'disc_label');
  const discLine =
    on && preview && 'data' in preview
      ? preview.data.disc_label_used
        ? discAnswer.path
          ? `Disc 2 of a two-disc album is saved in ${discAnswer.path
              .split('/')
              .slice(0, -1)
              .join(' / ')} /`
          : 'Disc 2 of a two-disc album'
        : 'Not used: your album template places the disc itself'
      : 'The folder SoulSync adds for each disc of a multi-disc album.';

  return (
    <>
      <SectionLabel title="File organization" />
      <Card>
        <Row
          label="Enable custom file organization templates"
          htmlFor="file-organization-enabled"
          help="Off keeps SoulSync's built-in layout."
        >
          <Switch
            id="file-organization-enabled"
            checked={on}
            onChange={(event) => setOrg('enabled', event.target.checked)}
          />
        </Row>
        <Row
          id="path-preview-scenarios-row"
          hidden={!on}
          label="Your templates, filled in with an example release"
          help="What each template below saves, for that release."
        >
          <Segmented
            id="path-preview-scenarios"
            label="Example release your templates are filled in with"
            itemAttr="data-scenario"
            value={scenario}
            options={PREVIEW_SCENARIOS.map(([value, label]) => ({ value, label }))}
            onChange={(value) => pick(value as Scenario)}
          />
        </Row>
        {template(
          'album_path',
          'template-album-path',
          'Album path template',
          '$albumartist/$albumartist - $album/$track - $title',
          'Folders split on /, the last part is the file name. Variables are under Variables at the foot of this card.',
          ALBUM_MORE,
        )}
        <Row
          sub
          label="Detect multi-artist compilations"
          htmlFor="detect-multi-artist-compilations"
          help="A soundtrack or compilation is filed as a compilation, not under whichever contributor happened to be downloading."
          more={
            <>
              Without this, one guitarist featured on 45 soundtracks collects all 45 albums.
              Compilations are filed with the Compilation path template below. Each track still
              credits the artist who recorded it. A release counts as a compilation when it is
              credited to Various Artists, when the metadata source types it one, or (when
              reorganizing, where the whole tracklist is known) when three or more tracks have
              different artists and none of them owns more than half the album. A soundtrack
              credited to one composer stays with that composer. It also decides whether reorganize
              gives compilations their own folder layout.
            </>
          }
        >
          <Switch
            id="detect-multi-artist-compilations"
            checked={org.detect_multi_artist_compilations !== false}
            onChange={(event) =>
              store.set(
                'file_organization',
                'detect_multi_artist_compilations',
                event.target.checked,
              )
            }
          />
        </Row>
        {template(
          'compilation_path',
          'template-compilation-path',
          'Compilation path template',
          'Compilations/$album/$track - $artist - $title',
          'Used instead of the album template for soundtracks and compilations.',
          <>
            Same variables as the album template; <code>$albumartist</code> is the release&apos;s
            credit (often Various Artists) and <code>$artist</code> is whoever recorded the track.
            To file compilations exactly like albums, give it the same template as the album one.
          </>,
        )}
        {template(
          'single_path',
          'template-single-path',
          'Single path template',
          '$albumartist/$albumartist - $title/$title',
          'A single may be a file on its own, with no folder.',
        )}
        {/* no longer drives anything (playlists import into Artist/Album and
            the Playlists folder mirrors the library), kept so the stored value
            round-trips. It shows only when it is what holds the save back */}
        {template(
          'playlist_path',
          'template-playlist-path',
          'Playlist path template (no longer used)',
          '$playlist/$artist - $title',
          'An old template that nothing uses now. It is shown because it would not work: put it right or empty it.',
        )}
        {template(
          'playlist_item',
          'template-playlist-item',
          'Playlist file naming',
          'Empty keeps the library file name',
          'Renames the files inside each Organize by playlist folder. A file name only, no folders.',
          <>
            Your real library files are never touched. Variables: <code>$position</code> (playlist
            order), <code>$artist</code>, <code>$album</code>, <code>$track</code>,{' '}
            <code>$title</code>. It must include <code>$title</code> and cannot contain /. Example:{' '}
            <code>$position - $artist - $title</code> gives{' '}
            <code>01 - Daft Punk - One More Time.flac</code>. Empty keeps the library file name.
          </>,
        )}
        <More
          label="More templates"
          names="Music video path template, Sample path template"
          count={2}
        >
          {template(
            'video_path',
            'template-video-path',
            'Music video path template',
            '$artist/$title-video',
            'Where a music video is saved, inside the music videos folder.',
          )}
          {template(
            'sample_path',
            'template-sample-path',
            'Sample path template',
            '$artist/$track - $chop',
            'How Sample Studio files a chop inside the chosen sample folder.',
            <>
              <code>$chop</code> is the save name, <code>$stem</code> is drums, vocals, bass or
              other (empty for the full mix). Every rendered file also carries its own tags. Empty
              parts collapse, so a chop with no album leaves no stray folder.
            </>,
          )}
        </More>
        <More label="Variables" names="what each template can use">
          <Variables />
        </More>
      </Card>

      <SectionLabel
        title="Folder details"
        action={
          <Btn
            kind="ghost"
            small
            icon="undo"
            id="file-org-reset"
            onClick={() => void resetTemplates()}
          >
            Reset to defaults
          </Btn>
        }
      />
      <Card>
        <Row
          label="Multi-disc folder label"
          htmlFor="disc-label"
          help={<span data-disc-preview="">{discLine}</span>}
          more={
            <>
              Ignored when your template places the disc itself with <code>$disc</code>,{' '}
              <code>$discnum</code> or <code>$cdnum</code>: the template wins.
            </>
          }
        >
          <Segmented
            id="disc-label"
            label="Multi-disc folder label"
            value={disc}
            options={[
              { value: 'Disc', label: 'Disc' },
              { value: 'CD', label: 'CD' },
            ]}
            onChange={(value) => setOrg('disc_label', value)}
          />
        </Row>
        <Row
          label="Collaborative album artist"
          htmlFor="collab-artist-mode"
          help="Which artist a shared album is filed under."
          more={
            <>
              How <code>$albumartist</code> resolves when a release has several artists, for albums
              and singles. First listed uses only the primary artist for folder names (Larry June,
              not Larry June, Curren$y &amp; The Alchemist). The full artist list is always kept in
              the tags.
            </>
          }
        >
          <Segmented
            id="collab-artist-mode"
            label="Collaborative album artist"
            value={text(org.collab_artist_mode) || 'first'}
            options={[
              { value: 'first', label: 'First listed artist' },
              { value: 'all', label: 'All artists combined' },
            ]}
            onChange={(value) => setOrg('collab_artist_mode', value)}
          />
        </Row>
        <Row
          label="Automatically add disambiguation to folder and file names"
          htmlFor="auto-disambiguation"
          help="Adds a release's edition note when your template doesn't place it."
          more={
            <>
              A release&apos;s MusicBrainz edition note goes on the album folder when the template
              leaves out <code>$disambiguation</code>, and a recording&apos;s version note (like
              &ldquo;acoustic&rdquo;) on the file name when it leaves out{' '}
              <code>$track_disambiguation</code>. This keeps same-named releases and same-titled
              recordings apart. Off, your template is used exactly as written.
            </>
          }
        >
          <Switch
            id="auto-disambiguation"
            checked={org.auto_disambiguation !== false}
            onChange={(event) => setOrg('auto_disambiguation', event.target.checked)}
          />
        </Row>
        <Row
          label='Group numbers and symbols under "#"'
          htmlFor="artistletter-symbol-fallback"
          help="With $artistletter, artists starting with a digit, symbol or non-Latin script share one # folder."
          more={
            <>
              &ldquo;365 Days&rdquo; and &ldquo;北京&rdquo; both go to <code>#/</code>. Accented
              letters fold to their base letter (Édith Piaf goes to <code>E/</code>). Off, the
              literal first character is used, as before.
            </>
          }
        >
          <Switch
            id="artistletter-symbol-fallback"
            checked={org.artistletter_symbol_fallback === true}
            onChange={(event) => setOrg('artistletter_symbol_fallback', event.target.checked)}
          />
        </Row>
        <Row
          label="Keep symbols in names with lookalike characters"
          htmlFor="unicode-substitution"
          help={
            'Windows does not allow : ? / \\ " * | < > in names. On, they become lookalikes that work everywhere.'
          }
          more={
            <>
              Off, they become <code>_</code> (<code>AC_DC</code>). On, <code>AC∕DC</code> and{' '}
              <code>What&apos;s Going On？</code>, and a trailing dot is kept (<code>DAMN․</code>).
              New downloads only; run Reorganize to rename what&apos;s already there.
            </>
          }
        >
          <Switch
            id="unicode-substitution"
            checked={org.unicode_substitution === true}
            onChange={(event) => setOrg('unicode_substitution', event.target.checked)}
          />
        </Row>
      </Card>

      <SectionLabel
        title="Library Organize"
        hint="How the Library Organize tool treats what you have"
      />
      <Card>
        <Row
          label="Preserve my casing, year and disc layout"
          htmlFor="reorganize-preserve-casing"
          help="Reorganize keeps the folder names you already have."
          more={
            <>
              When the tool re-matches a track, the metadata source often differs from files you
              already organized: letter case, the release year (a reissue [2023]
              &ldquo;corrected&rdquo; to 2020), or the disc layout (a single-disc album matched to a
              deluxe 2-disc edition, stamping a bogus &ldquo;0211&rdquo; prefix). On, a title or
              album differing <em>only</em> by letter case, your own album year, and a single-disc
              album&apos;s numbering are left alone. Off matches the metadata source exactly.
            </>
          }
        >
          <Switch
            id="reorganize-preserve-casing"
            checked={library.reorganize_preserve_casing !== false}
            onChange={(event) =>
              store.set('library', 'reorganize_preserve_casing', event.target.checked)
            }
          />
        </Row>
        <Row
          label="Write artist.nfo and artwork (Plex / Jellyfin / Kodi / Emby)"
          htmlFor="write-artist-nfo"
          help="A new artist folder gets an artist.nfo with the MusicBrainz ID, and artwork when it has none."
          more={
            <>
              The <em>artist.nfo</em> carries the artist&apos;s name and MusicBrainz artist ID, so
              Jellyfin, Kodi and Emby identify the artist instead of guessing by name. A folder with
              no artist artwork also gets <em>poster.jpg</em> and <em>background.jpg</em>{' '}
              (fanart.tv, TheAudioDB or Deezer), which Plex reads as local art. Nothing in the
              folder is replaced. To fill folders you already have, run the{' '}
              <em>Artist NFO Backfill</em> repair job.
            </>
          }
        >
          <Switch
            id="write-artist-nfo"
            checked={library.write_artist_nfo === true}
            onChange={(event) => store.set('library', 'write_artist_nfo', event.target.checked)}
          />
        </Row>
      </Card>
    </>
  );
}

// ── Tags and artwork > Music: the three tag options ──

export function MusicTagOptions({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const tags = ((settings.metadata_enhancement || {}) as Section).tags as Section | undefined;
  return (
    <>
      <Row
        label="Artist tag separator"
        htmlFor="artist-separator"
        help="Between several artists in the ARTIST tag. & matches MusicBrainz and Picard."
      >
        <Segmented
          id="artist-separator"
          label="Artist tag separator"
          value={text(tags?.artist_separator) || ', '}
          options={[
            { value: ', ', label: ', comma' },
            { value: '; ', label: '; semicolon' },
            { value: ' / ', label: '/ slash' },
            { value: ' & ', label: '& ampersand' },
          ]}
          onChange={(value) => store.set('metadata_enhancement', 'tags.artist_separator', value)}
        />
      </Row>
      <Row
        label="Write multi-value ARTISTS and GENRE tags"
        htmlFor="write-multi-artist"
        help="Each artist and each genre as its own tag value (one GENRE= per genre), for Navidrome, Jellyfin and Plex. Off writes one value joined with commas."
      >
        <Switch
          id="write-multi-artist"
          checked={!!tags?.write_multi_artist}
          onChange={(event) =>
            store.set('metadata_enhancement', 'tags.write_multi_artist', event.target.checked)
          }
        />
      </Row>
      <Row
        label="Move featured artists to title"
        htmlFor="feat-in-title"
        // a node, not a string: the "." in "feat." is not the end of a sentence
        help={
          <>
            Only the primary artist in the ARTIST tag; the others go in the title as (feat. Name).
          </>
        }
      >
        <Switch
          id="feat-in-title"
          checked={!!tags?.feat_in_title}
          onChange={(event) =>
            store.set('metadata_enhancement', 'tags.feat_in_title', event.target.checked)
          }
        />
      </Row>
    </>
  );
}

// ── Downloads > Behaviour > Music: the wishlist rules ──

export function WishlistRules({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const wishlist = (settings.wishlist || {}) as Section;
  const ttl = useLoadedDraft(store, text(wishlist.ignore_ttl_days) || '30', (raw) => {
    const n = parseInt(raw, 10);
    store.set(
      'wishlist',
      'ignore_ttl_days',
      Math.max(1, Math.min(365, Number.isFinite(n) ? n : 30)),
    );
  });
  return (
    <Card title="Wishlist" hint="What the wishlist adds, searches and remembers">
      <Row
        label="Allow duplicate tracks across albums"
        htmlFor="allow-duplicate-tracks"
        help="The same song can be wished for from different albums (completing a discography where albums share tracks). Off skips a track with the same name and artist."
      >
        <Switch
          id="allow-duplicate-tracks"
          checked={wishlist.allow_duplicate_tracks !== false}
          onChange={(event) =>
            store.set('wishlist', 'allow_duplicate_tracks', event.target.checked)
          }
        />
      </Row>
      <Row
        label="Search Wing It guesses as wishlist tracks"
        htmlFor="wishlist-wing-it-guesses"
        help="Off by default: guesses stay in the Wing It Pool for you to review."
        more="Wing It means a track couldn't be matched in the metadata catalogue: these are unmatched guesses, not tracks missing from your library. On, guesses are searched like any other wishlist track."
      >
        <Switch
          id="wishlist-wing-it-guesses"
          checked={wishlist.wing_it_guesses === true}
          onChange={(event) => store.set('wishlist', 'wing_it_guesses', event.target.checked)}
        />
      </Row>
      <Row
        field
        label="Removed-track re-try window"
        htmlFor="wishlist-ignore-ttl"
        help="A track you remove or cancel isn't added back on its own for this long. Lower it (to 7, say) to give removed tracks another chance sooner."
      >
        <NumberField
          id="wishlist-ignore-ttl"
          min="1"
          max="365"
          step="1"
          unit="days"
          value={ttl.value}
          onChange={(event) => ttl.onChange(event.target.value)}
        />
      </Row>
    </Card>
  );
}

// ── Import and files > Music: re-download what was deleted outside ──

export function RedownloadDeleted({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const library = (settings.library || {}) as Section;
  return (
    <Row
      label="Re-download files deleted outside SoulSync"
      htmlFor="redownload-externally-deleted"
      help="A watched track whose file was deleted outside SoulSync goes back to the Wishlist (on by default, like Lidarr)."
      more={
        <>
          Found by a library scan. Off treats such a deletion like{' '}
          <em>Don&apos;t download again</em>: the track is no longer watched instead.
        </>
      }
    >
      <Switch
        id="redownload-externally-deleted"
        checked={library.redownload_externally_deleted !== false}
        onChange={(event) =>
          store.set('library', 'redownload_externally_deleted', event.target.checked)
        }
      />
    </Row>
  );
}

// ── Podcasts: the template, and what file is kept ──

export function PodcastNamingHero({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  return (
    <LibHero
      parts={
        loads
          ? podcastNamingSentence(templatesOf(settings).podcast_path)
          : [{ text: 'Loading your naming templates.' }]
      }
    />
  );
}

export function PodcastNaming({ store }: { store: SettingsStore }) {
  const field = useTemplateField(store, 'podcast_path');
  const error = useTemplateError(store, 'podcast_path');
  return (
    <Card>
      <Row
        stack
        label="Podcast path template"
        htmlFor="template-podcast-path"
        error={error}
        help="Season and episode folders collapse cleanly when the feed leaves them out."
        more={
          <>
            Variables: <code>$show</code>, <code>$author</code>, <code>$title</code>,{' '}
            <code>$season</code> (01), <code>$seasonnum</code> (1), <code>$episode</code> (01),{' '}
            <code>$episodenum</code> (1), <code>$year</code>, <code>$date</code> (YYYY-MM-DD),{' '}
            <code>$type</code>. Default: <code>$show/Season $season/$title</code>. Use{' '}
            <code>{'${var}'}</code> to put text right after a variable.
          </>
        }
      >
        <TemplateField
          id="template-podcast-path"
          placeholder="$show/Season $season/$title"
          value={field.value}
          onChange={field.onChange}
          invalid={!!error}
        />
      </Row>
    </Card>
  );
}

export function PodcastImportHero({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  return (
    <LibHero
      parts={
        loads
          ? podcastImportSentence(text((settings.podcasts as Section | undefined)?.media_format))
          : [{ text: 'Loading.' }]
      }
    />
  );
}

export function PodcastImport({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const podcasts = (settings.podcasts || {}) as Section;
  return (
    <Card>
      <Row
        field
        label="Podcast media format"
        htmlFor="podcast-media-format"
        help="Which formats your podcast folders keep."
        more={
          <>
            <strong>Audio only</strong> keeps standard audio files with tags.{' '}
            <strong>Video only</strong> puts the audio in an MP4 with the episode artwork as a still
            frame and TV tags, so Plex, Jellyfin and Emby list them as TV shows with seasons,
            episode posters and resume. <strong>Audio and video</strong> keeps both side by side in
            the same folder with shared NFO sidecars, so a music library and a TV library can both
            index it.
          </>
        }
      >
        <Select
          id="podcast-media-format"
          value={text(podcasts.media_format) || 'audio'}
          onChange={(event) => store.set('podcasts', 'media_format', event.target.value)}
          options={[
            { value: 'audio', label: 'Audio only (MP3 / M4A), the default' },
            { value: 'video', label: 'Video only (MP4 with artwork, for TV media servers)' },
            { value: 'both', label: 'Audio and video, side by side' },
          ]}
        />
      </Row>
    </Card>
  );
}
