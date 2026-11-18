/**
 * Library > Post-Processing and Import (SETTINGS_PORT.md, step 8d): the
 * "Processing" group, the Post-Processing card (core features, the preferred
 * album-art sources, tag embedding per source) and the Import card. Same
 * markup, classes and ids.
 *
 * Each source's tag switches live in that source's own section (spotify.tags,
 * deezer.tags, ...), beside credentials the Connections tab (still vanilla)
 * saves. So every key here is saved alone: embed_tags, search_all_tracks,
 * use_original_date_for_date, and each tag as a dotted 'tags.<name>' key, the
 * rest of the section kept (settings-store.ts).
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { HintGroup, LIBRARY_TAB, useLoadedDraft } from './library-card';
import { helpBits, Section, useHelp } from './parts';
import { text } from './source-panels';

const TAB = LIBRARY_TAB;
type Section_ = Record<string, unknown>;

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}
function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

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
  { id: 'caa', name: 'Cover Art Archive', icon: '/static/img/brands/musicbrainz.png', emoji: '🎨' },
  { id: 'deezer', name: 'Deezer', icon: '/static/img/brands/deezer.png', emoji: '🎧' },
  { id: 'itunes', name: 'iTunes', icon: '/static/img/brands/itunes.png', emoji: '🍎' },
  { id: 'spotify', name: 'Spotify', icon: '/static/img/brands/spotify.png', emoji: '🟢' },
  { id: 'audiodb', name: 'TheAudioDB', icon: null, emoji: '💿' },
];

/** the saved order first (known sources only, available or not, so a save
 *  before the availability answer can never wipe it), then each connected
 *  source not in it, switched off (loadArtSourceOrder) */
