/**
 * Library > Organization (SETTINGS_PORT.md, step 8b): the media-tabbed card,
 * its Music and Podcasts panels, the naming templates' live preview (#1868)
 * and Reset to Defaults. Same markup, classes and ids.
 *
 * The Audiobooks panel is library-audiobooks.tsx (step 8c) and the Video
 * panel library-video-organization.tsx (video pass V2b). The audiobook
 * template is in the Audiobooks panel: Reset writes it there, as before.
 *
 * Templates are one dict on the server (file_organization.templates), and so
 * are the tag options (metadata_enhancement.tags): each key is saved alone as
 * a dotted key, the rest of the dict kept (settings-store.ts).
 *
 * A template that would not work holds every save back, as the old page
 * refused its whole save: the rule is registered on the store by the host
 * (templateErrors in organization.ts), and the save button says why.
 */

import { Fragment, type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { AudiobookOrganizationPanel } from './library-audiobooks';
import { HintGroup, LIBRARY_TAB, LibrarySection, useMediaTabs } from './library-card';
import { VideoOrganizationPanel } from './library-video-organization';
import {
  musicOrgSummary,
  PREVIEW_FIELDS,
  PREVIEW_SCENARIOS,
  previewBody,
  RESET_TEMPLATES,
  savedScenario,
  SCENARIO_KEY,
  type Scenario,
  type TemplateKey,
  templatesOf,
} from './organization';
import { useDraft } from './parts';
import { text } from './source-panels';

const TAB = LIBRARY_TAB;
type Section = Record<string, unknown>;

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}

// ── the path preview ──

type PreviewAnswer = {
  paths?: Record<string, unknown>;
  disc_label_used?: boolean;
};
type Preview = { data: PreviewAnswer } | { error: string } | null;

/** one line of a slot (_renderPathPreview) */
function PreviewRow({ result, note }: { result: unknown; note?: string }) {
  if (!result || typeof result === 'object') {
    return (
      <div className="path-preview-row path-preview-error">
        {(result as { error?: string } | null)?.error || 'Nothing to preview'}
      </div>
    );
  }
  const path = text(result);
  const parts = path.split('/');
  return (
    <div className="path-preview-row">
      <code className="path-preview-path" title={path}>
        {parts.map((part, index) => (
          <Fragment key={index}>
            {index ? <span className="path-preview-sep">/</span> : null}
            <span
              className={index === parts.length - 1 ? 'path-preview-file' : 'path-preview-folder'}
            >
              {part}
            </span>
          </Fragment>
        ))}
      </code>
      {note ? <span className="path-preview-note">{note}</span> : null}
    </div>
  );
}

function PreviewSlot({ field, preview, on }: { field: string; preview: Preview; on: boolean }) {
  const key = PREVIEW_FIELDS.find(([id]) => id === field)?.[1] ?? '';
  let rows: ReactNode = null;
  if (preview && 'error' in preview) {
    rows = <PreviewRow result={{ error: preview.error }} />;
  } else if (preview) {
    const { paths, disc_label_used } = preview.data;
    const note =
      key === 'disc_label'
        ? disc_label_used
          ? 'Disc 2 of a two-disc album'
          : 'Not used: your album template places the disc itself'
        : '';
    rows = (
      <>
        <PreviewRow result={paths?.[key]} note={note} />
        {key === 'single_path' && paths?.single_as_album ? (
          <PreviewRow
            result={paths.single_as_album}
            note="A release the source calls a single uses the album template until you change this one"
          />
        ) : null}
      </>
    );
  }
  return (
    <div className="path-preview" aria-live="polite" hidden={!on}>
      {rows}
    </div>
  );
}

/** what each template makes, rendered by the server's real path builder
 *  against a made-up release; refreshed as the fields change */
