/**
 * Library > Tags and artwork, Music, and Import and files, Music (settings-bic
 * phase 4): post-processing (the master switch, the core features with the
 * album art sources on the shared order list), the tag options, tag
 * embedding as one row per service with its tags as chips, the genre list
 * (moved from Downloads > Filtering, DESIGN 3.3), and the music import
 * settings. Same ids and keys.
 *
 * Each source's tag switches live in that source's own section (spotify.tags,
 * deezer.tags, ...), beside credentials Connections saves. So every key here
 * is saved alone: embed_tags, search_all_tracks, use_original_date_for_date,
 * and each tag as a dotted 'tags.<name>' key, the rest of the section kept
 * (settings-store.ts). A tag chip is a real tick box with its data-config.
 */

import { useEffect, useId, useState } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  Btn,
  Card,
  CheckChips,
  Mark,
  NumberField,
  OrderList,
  Row,
  SectionLabel,
  Select,
  Switch,
} from '../ui';
import s from '../ui/settings.module.css';
import { GenresCard } from './downloads/filtering-view';
import { useLoadedDraft, useLoads, useSettings } from './library-card';
import { MusicTagOptions, RedownloadDeleted } from './library-organization';
import { text } from './source-panels';

type Section_ = Record<string, unknown>;

/** a data-config path's value: off only when saved off */
function tagOn(settings: Record<string, unknown>, path: string) {
  let value: unknown = settings;
  for (const key of path.split('.')) value = (value as Record<string, unknown> | undefined)?.[key];
  return value !== false;
}

/** LIBRARY_SUMMARIES['library-post-processing'] */
export function postProcessingSummary(tagging: boolean, artwork: boolean) {
  const bits = [];
  if (tagging) bits.push('tagging');
  if (artwork) bits.push('artwork');
  return bits.length ? bits.join(' · ') : 'nothing enabled';
}

// ── the preferred album-art sources ──

export const ART_SOURCES = [
  { id: 'caa', name: 'Cover Art Archive', icon: '/static/img/brands/musicbrainz.png' },
  { id: 'deezer', name: 'Deezer', icon: '/static/img/brands/deezer.png' },
  { id: 'itunes', name: 'iTunes', icon: '/static/img/brands/itunes.png' },
  { id: 'spotify', name: 'Spotify', icon: '/static/img/brands/spotify.png' },
  { id: 'audiodb', name: 'TheAudioDB', icon: '/static/img/brands/audiodb.png' },
];
const artName = (id: string) => ART_SOURCES.find((src) => src.id === id)?.name ?? id;

/** the saved order first (known sources only, available or not, so a save
 *  before the availability answer can never wipe it), then each connected
 *  source not in it, switched off (loadArtSourceOrder) */
export function artList(saved: unknown, available: string[]) {
  const valid = new Set(ART_SOURCES.map((src) => src.id));
  const order: string[] = [];
  const enabled: Record<string, boolean> = {};
  for (const id of Array.isArray(saved) ? saved : []) {
    if (typeof id === 'string' && valid.has(id) && !order.includes(id)) {
      order.push(id);
      enabled[id] = true;
    }
  }
  for (const id of available) {
    if (valid.has(id) && !order.includes(id)) {
      order.push(id);
      enabled[id] = false;
    }
  }
  return { order, enabled };
}

type ArtList = { order: string[]; enabled: Record<string, boolean> };