export function artList(saved: unknown, available: string[]) {
  const valid = new Set(ART_SOURCES.map((s) => s.id));
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

function ArtIcon({ src }: { src: (typeof ART_SOURCES)[number] }) {
  const [broken, setBroken] = useState(false);
  if (!src.icon || broken)
    return <span className="hybrid-source-icon emoji-icon">{src.emoji}</span>;
  return (
    <img
      className="hybrid-source-icon"
      src={src.icon}
      alt={src.name}
      onError={() => setBroken(true)}
    />
  );
}

function ArtSources({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [list, setList] = useState<{ order: string[]; enabled: Record<string, boolean> } | null>(
    null,
  );
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
        available = (data.available || []).map((s) => (typeof s === 'string' ? s : s?.id));
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
  const commit = (next: { order: string[]; enabled: Record<string, boolean> }) => {
    setList(next);
    store.set(
      'metadata_enhancement',
      'album_art_order',
      next.order.filter((id) => next.enabled[id] === true),
    );
  };

  if (!list) return <div className="hybrid-source-list" id="art-source-list"></div>;
  const on = list.order.filter((id) => list.enabled[id] === true);
  return (
    <div className="hybrid-source-list" id="art-source-list">
      {list.order.length === 0 ? (
        <div style={{ padding: '10px', color: '#888', fontSize: '13px' }}>
          No connected art sources available.
        </div>
      ) : (
        list.order.map((id, index) => {
          const src = ART_SOURCES.find((s) => s.id === id);
          if (!src) return null;
          const enabled = list.enabled[id] === true;
          const move = (direction: number) => {
            const to = index + direction;
            if (to < 0 || to >= list.order.length) return;
            const order = list.order.slice();
            [order[index], order[to]] = [order[to], order[index]];
            commit({ ...list, order });
          };
          return (
            <div
              key={id}
              className={enabled ? 'hybrid-source-item' : 'hybrid-source-item disabled'}
              data-source-id={id}
            >
              <span className="hybrid-source-arrows">
                <button className="hybrid-arrow-btn" onClick={() => move(-1)} title="Move up">
                  ▲
                </button>
                <button className="hybrid-arrow-btn" onClick={() => move(1)} title="Move down">
                  ▼
                </button>
              </span>
              <ArtIcon src={src} />
              <span className="hybrid-source-name">{src.name}</span>
              <span className="hybrid-source-priority">{enabled ? on.indexOf(id) + 1 : ''}</span>
              <label className="hybrid-source-toggle">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) =>
                    commit({ ...list, enabled: { ...list.enabled, [id]: event.target.checked } })
                  }
                />
                <span className="toggle-track"></span>
              </label>
            </div>
          );
        })
      )}
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
const TAG_GROUPS: Array<{ service: string; name: string; count: string; tags: Tag[] }> = [
  { service: 'spotify', name: 'Spotify', count: '3 tags', tags: IDS },
  { service: 'itunes', name: 'iTunes', count: '3 tags', tags: IDS },
  {
    service: 'musicbrainz',
    name: 'MusicBrainz',
    count: '19 tags',
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
    count: '4 tags',
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
    count: '4 tags',
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
    count: '5 tags',
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
    count: '5 tags',
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
    count: '2 tags',
    tags: [
      ['genres', 'Genres'],
      ['url', 'URL'],
    ],
  },
  {
    service: 'genius',
    name: 'Genius',
    count: '2 tags',
    tags: [
      ['track_id', 'Track ID'],
      ['url', 'URL'],
    ],
  },
  {
    service: 'hifi',
    name: 'HiFi',
    count: '5 tags',
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
    count: '6 tags',
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

/** one tag switch: data-config is its path, section then dotted key */
function TagBox({
  store,
  path,
  label,
  disabled,
  desc,
}: {
  store: SettingsStore;
  path: string;
  label: string;
  disabled?: boolean;
  desc?: string;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [section, ...rest] = path.split('.');
  return (
    <label className={disabled ? 'checkbox-label disabled-tag' : 'checkbox-label'}>
      <input
        type="checkbox"
        data-config={path}
        checked={loads ? tagOn(settings, path) : true}
        disabled={disabled}
        onChange={(event) => store.set(section, rest.join('.'), event.target.checked)}
      />{' '}
      {label}
      {desc ? <small className="tag-embed-desc">{desc}</small> : null}
    </label>
  );
}

/** a source's group: the master switch in the header, its tags below
 *  (toggleTagGroup opens it; a master switched off greys its tags) */
function TagGroup({
  store,
  service,
  name,
  count,
  tags,
  children,
}: {
  store: SettingsStore;
  service?: string;
  name: string;
  count: string;
  tags: ReactNode | ((masterOff: boolean) => ReactNode);
  children?: ReactNode;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const [open, setOpen] = useState(false);
  const embed = service ? (loads ? (settings[service] || {}).embed_tags !== false : true) : true;
  return (
    <div className="tag-service-group">
      <div className="tag-service-header" onClick={() => setOpen(!open)}>
        <span className={open ? 'tag-group-arrow open' : 'tag-group-arrow'}>&#9654;</span>
        {service ? (
          <label className="checkbox-label" onClick={(event) => event.stopPropagation()}>
            <input
              type="checkbox"
              id={`embed-${service}`}
              checked={embed}
              onChange={(event) => store.set(service, 'embed_tags', event.target.checked)}
            />{' '}
            {name}
          </label>
        ) : (
          <label className="checkbox-label" style={{ pointerEvents: 'none' }}>
            {name}
          </label>
        )}
        <span className="tag-service-count">{count}</span>
      </div>
      <div className="tag-service-body" style={{ display: open ? 'block' : 'none' }}>
        {children}
        {typeof tags === 'function' ? tags(!embed) : tags}
      </div>
    </div>
  );
}

/** a setting-row checkbox with its help below (the Import card's rows) */
function Row({ control, help }: { control: ReactNode; help: ReactNode }) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      <div className="setting-row">
        {control}
        {bits.icon}
      </div>
      <div className="help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

export function ProcessingCards({ store }: SettingsTabProps) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const meta = (settings.metadata_enhancement || {}) as Section_;
  const post = (settings.post_processing || {}) as Section_;
  const imp = (settings.import || {}) as Section_;

  // before a load the markup's own defaults show (most boxes ticked)
  const enabled = loads ? meta.enabled !== false : true;
  const summary = loads ? postProcessingSummary(enabled, meta.embed_album_art !== false) : '';

  const duration = useLoadedDraft(store, text(post.duration_tolerance_seconds ?? 0), (raw) =>
    store.set('post_processing', 'duration_tolerance_seconds', parseFloat(raw) || 0),
  );

  const check = (id: string, checked: boolean, save: (value: boolean) => void, label: string) => (
    <label className="checkbox-label">
      <input
        type="checkbox"
        id={id}
        checked={checked}
        onChange={(event) => save(event.target.checked)}
      />{' '}
      {label}
    </label>
  );
  const setMeta = (key: string) => (value: unknown) =>
    store.set('metadata_enhancement', key, value);
  const plain = (id: string, value: boolean, save: (v: boolean) => void, label: string) => (
    <div className="form-group">{check(id, value, save, label)}</div>
  );

  return (
    <>
      <div className="stg-cardgroup" data-stg={TAB}>
        <span className="stg-cardgroup-label">Processing</span>
        <span className="stg-cardgroup-sub">What happens to a file after it arrives</span>
      </div>
      <Section
        tab={TAB}
        title="Post-Processing"
        hint="Metadata, tags, conversion, lyrics"
        summaryKey="library-post-processing"
        summary={summary}
      >
        <div className="settings-group" data-stg={TAB}>
          <HintGroup help="Master toggle — disabling this skips all metadata, artwork, and lyrics processing.">
            {check(
              'metadata-enabled',
              enabled,
              setMeta('enabled'),
              'Enable post-processing on downloaded files',
            )}
          </HintGroup>

          <div id="post-processing-options" style={{ display: enabled ? 'block' : 'none' }}>
            <div className="post-processing-section">
              <div className="post-processing-section-title">Core Features</div>
              {plain(
                'embed-album-art',
                loads ? meta.embed_album_art !== false : true,
                setMeta('embed_album_art'),
                'Embed album art into file',
              )}
              {plain(
                'cover-art-download',
                loads ? meta.cover_art_download !== false : true,
                setMeta('cover_art_download'),
                'Download cover.jpg to album folder',
              )}
              <HintGroup help="Higher resolution but quality may vary. When off, uses Spotify/iTunes/Deezer art (consistent 640x640). Ignored when a preferred album art source list is set below — add Cover Art Archive to that list instead.">
                {check(
                  'prefer-caa-art',
                  meta.prefer_caa_art === true,
                  setMeta('prefer_caa_art'),
                  'Use MusicBrainz Cover Art Archive for album art',
                )}
              </HintGroup>
              <HintGroup
                help="Order the sources to choose whose cover art is used. The first source that has a cover wins; misses fall through to the next, and if none match, your download's own art is kept. Only sources you're connected to are shown — leave all off to keep current behavior."
                after={<ArtSources store={store} />}
              >
                <label className="checkbox-label" style={{ cursor: 'default' }}>
                  Preferred album art sources
                </label>
              </HintGroup>
              <HintGroup help="When a track matches a single release, look up the album that contains it and tag it as that album — so every song in an album gets the same (album) cover instead of some getting the single's art. Off by default: adds an extra metadata lookup per single-matched track.">
                {check(
                  'single-to-album-enabled',
                  meta.single_to_album === true,
                  setMeta('single_to_album'),
                  'Match singles to their parent album',
                )}
              </HintGroup>
              {plain(
                'lrclib-enabled',
                loads ? meta.lrclib_enabled !== false : true,
                setMeta('lrclib_enabled'),
                'Generate .lrc lyrics files (LRClib)',
              )}
              <HintGroup help="Analyzes loudness and writes ReplayGain track gain/peak tags. Requires ffmpeg. Adds a few seconds per track.">
                {check(
                  'replaygain-enabled',
                  post.replaygain_enabled === true,
                  (v) => store.set('post_processing', 'replaygain_enabled', v),
                  'Apply ReplayGain tags after download',
                )}
              </HintGroup>
              <HintGroup
                help={
                  <>
                    Maximum drift between the file's actual length and the metadata source's
                    expected length before the file is quarantined. <strong>0 = auto</strong>: files
                    can run 3s short (5s for tracks &gt;10min) or 15s long. Raise this if tracks
                    routinely quarantine for being a few seconds off (live recordings, alternate
                    masterings, CD pregaps). A set value applies both ways, so anything under 15
                    also tightens the long side. Capped at 60s.
                  </>
                }
              >
                <label htmlFor="duration-tolerance-seconds">Duration tolerance (seconds)</label>
                <input
                  type="number"
                  id="duration-tolerance-seconds"
                  min="0"
                  max="60"
                  step="0.5"
                  style={{ width: '100px' }}
                  value={loads ? duration.value : '0'}
                  onChange={(event) => duration.onChange(event.target.value)}
                />
              </HintGroup>
            </div>

            <div className="post-processing-section">
              <div className="post-processing-section-title">Tag Embedding</div>
              <small className="settings-hint" style={{ marginBottom: '8px', display: 'block' }}>
                Toggle a service to enable/disable all its tags, or expand to control individual
                tags.
              </small>

              {TAG_GROUPS.map((group) => (
                <ServiceTags key={group.service} store={store} group={group} />
              ))}

              <TagGroup
                store={store}
                name="General"
                count="2 tags"
                tags={
                  <>
                    <TagBox
                      store={store}
                      path="metadata_enhancement.tags.quality_tag"
                      label="Quality"
                      desc="Audio quality (e.g. FLAC, MP3-320)"
                    />
                    <TagBox
                      store={store}
                      path="metadata_enhancement.tags.genre_merge"
                      label="Genre Merging"
                      desc="Merge genres from Spotify + enabled sources"
                    />
                  </>
                }
              />
            </div>

            <div className="form-group">
              <label>Supported Formats:</label>
              <div className="supported-formats">MP3, FLAC, MP4/M4A, OGG</div>
            </div>
          </div>
        </div>
      </Section>

      <Section tab={TAB} title="Import" hint="Folder-artist override, permanent library">
        <div className="settings-group" data-stg={TAB}>
          <Row
            control={check(
              'import-folder-artist-override',
              loads ? imp.folder_artist_override !== false : false,
              (v) => store.set('import', 'folder_artist_override', v),
              'Use the top Staging folder as the artist',
            )}
            help={
              <>
                On by default (legacy behaviour). When enabled, files staged as{' '}
                <code>Artist/Album/…</code> (or <code>Artist/Albums/Album/…</code>) take the top
                folder as the album artist — handy for mixtapes/compilations whose embedded tags
                carry DJ names. <strong>Turn this off</strong> if you drop a mixed pile of songs
                under one container folder: otherwise every file's artist gets overwritten with that
                folder's name (the cause of the "soulsync" mass-mislabel). With it off, the
                metadata-identified artist is always kept.
              </>
            }
          />
          <Row
            control={check(
              'import-transfer-permanent',
              loads ? imp.transfer_is_permanent !== false : false,
              (v) => store.set('import', 'transfer_is_permanent', v),
              'Music library folder is my permanent library — never move files out',
            )}
            help={
              <>
                On by default for new installs. Turn this <strong>off</strong> if your music library
                folder is just a scratch drop area rather than your real, permanent library (served
                by Navidrome/Plex/etc.). A Deep Scan then never relocates files it doesn't recognize
                out to Staging — it leaves them in place and just reports them. Even with this off,
                a Deep Scan now refuses to move files when the database looks badly out of sync with
                disk, as a safety guard against wiping a library (issue #904).
              </>
            }
          />
        </div>
      </Section>
    </>
  );
}

/** a source's tag group; MusicBrainz is followed by its original-date row */
function ServiceTags({
  store,
  group,
}: {
  store: SettingsStore;
  group: (typeof TAG_GROUPS)[number];
}) {
  const settings = useSettings(store);
  const mb = (settings.musicbrainz || {}) as Section_;
  const beatport = (settings.beatport || {}) as Section_;
  const boxes = (off: boolean) => (
    <>
      {group.tags.map(([key, label]) => (
        <TagBox
          key={key}
          store={store}
          path={`${group.service}.tags.${key}`}
          label={label}
          disabled={off}
        />
      ))}
      {group.service === 'beatport' ? (
        <label className={off ? 'checkbox-label disabled-tag' : 'checkbox-label'}>
          <input
            type="checkbox"
            id="beatport-search-all"
            checked={beatport.search_all_tracks === true}
            disabled={off}
            onChange={(event) => store.set('beatport', 'search_all_tracks', event.target.checked)}
          />{' '}
          Look up every track, not just electronic ones
          <small className="tag-embed-desc">Slower: one Beatport request per track.</small>
        </label>
      ) : null}
    </>
  );
  return (
    <>
      <TagGroup
        store={store}
        service={group.service}
        name={group.name}
        count={group.count}
        tags={boxes}
      >
        {group.service === 'beatport' ? (
          <small className="tag-embed-desc">
            Found by ISRC, only the exact recording. Free for tracks that came from a Beatport
            chart; other electronic tracks are looked up on Beatport.
          </small>
        ) : null}
      </TagGroup>
      {group.service === 'musicbrainz' ? (
        <>
          <label className="toggle-row" style={{ margin: '8px 0 2px' }}>
            <input
              type="checkbox"
              id="mb-use-original-date-for-date"
              checked={mb.use_original_date_for_date === true}
              onChange={(event) =>
                store.set('musicbrainz', 'use_original_date_for_date', event.target.checked)
              }
            />{' '}
            <span>Use original release date for DATE</span>
          </label>
          <div className="help-text" style={{ marginTop: '6px' }}>
            Beets-style: write the MusicBrainz release-group's first-release-date as the DATE tag
            instead of the downloaded edition's date (a 2016 reissue of a 2010 album gets 2010). The
            original date is still kept in ORIGINALDATE either way, and a more precise same-year
            date is never replaced by a vaguer one.
          </div>
        </>
      ) : null}
    </>
  );
}