function usePathPreview(
  store: SettingsStore,
  on: boolean,
  shown: () => Record<TemplateKey, string>,
) {
  const settings = useSettings(store);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const [scenario, setScenario] = useState<Scenario>(savedScenario);
  const [preview, setPreview] = useState<Preview>(null);
  const latest = useRef({ settings, scenario, on });
  latest.current = { settings: store.getState().settings, scenario, on };
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

  const bar = (
    <div
      id="path-preview-scenarios"
      className="path-preview-scenarios"
      role="radiogroup"
      aria-label="Example release your templates are filled in with"
      hidden={!on}
    >
      <span className="path-preview-scenarios-label">
        Your templates, filled in with an example release:
      </span>
      {PREVIEW_SCENARIOS.map(([key, label]) => (
        <button
          key={key}
          type="button"
          data-scenario={key}
          role="radio"
          aria-checked={scenario === key ? 'true' : 'false'}
          onClick={() => pick(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
  const slot = (field: string) => <PreviewSlot field={field} preview={preview} on={on} />;
  return { bar, slot, refresh };
}

// ── the card ──

export function OrganizationCard({ store }: SettingsTabProps) {
  const settings = useSettings(store);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const org = (settings.file_organization || {}) as Section;
  const tags = ((settings.metadata_enhancement || {}) as Section).tags as Section | undefined;
  const wishlist = (settings.wishlist || {}) as Section;
  const library = (settings.library || {}) as Section;
  const podcasts = (settings.podcasts || {}) as Section;
  // a template field shows what was typed, an emptied one included; a load
  // or Reset shows the saved one again (empty saved shows the default, as
  // loadSettingsData filled it)
  const [typed, setTyped] = useState<Partial<Record<TemplateKey, string>>>({});
  useEffect(() => setTyped({}), [loads]);
  const templates = { ...templatesOf(settings), ...typed };
  const shown = useRef(templates);
  shown.current = templates;

  const on = org.enabled !== false;
  const preview = usePathPreview(store, on, () => shown.current);

  const setOrg = (key: string, value: unknown, delay?: number) => {
    store.set('file_organization', key, value);
    preview.refresh(delay);
  };
  const template = (key: TemplateKey) => ({
    value: loads ? templates[key] : '',
    onChange: (event: { target: { value: string } }) => {
      setTyped({ ...typed, [key]: event.target.value });
      setOrg(`templates.${key}`, event.target.value);
    },
  });

  const ttlDraft = useDraft(text(wishlist.ignore_ttl_days) || '30', (raw) => {
    const n = parseInt(raw, 10);
    store.set(
      'wishlist',
      'ignore_ttl_days',
      Math.max(1, Math.min(365, Number.isFinite(n) ? n : 30)),
    );
  });
  useEffect(() => ttlDraft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  function resetTemplates() {
    const values: Section = {};
    for (const [key, value] of Object.entries(RESET_TEMPLATES)) values[`templates.${key}`] = value;
    // the audiobook template too, in the Audiobooks panel
    setTyped({});
    store.patch('file_organization', values);
    preview.refresh(0);
  }

  const { media, tabs } = useMediaTabs('organization', 'Organization media type');
  const musicPanel = useRef<HTMLDivElement>(null);

  const summary = loads ? musicOrgSummary(on, templates.album_path) : '';
  const fallback = 'File naming templates & reorganize';

  const check = (
    id: string,
    checked: boolean,
    save: (value: boolean) => void,
    label: ReactNode,
    className?: string,
  ) => (
    <label className={className}>
      <input
        type="checkbox"
        id={id}
        checked={checked}
        onChange={(event) => save(event.target.checked)}
      />
      {/* the space the markup had between the box and its words */} {label}
    </label>
  );

  return (
    <LibrarySection title="Organization" hint="Naming, layout and file handling">
      {tabs}
      <div
        ref={musicPanel}
        id="organization-music-panel"
        role="tabpanel"
        aria-labelledby="organization-music-tab"
        className="stg-media-panel"
        tabIndex={0}
        hidden={media !== 'music'}
      >
        <div className="stg-media-context">
          <strong>Music organization</strong>
          <span
            className={summary ? 'stg-media-summary is-live' : 'stg-media-summary'}
            data-stg-summary="library-music-org"
            data-stg-fallback={fallback}
          >
            {summary || fallback}
          </span>
        </div>

        <div className="settings-group" data-stg={TAB}>
          <h3>File Organization</h3>

          <div className="form-group">
            {check(
              'file-organization-enabled',
              on,
              (v) => setOrg('enabled', v, 0),
              'Enable custom file organization templates',
              'checkbox-label',
            )}
          </div>

          <HintGroup
            help={
              <>
                When a release carries a MusicBrainz disambiguation (edition note) and your template
                doesn't place <code>$disambiguation</code> itself, it is added to the album folder
                automatically. When a recording carries its own disambiguation (version note, like
                “acoustic”) and your template doesn't place <code>$track_disambiguation</code>{' '}
                itself, it is added to the track filename automatically. This keeps same-named
                releases and same-titled recordings apart — turn it off and your template is
                respected exactly as written.
              </>
            }
          >
            {check(
              'auto-disambiguation',
              org.auto_disambiguation !== false,
              (v) => setOrg('auto_disambiguation', v, 0),
              'Automatically add disambiguation to folder and filenames',
              'checkbox-label',
            )}
          </HintGroup>

          {preview.bar}
          <HintGroup
            preview={preview.slot('template-album-path')}
            help={
              <>
                Variables: <code>$albumartist</code>, <code>$artist</code>,{' '}
                <code>$artistletter</code>, <code>$albumartistletter</code>, <code>$album</code>,{' '}
                <code>$albumtype</code>, <code>$title</code>, <code>$track</code>,{' '}
                <code>$disc</code> (01), <code>$discnum</code> (1), <code>$cdnum</code> (CD01):
                these three only appear on a multi-disc album, so a single-disc album isn't stamped
                01- on every file. <code>$discalways</code> (01) and <code>$discnumalways</code> (1)
                appear on every album, <code>$year</code>, <code>$quality</code> (filename only),{' '}
                <code>$disambiguation</code> (the release's edition note, like “baby punk version”;
                added to the album folder automatically when your template leaves it out, unless
                “Automatically add disambiguation” above is turned off),{' '}
                <code>$track_disambiguation</code> (the recording's version note, like “acoustic”;
                added to the filename automatically when your template leaves it out, unless the
                same toggle above is turned off), <code>$explicit</code> ([E] on explicit tracks,
                nothing otherwise), <code>$contentrating</code> ([E] or [C]) and{' '}
                <code>$contentratingfull</code> ([Explicit] or [Clean]); these stay empty when the
                source doesn't say, and in a folder they follow the album, not the track. To tell
                same-named artists apart: <code>$albumartistdisambiguation</code> (MusicBrainz's
                note, like “US grunge band”), <code>$albumartistcountry</code> (US, GB, JM),{' '}
                <code>$albumartistgenre</code>, <code>$albumartistinfo</code> (the first of those
                three the artist has), <code>$albumartistcomposite</code> (the note, else country
                and genre together, like “US Rock”) and <code>$albumartistclash</code> (the same as
                info, but only when another artist in your library shares the name, so{' '}
                <code>$albumartist ($albumartistclash)</code> keeps unique artists plain). The{' '}
                <code>$artist</code> versions (<code>$artistinfo</code> and so on) describe the
                track artist. They come from enrichment, so an artist that hasn't been matched to
                MusicBrainz yet renders nothing. The three disc variables work in a folder segment
                as well as in the filename and are empty on a single-disc album, so{' '}
                <code>$albumartist/$album/Disc $discnum/$track - $title</code> gives you a disc
                folder only where there is more than one disc. Put any of them in the template and
                it replaces the automatic disc folder from “Multi-Disc Folder Label” below. Use{' '}
                <code>{'${var}'}</code> to append text: <code>{'${albumtype}'}s</code> → Albums
              </>
            }
          >
            <label>Album Path Template:</label>
            <input
              type="text"
              id="template-album-path"
              placeholder="$albumartist/$albumartist - $album/$track - $title"
              {...template('album_path')}
            />
          </HintGroup>

          <HintGroup
            help={
              <>
                A soundtrack or compilation is filed as a compilation instead of whichever
                contributor happened to be downloading — without this, one guitarist featured on 45
                soundtracks collects all 45 albums. Compilations are filed with the Compilation Path
                Template below, not the album one. Each track still credits the artist who actually
                recorded it. A release counts as a compilation when it is credited to Various
                Artists, when the metadata source types it one, or — when reorganizing, where the
                whole tracklist is known — when three or more tracks have different artists and none
                of them owns more than half the album. A soundtrack credited to a single composer is
                left with that composer. Also governs whether reorganize gives compilations their
                own folder layout.
              </>
            }
          >
            {check(
              'detect-multi-artist-compilations',
              org.detect_multi_artist_compilations !== false,
              (v) => store.set('file_organization', 'detect_multi_artist_compilations', v),
              'Detect multi-artist compilations',
            )}
          </HintGroup>
          <HintGroup
            preview={preview.slot('template-compilation-path')}
            help={
              <>
                Used instead of the album template for soundtracks and compilations (see “Detect
                multi-artist compilations” above). Same variables as the album template;{' '}
                <code>$albumartist</code> is the release's credit (often Various Artists) and{' '}
                <code>$artist</code> is whoever recorded the track. To file compilations exactly
                like albums, give it the same template as the album one.
              </>
            }
          >
            <label>Compilation Path Template:</label>
            <input
              type="text"
              id="template-compilation-path"
              placeholder="Compilations/$album/$track - $artist - $title"
              {...template('compilation_path')}
            />
          </HintGroup>

          <HintGroup
            preview={preview.slot('template-single-path')}
            help={
              <>
                Variables: <code>$albumartist</code>, <code>$artist</code>,{' '}
                <code>$artistletter</code>, <code>$albumartistletter</code>, <code>$title</code>,{' '}
                <code>$track</code> (01), <code>$album</code>, <code>$albumtype</code>,{' '}
                <code>$year</code>, <code>$quality</code> (filename only). Use{' '}
                <code>{'${var}'}</code> to append text: <code>{'${albumtype}'}s</code> → Singles
              </>
            }
          >
            <label>Single Path Template:</label>
            <input
              type="text"
              id="template-single-path"
              placeholder="$albumartist/$albumartist - $title/$title"
              {...template('single_path')}
            />
          </HintGroup>

          {/* Playlist Path Template: hidden from the UI. Playlists now import
              normally into Artist/Album and the Playlists/ folder is a symlink
              view that mirrors the real library filename, so this template no
              longer drives playlist-file naming. Kept so the stored value still
              round-trips. hidden, not display:none: the media card's
              display:block !important beat the inline style. */}
          <div className="form-group" hidden>
            <label>Playlist Path Template:</label>
            <input
              type="text"
              id="template-playlist-path"
              placeholder="$playlist/$artist - $title"
              {...template('playlist_path')}
            />
            <small className="settings-hint">
              Variables: $playlist, $albumartist, $artist, $artistletter, $title, $year, $quality
              (filename only). Use {'${var}'} to append text
            </small>
          </div>

          <HintGroup
            preview={preview.slot('template-playlist-item')}
            help={
              <>
                Renames the files inside each "Organize by Playlist" folder (your real library files
                are never touched). <strong>Filename only — no folders.</strong> Variables:{' '}
                <code>$position</code> (playlist order), <code>$artist</code>, <code>$album</code>,{' '}
                <code>$track</code>, <code>$title</code>. Must include <code>$title</code> and
                cannot contain "/". Example: <code>$position - $artist - $title</code> →{' '}
                <code>01 - Daft Punk - One More Time.flac</code>. Empty = keep the original library
                filename.
              </>
            }
          >
            <label>Playlist File Naming:</label>
            <input
              type="text"
              id="template-playlist-item"
              placeholder="Leave empty to keep the library filename"
              {...template('playlist_item')}
            />
          </HintGroup>

          <HintGroup
            preview={preview.slot('template-video-path')}
            help={
              <>
                Variables: <code>$artist</code>, <code>$artistletter</code>, <code>$title</code>,{' '}
                <code>$year</code>. Use <code>{'${var}'}</code> to append text. Default:{' '}
                <code>$artist/$title-video</code>
              </>
            }
          >
            <label>Music Video Path Template:</label>
            <input
              type="text"
              id="template-video-path"
              placeholder="$artist/$title-video"
              {...template('video_path')}
            />
          </HintGroup>

          <HintGroup
            preview={preview.slot('template-sample-path')}
            help={
              <>
                Variables: <code>$artist</code>, <code>$track</code>, <code>$album</code>,{' '}
                <code>$chop</code> (the save name), <code>$stem</code> (drums/vocals/bass/other,
                empty for the full mix). Default: <code>$artist/$track - $chop</code>. Sample Studio
                files saved chops inside the chosen sample folder with this layout; every rendered
                file also carries its own tags. Empty segments collapse — a chop with no album info
                won't leave a stray folder. Use <code>{'${var}'}</code> to append text.
              </>
            }
          >
            <label>Sample Path Template:</label>
            <input
              type="text"
              id="template-sample-path"
              placeholder="$artist/$track - $chop"
              {...template('sample_path')}
            />
          </HintGroup>

          <HintGroup
            preview={preview.slot('disc-label')}
            help={
              <>
                Label for the disc subfolder SoulSync adds automatically on a multi-disc album (
                <code>Disc 1/</code>). Ignored when your path template already places the disc
                itself with <code>$disc</code>, <code>$discnum</code> or <code>$cdnum</code> — the
                template wins.
              </>
            }
          >
            <label>Multi-Disc Folder Label:</label>
            <select
              id="disc-label"
              value={text(org.disc_label) || 'Disc'}
              onChange={(event) => setOrg('disc_label', event.target.value, 0)}
            >
              <option value="Disc">Disc (e.g., Disc 1/)</option>
              <option value="CD">CD (e.g., CD 1/)</option>
            </select>
          </HintGroup>

          <HintGroup
            help={
              <>
                How <code>$albumartist</code> resolves when a release has multiple artists — this
                governs the FOLDER your music sorts into for both albums and singles (the default
                templates use <code>$albumartist</code>). "First Listed" uses only the primary
                artist for folder names (e.g., "Larry June" instead of "Larry June, Curren$y &amp;
                The Alchemist"). Full artist list is always preserved in file metadata tags.
              </>
            }
          >
            <label>Collaborative Album Artist:</label>
            <select
              id="collab-artist-mode"
              className="form-select"
              value={text(org.collab_artist_mode) || 'first'}
              onChange={(event) => setOrg('collab_artist_mode', event.target.value, 0)}
            >
              <option value="first">First Listed Artist (recommended)</option>
              <option value="all">All Artists Combined</option>
            </select>
          </HintGroup>

          <HintGroup
            help={
              <>
                When <code>$artistletter</code> is used in templates: artists starting with digits,
                symbols or non-Latin scripts all sort into a single <code>#</code> folder instead of
                one folder per character ("365 Days" → <code>#/</code>, "北京" → <code>#/</code>).
                Accented letters fold to their base letter (Édith Piaf → <code>E/</code>). Off = the
                literal first character, exactly as before.
              </>
            }
          >
            {check(
              'artistletter-symbol-fallback',
              org.artistletter_symbol_fallback === true,
              (v) => setOrg('artistletter_symbol_fallback', v, 0),
              'Group numbers & symbols under "#"',
            )}
          </HintGroup>

          <HintGroup
            help={
              <>
                Windows doesn't allow <code>: ? / \ " * | &lt; &gt;</code> in file or folder names.
                Off, they become <code>_</code> (<code>AC_DC</code>). On, they become lookalike
                characters that work everywhere (<code>AC∕DC</code>, <code>What's Going On？</code>
                ), and a trailing dot is kept (<code>DAMN․</code>). Applies to new downloads; run
                Reorganize to rename what's already there.
              </>
            }
          >
            {check(
              'unicode-substitution',
              org.unicode_substitution === true,
              (v) => setOrg('unicode_substitution', v, 0),
              'Keep symbols in names with lookalike characters',
            )}
          </HintGroup>

          <HintGroup
            help={
              <>
                Separator between multiple artists in the ARTIST tag (e.g. <code>&amp;</code>{' '}
                matches MusicBrainz/Picard style)
              </>
            }
          >
            <label>Artist Tag Separator:</label>
            <select
              id="artist-separator"
              className="form-select"
              value={text(tags?.artist_separator) || ', '}
              onChange={(event) =>
                store.set('metadata_enhancement', 'tags.artist_separator', event.target.value)
              }
            >
              <option value=", ">, (comma)</option>
              <option value="; ">; (semicolon)</option>
              <option value=" / ">/ (slash)</option>
              <option value=" & ">&amp; (ampersand)</option>
            </select>
          </HintGroup>
          <HintGroup help="Write each artist, and each genre, as a separate tag value (one GENRE= per genre) for Navidrome/Jellyfin/Plex. Off writes one comma-joined value">
            {check(
              'write-multi-artist',
              !!tags?.write_multi_artist,
              (v) => store.set('metadata_enhancement', 'tags.write_multi_artist', v),
              <span>Write multi-value ARTISTS and GENRE tags</span>,
              'checkbox-label',
            )}
          </HintGroup>
          <HintGroup help="Keep only primary artist in ARTIST tag, append others as (feat. ...) in title">
            {check(
              'feat-in-title',
              !!tags?.feat_in_title,
              (v) => store.set('metadata_enhancement', 'tags.feat_in_title', v),
              <span>Move featured artists to title</span>,
              'checkbox-label',
            )}
          </HintGroup>

          <HintGroup help="When enabled, the same song can be added to the wishlist from different albums (e.g., completing a discography where albums share tracks). When disabled, tracks with the same name and artist are skipped.">
            {check(
              'allow-duplicate-tracks',
              wishlist.allow_duplicate_tracks !== false,
              (v) => store.set('wishlist', 'allow_duplicate_tracks', v),
              'Allow duplicate tracks across albums',
              'checkbox-label',
            )}
          </HintGroup>

          <HintGroup help="Wing It means a track couldn't be matched in the metadata catalogue — these are unmatched guesses, not tracks missing from your library. Off by default: guesses stay in the Wing It Pool for manual review. Turn this on to let guesses be searched like any other wishlist track.">
            {check(
              'wishlist-wing-it-guesses',
              wishlist.wing_it_guesses === true,
              (v) => store.set('wishlist', 'wing_it_guesses', v),
              <span>Search Wing It guesses as wishlist tracks</span>,
              'checkbox-label',
            )}
          </HintGroup>

          <HintGroup help="When you remove or cancel a wishlist track, SoulSync remembers not to re-add it automatically for this many days. Lower it (e.g. 7) to give removed tracks another chance sooner.">
            <label htmlFor="wishlist-ignore-ttl">Removed-track re-try window (days)</label>
            <input
              type="number"
              id="wishlist-ignore-ttl"
              min="1"
              max="365"
              step="1"
              value={ttlDraft.value}
              onChange={(event) => ttlDraft.onChange(event.target.value)}
            />
          </HintGroup>

          <div className="form-group">
            <button className="test-button" onClick={resetTemplates} style={{ background: '#666' }}>
              🔄 Reset to Defaults
            </button>
            <small className="settings-hint">
              Restores original path structure. Your downloads will be organized like before.
            </small>
          </div>
        </div>
        <div className="settings-group" data-stg={TAB}>
          <h3>Library Organize</h3>
          {check(
            'reorganize-preserve-casing',
            library.reorganize_preserve_casing !== false,
            (v) => store.set('library', 'reorganize_preserve_casing', v),
            <span>Preserve my casing, year &amp; disc layout</span>,
            'toggle-row',
          )}
          <div className="help-text" style={{ marginTop: '6px' }}>
            When the Library Organize tool re-matches a track, the metadata source often differs
            from files you've already organized: capitalization, release year (a reissue [2023]
            "corrected" to the source's 2020), or disc layout (a single-disc album matched to a
            deluxe 2-disc edition, stamping a bogus "0211" prefix). With this on (default), a
            title/album differing <em>only</em> by letter-case, your own album year, and a genuinely
            single-disc album's numbering are all left alone — no needless renames. Turn off to
            match the metadata source exactly.
          </div>
          <label className="toggle-row" style={{ marginTop: '10px' }}>
            <input
              type="checkbox"
              id="write-artist-nfo"
              checked={library.write_artist_nfo === true}
              onChange={(event) => store.set('library', 'write_artist_nfo', event.target.checked)}
            />{' '}
            <span>Write artist.nfo (Jellyfin / Kodi / Emby)</span>
          </label>
          <div className="help-text" style={{ marginTop: '6px' }}>
            When an import creates an artist folder, also write a Kodi-format <em>artist.nfo</em>{' '}
            containing the artist's name and MusicBrainz artist ID, so Jellyfin / Kodi / Emby
            identify the artist instead of guessing by name. An existing <em>artist.nfo</em> with a
            different ID is never overwritten. To backfill folders you already have, run the{' '}
            <em>Artist NFO Backfill</em> repair job.
          </div>
        </div>
      </div>
      <AudiobookOrganizationPanel
        store={store}
        hidden={media !== 'audiobooks'}
        template={template('audiobook_path')}
      />
      <div
        id="organization-podcasts-panel"
        role="tabpanel"
        aria-labelledby="organization-podcasts-tab"
        className="stg-media-panel"
        tabIndex={0}
        hidden={media !== 'podcasts'}
      >
        <div className="stg-media-context">
          <strong>Podcast organization</strong>
          <span className="stg-media-summary">Naming &amp; media format</span>
        </div>

        <div className="settings-group" data-stg={TAB}>
          <HintGroup
            help={
              <>
                Variables: <code>$show</code>, <code>$author</code>, <code>$title</code>,{' '}
                <code>$season</code> (01), <code>$seasonnum</code> (1), <code>$episode</code> (01),{' '}
                <code>$episodenum</code> (1), <code>$year</code>, <code>$date</code> (YYYY-MM-DD),{' '}
                <code>$type</code>. Default: <code>$show/Season $season/$title</code>. Season and
                episode folders collapse cleanly if omitted in feed. Use <code>{'${var}'}</code> to
                append text.
              </>
            }
          >
            <label>Podcast Path Template:</label>
            <input
              type="text"
              id="template-podcast-path"
              placeholder="$show/Season $season/$title"
              {...template('podcast_path')}
            />
          </HintGroup>

          <HintGroup
            help={
              <>
                Choose which formats to store in your podcast folders. <strong>Audio Only</strong>{' '}
                keeps standard audio files with embedded tags. <strong>Video Only</strong> remuxes
                the audio into an MP4 video container with a static high-res episode artwork frame
                and Apple/iTunes TV metadata tags so Plex, Jellyfin, and Emby can catalog them as TV
                Shows with seasons, episode posters, and playback resume.{' '}
                <strong>Audio &amp; Video</strong> keeps both formats side-by-side in the same
                folder with shared NFO sidecars so both Music and TV media libraries can index the
                same folder simultaneously.
              </>
            }
          >
            <label>Podcast Media Format:</label>
            <select
              id="podcast-media-format"
              className="form-select"
              value={text(podcasts.media_format) || 'audio'}
              onChange={(event) => store.set('podcasts', 'media_format', event.target.value)}
            >
              <option value="audio">Audio Only (MP3 / M4A) — Default</option>
              <option value="video">
                Video Only (MP4 with static artwork for TV media servers)
              </option>
              <option value="both">Audio &amp; Video (Both formats side-by-side)</option>
            </select>
          </HintGroup>
          <label className="toggle-row" style={{ marginTop: '10px' }}>
            <input
              type="checkbox"
              id="redownload-externally-deleted"
              checked={library.redownload_externally_deleted !== false}
              onChange={(event) =>
                store.set('library', 'redownload_externally_deleted', event.target.checked)
              }
            />{' '}
            <span>Re-download files deleted outside SoulSync</span>
          </label>
          <div className="help-text" style={{ marginTop: '6px' }}>
            When a library scan finds that a monitored track's file was deleted outside SoulSync,
            the track is wanted again and goes back to the Wishlist (on by default, like Lidarr).
            Turn off to treat such a deletion like <em>Don't download again</em>: the track is
            unmonitored instead.
          </div>
        </div>
      </div>
      <VideoOrganizationPanel hidden={media !== 'video'} />
    </LibrarySection>
  );
}