function ArtSources({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [list, setList] = useState<ArtList | null>(null);
  useEffect(() => {
    if (!loads) return;
    const saved = (settings.metadata_enhancement || {}).album_art_order;
    setList(artList(saved, []));
    let live = true;
    void (async () => {
      let available: string[] = [];
      try {
        const data = (await (await fetch('/api/metadata/art-sources')).json()) as {
          available?: Array<string | { id: string }>;
        };
        available = (data.available || []).map((src) => (typeof src === 'string' ? src : src?.id));
      } catch {
        available = [];
      }
      if (live)
        setList((now) => {
          const base = now || artList(saved, []);
          const extra = artList([], available);
          const order = [...base.order, ...extra.order.filter((id) => !base.order.includes(id))];
          return { order, enabled: { ...extra.enabled, ...base.enabled } };
        });
    })();
    return () => {
      live = false;
    };
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  // saved: the switched-on sources in their shown order
  const commit = (next: ArtList) => {
    setList(next);
    store.set(
      'metadata_enhancement',
      'album_art_order',
      next.order.filter((id) => next.enabled[id] === true),
    );
  };

  if (!list) return <div id="art-source-list" />;
  if (!list.order.length)
    return (
      <div id="art-source-list">
        <p className={s.listEmpty}>No connected art sources available.</p>
      </div>
    );
  return (
    <div id="art-source-list">
      <OrderList
        label="Album art sources, first tried first"
        items={list.order.map((id) => {
          const src = ART_SOURCES.find((x) => x.id === id);
          return {
            id,
            name: artName(id),
            mark: <Mark src={src?.icon} icon="box" size="sm" />,
            keepAction: true,
            action: (
              <Switch
                aria-label={`Use ${artName(id)} for cover art`}
                data-art-toggle={id}
                checked={list.enabled[id] === true}
                onChange={(event) =>
                  commit({ ...list, enabled: { ...list.enabled, [id]: event.target.checked } })
                }
              />
            ),
          };
        })}
        role={(id, _index, order) => {
          if (list.enabled[id] !== true) return 'Off: not asked';
          const place = order.filter((x) => list.enabled[x] === true).indexOf(id);
          return place === 0
            ? 'Asked first'
            : `Asked ${place + 1}${['nd', 'rd'][place - 1] ?? 'th'}`;
        }}
        dropText={(_id, index) => `Drop here: place ${index + 1}`}
        afterText={(order) => {
          const first = order.find((id) => list.enabled[id] === true);
          return first ? `${artName(first)} is asked first.` : 'None is switched on.';
        }}
        onChange={(order) => commit({ ...list, order })}
        minItems={list.order.length}
        keepText="Switch it off instead"
      />
    </div>
  );
}

// ── tag embedding ──

type Tag = [key: string, label: string];
const IDS: Tag[] = [
  ['track_id', 'Track ID'],
  ['artist_id', 'Artist ID'],
  ['album_id', 'Album ID'],
];
export const TAG_GROUPS: Array<{ service: string; name: string; tags: Tag[]; icon?: string }> = [
  { service: 'spotify', name: 'Spotify', tags: IDS, icon: 'spotify.png' },
  { service: 'itunes', name: 'iTunes', tags: IDS, icon: 'itunes.png' },
  {
    service: 'musicbrainz',
    name: 'MusicBrainz',
    icon: 'musicbrainz.png',
    tags: [
      ['recording_id', 'Recording ID'],
      ['artist_id', 'Artist ID'],
      ['release_id', 'Release ID'],
      ['release_comment', 'Release Comment'],
      ['recording_comment', 'Recording Comment'],
      ['release_group_id', 'Release Group ID'],
      ['album_artist_id', 'Album Artist ID'],
      ['release_track_id', 'Release Track ID'],
      ['release_type', 'Release Type'],
      ['release_date', 'Release Date'],
      ['original_date', 'Original Date / Year'],
      ['artist_sort', 'Artist Sort Order'],
      ['album_artist_sort', 'Album Artist Sort Order'],
      ['artists', 'Artists'],
      ['label', 'Record Label'],
      ['release_status', 'Release Status'],
      ['release_country', 'Release Country'],
      ['barcode', 'Barcode'],
      ['media', 'Media'],
      ['total_discs', 'Total Discs'],
      ['catalog_number', 'Catalog Number'],
      ['script', 'Script'],
      ['asin', 'ASIN'],
      ['isrc', 'ISRC'],
      ['genres', 'Genres'],
    ],
  },
  {
    service: 'deezer',
    name: 'Deezer',
    icon: 'deezer.png',
    tags: [
      ['track_id', 'Track ID'],
      ['artist_id', 'Artist ID'],
      ['bpm', 'BPM'],
      ['isrc', 'ISRC'],
    ],
  },
  {
    service: 'audiodb',
    name: 'AudioDB',
    icon: 'audiodb.png',
    tags: [
      ['track_id', 'Track ID'],
      ['mood', 'Mood'],
      ['style', 'Style'],
      ['genre', 'Genre'],
    ],
  },
  {
    service: 'tidal',
    name: 'Tidal',
    icon: 'tidal.png',
    tags: [
      ['track_id', 'Track ID'],
      ['artist_id', 'Artist ID'],
      ['isrc', 'ISRC'],
      ['copyright', 'Copyright'],
      ['bpm', 'BPM'],
    ],
  },
  {
    service: 'qobuz',
    name: 'Qobuz',
    icon: 'qobuz.svg',
    tags: [
      ['track_id', 'Track ID'],
      ['artist_id', 'Artist ID'],
      ['isrc', 'ISRC'],
      ['copyright', 'Copyright'],
      ['label', 'Label'],
    ],
  },
  {
    service: 'lastfm',
    name: 'Last.fm',
    icon: 'lastfm.png',
    tags: [
      ['genres', 'Genres'],
      ['url', 'URL'],
    ],
  },
  {
    service: 'genius',
    name: 'Genius',
    icon: 'genius.png',
    tags: [
      ['track_id', 'Track ID'],
      ['url', 'URL'],
    ],
  },
  {
    service: 'hifi',
    name: 'HiFi',
    tags: [
      ['track_id', 'Track ID'],
      ['artist_id', 'Artist ID'],
      ['isrc', 'ISRC'],
      ['bpm', 'BPM'],
      ['copyright', 'Copyright'],
    ],
  },
  {
    service: 'beatport',
    name: 'Beatport',
    tags: [
      ['bpm', 'BPM'],
      ['key', 'Key'],
      ['label', 'Label'],
      ['catalog_number', 'Catalog number'],
      ['genre_merge', 'Genre'],
      ['track_id', 'Track ID'],
    ],
  },
];

const GENERAL_TAGS: Tag[] = [
  ['quality_tag', 'Quality'],
  ['genre_merge', 'Genre merging'],
];

/** a service's row: its switch (every tag at once), how many are on, and
 *  its tags as chips under it */
function ServiceTags({
  store,
  group,
}: {
  store: SettingsStore;
  group: (typeof TAG_GROUPS)[number] | { service: null; name: string; tags: Tag[]; icon?: string };
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [open, setOpen] = useState(false);
  const chipsId = useId();
  const section = group.service ?? 'metadata_enhancement';
  const path = (key: string) => `${section}.tags.${key}`;
  const master = group.service
    ? loads
      ? ((settings[group.service] || {}) as Section_).embed_tags !== false
      : true
    : true;
  const on = group.tags
    .filter(([key]) => (loads ? tagOn(settings, path(key)) : true))
    .map(([key]) => key);
  const mb = (settings.musicbrainz || {}) as Section_;
  const beatport = (settings.beatport || {}) as Section_;
  const words = !master
    ? `Off: none of its ${group.tags.length} tags are written`
    : `${on.length} of ${group.tags.length} tags written`;
  return (
    <>
      <Row
        label={group.name}
        htmlFor={group.service ? `embed-${group.service}` : undefined}
        help={
          group.service
            ? words
            : 'Audio quality (like FLAC, MP3-320), and genres merged from Spotify and the switched-on sources.'
        }
        mark={
          <Mark
            src={group.icon ? `/static/img/brands/${group.icon}` : null}
            icon={group.service ? 'wave' : 'sliders'}
            size="sm"
          />
        }
      >
        <Btn
          kind="ghost"
          small
          aria-expanded={open}
          aria-controls={chipsId}
          data-tags-toggle={group.service ?? 'general'}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? 'Hide tags' : `${group.tags.length} tags`}
        </Btn>
        {group.service ? (
          <Switch
            id={`embed-${group.service}`}
            aria-label={`Write ${group.name} tags`}
            checked={master}
            onChange={(event) => store.set(group.service!, 'embed_tags', event.target.checked)}
          />
        ) : null}
      </Row>
      <div className={s.tagChips} id={chipsId} hidden={!open} data-tag-group={section}>
        {group.service === 'beatport' ? (
          <p className={s.tagNote}>
            Found by ISRC, only the exact recording. Free for tracks that came from a Beatport
            chart; other electronic tracks are looked up on Beatport.
          </p>
        ) : null}
        <CheckChips
          label={`${group.name} tags`}
          options={group.tags.map(([value, label]) => ({ value, label }))}
          value={on}
          disabled={!master}
          inputAttrs={(key) => ({ 'data-config': path(key) })}
          onToggle={(key, value) => store.set(section, `tags.${key}`, value)}
        />
        {group.service === 'beatport' ? (
          <label className={s.tagExtra}>
            <Switch
              id="beatport-search-all"
              checked={beatport.search_all_tracks === true}
              disabled={!master}
              onChange={(event) => store.set('beatport', 'search_all_tracks', event.target.checked)}
            />
            <span>
              Look up every track, not just electronic ones
              <small>Slower: one Beatport request per track.</small>
            </span>
          </label>
        ) : null}
      </div>
      {group.service === 'musicbrainz' ? (
        <Row
          sub
          label="Use original release date for DATE"
          htmlFor="mb-use-original-date-for-date"
          help="The release group's first release date goes in DATE, not the downloaded edition's (a 2016 reissue of a 2010 album gets 2010)."
          more="Beets-style. The original date is kept in ORIGINALDATE either way, and a more precise date in the same year is never replaced by a vaguer one."
        >
          <Switch
            id="mb-use-original-date-for-date"
            checked={mb.use_original_date_for_date === true}
            onChange={(event) =>
              store.set('musicbrainz', 'use_original_date_for_date', event.target.checked)
            }
          />
        </Row>
      ) : null}
    </>
  );
}

/** "Fill empty tags automatically" (core/library2/tag_fill.py). unset is
 *  Fill empty tags only: the import already fills blanks the same way */
export const FILL_EMPTY_TAGS_OPTIONS = [
  { value: 'fill', label: 'Fill empty tags only' },
  { value: 'update', label: 'Fill and update' },
  { value: 'off', label: 'Off' },
] as const;

export function fillEmptyTags(raw: unknown): string {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  return FILL_EMPTY_TAGS_OPTIONS.some((o) => o.value === value) ? value : 'fill';
}

export function MusicTags({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const meta = (settings.metadata_enhancement || {}) as Section_;
  const post = (settings.post_processing || {}) as Section_;
  // before a load the markup's own defaults show (most switches on)
  const enabled = loads ? meta.enabled !== false : true;
  const duration = useLoadedDraft(store, text(post.duration_tolerance_seconds ?? 0), (raw) =>
    store.set('post_processing', 'duration_tolerance_seconds', parseFloat(raw) || 0),
  );
  const setMeta = (key: string) => (value: unknown) =>
    store.set('metadata_enhancement', key, value);
  const art = !!(loads
    ? meta.embed_album_art !== false || meta.cover_art_download !== false
    : true);
  return (
    <>
      <Card>
        <Row
          label="Enable post-processing on downloaded files"
          htmlFor="metadata-enabled"
          help="Off skips all metadata, artwork and lyrics processing: files are kept as they arrive."
          warn={
            loads && !enabled
              ? 'Post-processing is off: downloads get no tags, artwork or lyrics.'
              : undefined
          }
        >
          <Switch
            id="metadata-enabled"
            checked={enabled}
            onChange={(event) => setMeta('enabled')(event.target.checked)}
          />
        </Row>
      </Card>

      <div id="post-processing-options" hidden={!enabled}>
        <SectionLabel title="Core features" hint="What every download gets" />
        <Card>
          <Row label="Embed album art into file" htmlFor="embed-album-art">
            <Switch
              id="embed-album-art"
              checked={loads ? meta.embed_album_art !== false : true}
              onChange={(event) => setMeta('embed_album_art')(event.target.checked)}
            />
          </Row>
          <Row label="Download cover.jpg to album folder" htmlFor="cover-art-download">
            <Switch
              id="cover-art-download"
              checked={loads ? meta.cover_art_download !== false : true}
              onChange={(event) => setMeta('cover_art_download')(event.target.checked)}
            />
          </Row>
          <Row
            label="Use MusicBrainz Cover Art Archive for album art"
            htmlFor="prefer-caa-art"
            off={art && Array.isArray(meta.album_art_order) && meta.album_art_order.length > 0}
            help="Higher resolution, but quality may vary. Off uses Spotify, iTunes or Deezer art (a steady 640x640)."
            more="Ignored when the album art sources below have any switched on: add Cover Art Archive to that list instead."
          >
            <Switch
              id="prefer-caa-art"
              checked={meta.prefer_caa_art === true}
              onChange={(event) => setMeta('prefer_caa_art')(event.target.checked)}
            />
          </Row>
          <Row
            stack
            label="Preferred album art sources"
            help="The first source that has a cover wins; a miss falls through to the next. None switched on keeps how it works today."
            more="Only sources you're connected to are listed. When none matches, the download's own art is kept."
          >
            <ArtSources store={store} />
          </Row>
          <Row
            label="Match singles to their parent album"
            htmlFor="single-to-album-enabled"
            help="A track matched to a single is tagged as the album that holds it, so every song of an album gets the same cover."
            more="Off by default: it adds a metadata lookup per single-matched track."
          >
            <Switch
              id="single-to-album-enabled"
              checked={meta.single_to_album === true}
              onChange={(event) => setMeta('single_to_album')(event.target.checked)}
            />
          </Row>
          <Row label="Generate .lrc lyrics files (LRClib)" htmlFor="lrclib-enabled">
            <Switch
              id="lrclib-enabled"
              checked={loads ? meta.lrclib_enabled !== false : true}
              onChange={(event) => setMeta('lrclib_enabled')(event.target.checked)}
            />
          </Row>
          <Row
            label="Save lyrics the player finds beside library songs"
            htmlFor="lrclib-cache-player"
            help="When the player finds lyrics on LRClib for a library song, it saves them next to the file as .lrc or .txt. An existing lyrics file is never overwritten."
          >
            <Switch
              id="lrclib-cache-player"
              checked={loads ? meta.lrclib_cache_player !== false : true}
              onChange={(event) => setMeta('lrclib_cache_player')(event.target.checked)}
            />
          </Row>
          <Row
            label="Apply ReplayGain tags after download"
            htmlFor="replaygain-enabled"
            help="Measures loudness and writes ReplayGain track gain and peak tags. Needs ffmpeg; adds a few seconds per track. Songs the background loudness check measures get their tags written too."
          >
            <Switch
              id="replaygain-enabled"
              checked={post.replaygain_enabled === true}
              onChange={(event) =>
                store.set('post_processing', 'replaygain_enabled', event.target.checked)
              }
            />
          </Row>
          <Row
            field
            label="Duration tolerance"
            htmlFor="duration-tolerance-seconds"
            help="How far a file's length may drift from the expected length before it is quarantined. 0 is automatic."
            more={
              <>
                <strong>0, automatic</strong>: a file may run 3 seconds short (5 for tracks over 10
                minutes) or 15 long. Raise it if tracks are quarantined for being a few seconds off
                (live recordings, other masterings, CD pregaps). A set value applies both ways, so
                anything under 15 also tightens the long side. At most 60.
              </>
            }
          >
            <NumberField
              id="duration-tolerance-seconds"
              min="0"
              max="60"
              step="0.5"
              unit="seconds"
              value={loads ? duration.value : '0'}
              onChange={(event) => duration.onChange(event.target.value)}
            />
          </Row>
        </Card>

        <SectionLabel title="Tag options" hint="How several artists and genres are written" />
        <Card>
          <MusicTagOptions store={store} />
        </Card>

        <SectionLabel
          title="Tag embedding"
          hint="A service's switch writes all its tags or none; its tags pick which"
        />
        <Card>
          {TAG_GROUPS.map((group) => (
            <ServiceTags key={group.service} store={store} group={group} />
          ))}
          <ServiceTags
            store={store}
            group={{ service: null, name: 'General', tags: GENERAL_TAGS }}
          />
          <p className={s.cardFoot}>Supported formats: MP3, FLAC, MP4/M4A, OGG.</p>
        </Card>

        <SectionLabel title="Library files" hint="Tags the library learns after a download" />
        <Card>
          <Row
            field
            label="Fill empty tags automatically"
            htmlFor="fill-empty-tags"
            help="When the library learns something after a download (a genre, a year, a track total), SoulSync writes it into the tags the file left blank."
            more={
              <>
                <strong>Fill empty tags only</strong> never replaces a tag the file has.{' '}
                <strong>Fill and update</strong> also replaces tags that differ from the library, as
                applying a Library Re-tag finding does. Hand-tagged and locked albums, files still
                downloading or seeding, and read-only files are left alone. Every fill is listed on
                the Tools page under Filled Tags and can be undone. <strong>Off</strong> files them
                as Library Re-tag findings instead.
              </>
            }
          >
            <Select
              id="fill-empty-tags"
              value={loads ? fillEmptyTags(meta.fill_empty_tags) : 'fill'}
              options={FILL_EMPTY_TAGS_OPTIONS.map((o) => ({ ...o }))}
              onChange={(event) => setMeta('fill_empty_tags')(event.target.value)}
            />
          </Row>
        </Card>
      </div>

      <SectionLabel title="Genres" hint="Which genre tags are kept when music is tagged" />
      <GenresCard store={store} />
    </>
  );
}

// ── Import and files > Music ──

/** the import matcher's default search source (#2203); '' is Any source */
export const IMPORT_SEARCH_SOURCE_OPTIONS = [
  { value: '', label: 'Any source' },
  { value: 'musicbrainz', label: 'MusicBrainz' },
  { value: 'spotify', label: 'Spotify' },
  { value: 'deezer', label: 'Deezer' },
  { value: 'itunes', label: 'iTunes' },
  { value: 'discogs', label: 'Discogs' },
  { value: 'jiosaavn', label: 'JioSaavn' },
  { value: 'bandcamp', label: 'Bandcamp' },
] as const;

export function MusicImport({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const imp = (settings.import || {}) as Section_;
  return (
    <Card>
      <Row
        field
        id="import-default-search-source-group"
        label="Default search source"
        htmlFor="import-default-search-source"
        help="The source the Import page searches when you identify or fix a match. Any source asks your primary metadata source first; a source that isn't connected falls back to Any source."
      >
        <Select
          id="import-default-search-source"
          value={loads ? text(imp.default_search_source) : ''}
          onChange={(event) => store.set('import', 'default_search_source', event.target.value)}
          options={IMPORT_SEARCH_SOURCE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        />
      </Row>
      <Row
        label="Use the top Staging folder as the artist"
        htmlFor="import-folder-artist-override"
        help="On by default. Files staged as Artist/Album/... take the top folder as the album artist, handy for mixtapes whose tags carry DJ names."
        more={
          <>
            <strong>Turn this off</strong> if you drop a mixed pile of songs under one folder:
            otherwise every file&apos;s artist is overwritten with that folder&apos;s name (the
            cause of the &ldquo;soulsync&rdquo; mass-mislabel). Off, the artist the metadata found
            is always kept. <code>Artist/Albums/Album/...</code> works too.
          </>
        }
      >
        <Switch
          id="import-folder-artist-override"
          checked={loads ? imp.folder_artist_override !== false : false}
          onChange={(event) => store.set('import', 'folder_artist_override', event.target.checked)}
        />
      </Row>
      <Row
        label="Music library folder is my permanent library, never move files out"
        htmlFor="import-transfer-permanent"
        help="On by default. Off if your music library folder is only a drop area, not your real library."
        more={
          <>
            On, a Deep Scan never moves files out: one it doesn&apos;t recognize is added to the
            library where it sits. Off, a Deep Scan moves those files to the import folder to be
            matched and filed. Even with this off, a Deep Scan refuses to move files when the
            database looks badly out of step with the disk, so a library is never wiped (#904).
          </>
        }
      >
        <Switch
          id="import-transfer-permanent"
          checked={loads ? imp.transfer_is_permanent !== false : false}
          onChange={(event) => store.set('import', 'transfer_is_permanent', event.target.checked)}
        />
      </Row>
      <RedownloadDeleted store={store} />
    </Card>
  );
}
