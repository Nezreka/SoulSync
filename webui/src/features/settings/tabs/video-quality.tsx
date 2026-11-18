/**
 * The Quality tab's video sections (SETTINGS_PORT.md, video pass V1): Video
 * Quality (the named profiles, each kind's default, the ladder, the cutoff,
 * the rejects, the tie-breakers, the audio language, the library, the size
 * guards, the seeders floor), Custom Formats and YouTube Quality. Was
 * video-settings.js; they still save through /api/video/downloads/quality*,
 * every change at once, and load each time the video settings page is shown.
 *
 * The fields a person types in commit on the browser's change event (a text
 * field on leaving it, a slider on letting go), as the page did, not on each
 * keystroke: useCommit wires that.
 */

import { type ReactNode, useEffect, useRef, useState } from 'react';

import { useCommit } from './commit';
import { Section } from './parts';
import { onVideoSave, useVideoShown } from './video-shown';

const QUALITY_URL = '/api/video/downloads/quality';
const YT_QUALITY_URL = '/api/video/downloads/youtube-quality';
const TAB = 'quality';

type Shell = Window & {
  showToast?: (message: string, type?: string) => void;
  showConfirmDialog?: (o: {
    title: string;
    message: string;
    confirmText?: string;
    destructive?: boolean;
  }) => Promise<boolean>;
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => shell().showToast?.(message, type);

// pretty labels for the source × resolution ladder (the keys come from the server)
const TIER_LABEL: Record<string, string> = {
  'remux-2160p': 'Remux · 4K',
  'bluray-2160p': 'BluRay · 4K',
  'web-2160p': 'WEB-DL · 4K',
  'webrip-2160p': 'WEBRip · 4K',
  'hdtv-2160p': 'HDTV · 4K',
  'remux-1080p': 'Remux · 1080p',
  'bluray-1080p': 'BluRay · 1080p',
  'web-1080p': 'WEB-DL · 1080p',
  'webrip-1080p': 'WEBRip · 1080p',
  'hdtv-1080p': 'HDTV · 1080p',
  'bluray-720p': 'BluRay · 720p',
  'web-720p': 'WEB-DL · 720p',
  'webrip-720p': 'WEBRip · 720p',
  'hdtv-720p': 'HDTV · 720p',
  dvd: 'DVD',
  sdtv: 'SDTV',
};
const REJECT_LABEL: Record<string, string> = {
  cam: 'CAM / TS',
  screener: 'Screener',
  workprint: 'Workprint',
  '3d': '3D',
  x264: 'x264 / AVC',
};
const REJECT_ORDER = ['cam', 'screener', 'workprint', '3d', 'x264'];

async function getJson<T>(url: string): Promise<T | null> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  return r.ok ? ((await r.json()) as T) : null;
}
async function postJson<T>(url: string, body: unknown): Promise<T | null> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  return r.ok ? ((await r.json()) as T) : null;
}

// ── the profile ──

export interface Tier {
  key: string;
  enabled: boolean;
}
export interface VideoProfile {
  tiers?: Tier[];
  cutoff_resolution?: string | null;
  rejects?: string[];
  prefer_codec?: string;
  prefer_hdr?: string;
  prefer_audio?: string;
  prefer_repack?: boolean;
  audio_language_mode?: string;
  audio_languages?: string[];
  library?: string;
  max_movie_gb?: number;
  max_episode_gb?: number;
  preferred_movie_gb?: number;
  preferred_episode_gb?: number;
  min_seeders?: number | null;
  format_scores?: Record<string, number>;
  min_format_score?: number;
  [key: string]: unknown;
}
interface Named {
  id: number;
  name: string;
  profile: VideoProfile;
  /** what it's for (#2168): movie, tv, or null for both */
  media_type?: 'movie' | 'tv' | null;
  tags?: string[];
}

/** "Used for" choices: the request dialog only offers a profile for its kind */
const USED_FOR: Array<[string, ReactNode]> = [
  ['movie', 'Movies'],
  ['tv', 'TV'],
  ['both', 'Both'],
];
interface Format {
  id: number;
  name: string;
  include?: string[];
  exclude?: string[];
  score: number;
}

// audio languages a release tag can name (core/video/quality_profile.AUDIO_LANGUAGES)
const LANG_ORDER = [
  'fr',
  'de',
  'it',
  'es',
  'pt',
  'ja',
  'ko',
  'zh',
  'ru',
  'pl',
  'nl',
  'sv',
  'da',
  'no',
  'fi',
  'hi',
  'tr',
  'cs',
  'hu',
  'ar',
  'uk',
  'en',
];
const LANG_LABEL: Record<string, string> = {
  fr: 'French',
  de: 'German',
  it: 'Italian',
  es: 'Spanish',
  pt: 'Portuguese',
  ja: 'Japanese',
  ko: 'Korean',
  zh: 'Chinese',
  ru: 'Russian',
  pl: 'Polish',
  nl: 'Dutch',
  sv: 'Swedish',
  da: 'Danish',
  no: 'Norwegian',
  fi: 'Finnish',
  hi: 'Hindi',
  tr: 'Turkish',
  cs: 'Czech',
  hu: 'Hungarian',
  ar: 'Arabic',
  uk: 'Ukrainian',
  en: 'English',
};

const sizeLabel = (v: number) => (v ? `${v} GB` : 'No limit');
const prefLabel = (v: number) => (v ? `Around ${v} GB` : 'Off');
const seedersLabel = (raw: unknown) => {
  const n = parseInt(String(raw), 10) || 0;
  return n <= 0 ? 'No minimum — grab dead swarms too' : `At least ${n} seeder${n === 1 ? '' : 's'}`;
};
/** absent from a profile stored before the gate existed means the default 1 */
const seedersOf = (p: VideoProfile) =>
  p.min_seeders === undefined || p.min_seeders === null ? 1 : p.min_seeders;

/** a row of buttons, one lit (settings.js _vqSeg) */
function Seg({
  id,
  attr,
  value,
  choices,
  pick,
}: {
  id: string;
  attr: string;
  value: string | undefined;
  choices: Array<[string, ReactNode]>;
  pick: (v: string) => void;
}) {
  return (
    <div className="vq-seg" id={id}>
      {choices.map(([v, label], i) => (
        <SegButton key={v} attr={attr} v={v} on={v === value} pick={pick} space={i > 0}>
          {label}
        </SegButton>
      ))}
    </div>
  );
}
function SegButton({
  attr,
  v,
  on,
  pick,
  space,
  children,
}: {
  attr: string;
  v: string;
  on: boolean;
  pick: (v: string) => void;
  space: boolean;
  children: ReactNode;
}) {
  return (
    <>
      {space ? ' ' : null}
      <button
        type="button"
        {...{ [attr]: v }}
        className={on ? 'active' : undefined}
        onClick={() => pick(v)}
      >
        {children}
      </button>
    </>
  );
}

/** a slider whose label follows the drag and which saves on letting go */
function Slider({
  id,
  min,
  max,
  step,
  value,
  label,
  labelId,
  title,
  parse,
  commit,
}: {
  id: string;
  min: string;
  max: string;
  step: string;
  value: number;
  label: (v: number) => string;
  labelId: string;
  title: string;
  parse: (raw: string) => number;
  commit: (v: number) => void;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const ref = useCommit<HTMLInputElement>((el) => {
    setDragging(null);
    commit(parse(el.value));
  });
  const shown = dragging ?? value;
  return (
    <div className="vq-block" style={{ margin: 0 }}>
      <div className="vq-label">
        {title}{' '}
        <span className="vq-hint" id={labelId}>
          {label(shown)}
        </span>
      </div>
      <input
        type="range"
        id={id}
        className="vq-slider"
        min={min}
        max={max}
        step={step}
        ref={ref}
        value={shown}
        onChange={(event) => setDragging(parse(event.target.value))}
      />
    </div>
  );
}

export function VideoQualitySections() {
  const shown = useVideoShown();
  const [profiles, setProfiles] = useState<Named[]>([]);
  const [selectedId, setSelectedId] = useState(0);
  const [profile, setProfile] = useState<VideoProfile | null>(null);
  const [formats, setFormats] = useState<Format[]>([]);
  const [defaults, setDefaults] = useState<Record<string, number> | null>(null);
  const [defaultsBusy, setDefaultsBusy] = useState<string | null>(null);
  // each re-render of the rows the page did put the saved values back in its
  // uncontrolled fields: the key goes up with it
  const [rows, setRows] = useState(0);
  const latest = useRef({ selectedId, profile, profiles });
  latest.current = { selectedId, profile, profiles };
  const nameField = useRef<HTMLInputElement>(null);

  const named = selectedId > 0;
  const selected = profiles.find((p) => p.id === selectedId) || profiles[0];

  async function loadFormats() {
    try {
      const d = await getJson<{ formats?: Format[] }>(`${QUALITY_URL}/formats`);
      if (!d) return;
      setFormats(d.formats || []);
      setRows((n) => n + 1);
    } catch {
      // ignore
    }
  }

  async function loadKindDefaults() {
    try {
      const d = await getJson<Record<string, number>>(`${QUALITY_URL}/defaults`);
      if (d) setDefaults(d);
    } catch {
      // an older server: the selects stay on Default
    }
  }

  async function load(pick?: number) {
    try {
      const d = await getJson<{ profiles?: Named[] }>(`${QUALITY_URL}/profiles`);
      if (!d || !Array.isArray(d.profiles) || !d.profiles.length) return;
      const want = pick ?? latest.current.selectedId;
      const cur = d.profiles.find((p) => p.id === want) || d.profiles[0];
      setProfiles(d.profiles);
      setSelectedId(cur.id);
      setProfile(cur.profile);
      setRows((n) => n + 1);
      // a deleted profile may have been a default
      setDefaults(null);
      void loadKindDefaults();
      void loadFormats();
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    if (shown > 0) void load();
  }, [shown]); // eslint-disable-line react-hooks/exhaustive-deps

  /** settings.js saveQuality: the selected profile, named or the Default */
  async function save(next: VideoProfile | null = latest.current.profile, silent = true) {
    if (!next) return;
    const { selectedId: id, profiles: all } = latest.current;
    try {
      if (id > 0) {
        const cur = all.find((p) => p.id === id);
        const name = nameField.current?.value.trim() || cur?.name || 'Unnamed profile';
        const d = await postJson<{ profile?: VideoProfile; name?: string }>(
          `${QUALITY_URL}/profiles`,
          {
            id,
            name,
            profile: next,
          },
        );
        if (d?.profile) {
          const saved = d.profile;
          setProfile(saved);
          setProfiles((list) =>
            list.map((p) => (p.id === id ? { ...p, name: d.name ?? p.name, profile: saved } : p)),
          );
        }
        if (!silent) toast('Quality profile saved', 'success');
        return;
      }
      const d = await postJson<VideoProfile>(QUALITY_URL, next);
      if (d) setProfile(d);
      if (!silent) toast('Quality profile saved', 'success');
    } catch {
      // ignore
    }
  }

  // the shared save button saves the profile too, on the video side
  useEffect(() => onVideoSave(() => save()), []); // eslint-disable-line react-hooks/exhaustive-deps

  /** a change to the profile on screen, saved at once */
  function change(update: (p: VideoProfile) => VideoProfile) {
    const cur = latest.current.profile;
    if (!cur) return;
    const next = update(cur);
    setProfile(next);
    latest.current.profile = next;
    void save(next);
  }

  function pickProfile(id: number) {
    const cur = profiles.find((p) => p.id === id);
    setSelectedId(id);
    latest.current.selectedId = id;
    if (cur) {
      setProfile(cur.profile);
      setRows((n) => n + 1);
    }
  }

  async function newProfile() {
    try {
      const res = await postJson<{ success?: boolean; id: number }>(`${QUALITY_URL}/profiles`, {
        name: 'New profile',
        profile,
      });
      if (!res?.success) throw new Error();
      toast(
        'Profile created — rename it, tweak it, then assign it from a title’s Manage panel',
        'success',
      );
      void load(res.id);
    } catch {
      toast('Couldn’t create the profile', 'error');
    }
  }

  async function deleteProfile() {
    if (selectedId <= 0) return;
    const remove = async () => {
      try {
        const r = await fetch(`${QUALITY_URL}/profiles/${selectedId}`, { method: 'DELETE' });
        const res = r.ok ? ((await r.json()) as { success?: boolean }) : null;
        if (!res?.success) throw new Error();
        toast('Profile deleted — titles using it fall back to Default', 'success');
        void load(0);
      } catch {
        toast('Couldn’t delete the profile', 'error');
      }
    };
    const confirm = shell().showConfirmDialog;
    if (!confirm) return void remove();
    const ok = await confirm({
      title: 'Delete this quality profile?',
      message: 'Titles assigned to it will use the Default profile instead.',
      confirmText: 'Delete',
      destructive: true,
    });
    if (ok) void remove();
  }

  /** what a named profile is for (#2168): movies, tv or both */
  async function setUsedFor(raw: string) {
    const id = latest.current.selectedId;
    if (id <= 0) return;
    const mediaType = raw === 'both' ? null : raw;
    try {
      const r = await fetch(`${QUALITY_URL}/profiles/${id}/media-type`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ media_type: mediaType }),
      });
      const d = (await r.json()) as {
        success?: boolean;
        media_type?: string | null;
        error?: string;
      };
      if (!r.ok || !d.success) {
        toast(d?.error || 'Couldn’t save what it’s for', 'error');
        return;
      }
      setProfiles((all) =>
        all.map((p) =>
          p.id === id ? { ...p, media_type: (d.media_type as Named['media_type']) ?? null } : p,
        ),
      );
      toast(
        mediaType === null
          ? 'Offered for movies and TV'
          : `Offered only for ${mediaType === 'movie' ? 'movies' : 'TV'}`,
        'success',
      );
    } catch {
      toast('Couldn’t save what it’s for', 'error');
    }
  }

  async function setKindDefault(kind: string, raw: string, label: string) {
    setDefaultsBusy(kind);
    try {
      const r = await fetch(`${QUALITY_URL}/defaults`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ kind, profile_id: parseInt(raw, 10) || 0 }),
      });
      const d = (await r.json()) as Record<string, number> & { error?: string };
      if (!r.ok) {
        toast(d?.error || 'Could not save the default', 'error');
        setDefaults(null);
        void loadKindDefaults();
        return;
      }
      setDefaults(d);
      toast(`New ${kind === 'movie' ? 'movies' : 'shows'} use ${label}`, 'success');
    } catch {
      // ignore
    } finally {
      setDefaultsBusy(null);
    }
  }

  const nameRef = useCommit<HTMLInputElement>(() => void save());
  const p = profile;
  const tiers = p?.tiers || [];
  const langMode = p?.audio_language_mode || 'off';
  const langs = p?.audio_languages || [];
  const profileOptions = profiles.length ? profiles : [{ id: 0, name: 'Default', profile: {} }];

  return (
    <>
      <Section tab={TAB} title="Video Quality" hint="Ladder, cutoff, rejects & tie-breakers">
        <div className="settings-group" data-stg={TAB}>
          <div className="setting-help-text">
            Profiles apply <strong>per title</strong>: pick one in a title's Get or Manual search,
            or its Manage panel. A title with none uses its kind's default below. Quality is read
            from the release name — pick the ladder you'll accept, where to stop upgrading, what to
            reject outright, then the soft tie-breakers.
          </div>
          <div
            className="form-group"
            style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}
          >
            <select
              className="library-source-filter-select"
              data-vq-profile-select=""
              title="Which profile you're editing"
              value={String(selectedId)}
              onChange={(event) => pickProfile(parseInt(event.target.value, 10) || 0)}
            >
              {profileOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>{' '}
            <input
              type="text"
              className={named ? undefined : 'hidden'}
              data-vq-profile-name=""
              placeholder="Profile name"
              style={{ maxWidth: '180px' }}
              key={`name-${rows}-${selectedId}`}
              defaultValue={named ? selected?.name || '' : ''}
              ref={(el) => {
                nameField.current = el;
                nameRef(el);
              }}
            />{' '}
            <button
              className="test-button"
              type="button"
              data-vq-profile-new=""
              title="Duplicate what's on screen as a new named profile"
              onClick={() => void newProfile()}
            >
              + New profile
            </button>{' '}
            <button
              className={named ? 'test-button' : 'test-button hidden'}
              type="button"
              data-vq-profile-delete=""
              style={{ background: '#663333' }}
              onClick={() => void deleteProfile()}
            >
              Delete
            </button>
          </div>
          {named ? (
            <div
              className="form-group"
              data-vq-used-for=""
              style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}
            >
              <span className="vq-label" style={{ margin: 0 }}>
                Used for
              </span>
              <Seg
                id="vq-used-for"
                attr="data-vq-used-for-pick"
                value={selected?.media_type || 'both'}
                choices={USED_FOR}
                pick={(v) => void setUsedFor(v)}
              />
              <span className="vq-hint">
                Members asking for a movie only see Movies and Both profiles; shows, TV and Both.
              </span>
            </div>
          ) : null}
          <div
            className="form-group"
            data-vq-kind-defaults=""
            style={{ display: 'flex', gap: '14px', alignItems: 'center', flexWrap: 'wrap' }}
          >
            {(
              [
                [
                  'movie',
                  'New movies use',
                  "The profile a movie gets when you haven't picked one for it",
                ],
                [
                  'show',
                  'New shows use',
                  "The profile a show gets when you haven't picked one for it",
                ],
              ] as const
            ).map(([kind, words, title], i) => (
              <KindDefault
                key={kind}
                space={i > 0}
                kind={kind}
                words={words}
                title={title}
                value={String((defaults && defaults[kind]) || 0)}
                options={profileOptions}
                busy={defaultsBusy === kind}
                pick={(raw, label) => void setKindDefault(kind, raw, label)}
              />
            ))}
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Quality ladder{' '}
              <span className="vq-hint">
                toggle what you'll accept · rank best→worst with the arrows
              </span>
            </div>
            <div className="hybrid-source-list" id="vq-tier-rows">
              {tiers.map((t, i) => (
                <div
                  key={t.key}
                  className={t.enabled ? 'hybrid-source-item' : 'hybrid-source-item disabled'}
                >
                  <span className="hybrid-source-arrows">
                    <button
                      type="button"
                      className="hybrid-arrow-btn"
                      data-vq-tier-move={t.key}
                      data-dir="-1"
                      disabled={i === 0}
                      title="Move up"
                      onClick={() => change((cur) => moveTier(cur, t.key, -1))}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      className="hybrid-arrow-btn"
                      data-vq-tier-move={t.key}
                      data-dir="1"
                      disabled={i === tiers.length - 1}
                      title="Move down"
                      onClick={() => change((cur) => moveTier(cur, t.key, 1))}
                    >
                      ▼
                    </button>
                  </span>
                  <span className="hybrid-source-name">{TIER_LABEL[t.key] || t.key}</span>
                  <span className="hybrid-source-priority">{i + 1}</span>
                  <label className="hybrid-source-toggle">
                    <input
                      type="checkbox"
                      data-vq-tier-toggle={t.key}
                      checked={!!t.enabled}
                      onChange={(event) => {
                        const on = event.target.checked;
                        change((cur) => ({
                          ...cur,
                          tiers: (cur.tiers || []).map((x) =>
                            x.key === t.key ? { ...x, enabled: on } : x,
                          ),
                        }));
                      }}
                    />
                    <span className="toggle-track"></span>
                  </label>
                </div>
              ))}
            </div>
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Upgrade until{' '}
              <span className="vq-hint">
                once you have this resolution (or better) stop chasing upgrades
              </span>
            </div>
            <select
              id="vq-cutoff"
              className="vq-select"
              value={p ? (p.cutoff_resolution == null ? '1080p' : p.cutoff_resolution) : '2160p'}
              onChange={(event) => {
                const v = event.target.value;
                change((cur) => ({ ...cur, cutoff_resolution: v }));
              }}
            >
              <option value="2160p">4K · 2160p</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p (SD)</option>
              <option value="">Best available — never stop upgrading</option>
            </select>
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Never grab{' '}
              <span className="vq-hint">hard rejects — skipped no matter what else matches</span>
            </div>
            <div className="vq-chips" id="vq-rejects">
              {p
                ? REJECT_ORDER.map((k) => {
                    const on = (p.rejects || []).includes(k);
                    return (
                      <button
                        key={k}
                        type="button"
                        className={on ? 'vq-chip on' : 'vq-chip'}
                        data-vq-reject={k}
                        onClick={() => change((cur) => ({ ...cur, rejects: flip(cur.rejects, k) }))}
                      >
                        {REJECT_LABEL[k] || k}
                      </button>
                    );
                  })
                : null}
            </div>
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Preferences{' '}
              <span className="vq-hint">
                soft tie-breakers — they rank candidates, they never reject on their own
              </span>
            </div>
            <div className="vq-prefs">
              <div className="vq-pref-row">
                <span className="vq-pref-name">Codec</span>
                <Seg
                  id="vq-codec"
                  attr="data-vq-codec"
                  value={p?.prefer_codec}
                  choices={[
                    ['any', 'Any'],
                    ['hevc', 'HEVC / x265'],
                    ['av1', 'AV1'],
                  ]}
                  pick={(v) => change((cur) => ({ ...cur, prefer_codec: v }))}
                />
              </div>
              <div className="vq-pref-row">
                <span className="vq-pref-name">HDR</span>
                <Seg
                  id="vq-hdr"
                  attr="data-vq-hdr"
                  value={p?.prefer_hdr}
                  choices={[
                    ['off', "Don't care"],
                    ['prefer', 'Prefer'],
                    ['require', 'Require'],
                  ]}
                  pick={(v) => change((cur) => ({ ...cur, prefer_hdr: v }))}
                />
              </div>
              <div className="vq-pref-row">
                <span className="vq-pref-name">Audio</span>
                <Seg
                  id="vq-audio"
                  attr="data-vq-audio"
                  value={p?.prefer_audio}
                  choices={[
                    ['any', 'Any'],
                    ['surround', 'Surround'],
                    ['lossless', 'Lossless'],
                    ['atmos', 'Atmos'],
                  ]}
                  pick={(v) => change((cur) => ({ ...cur, prefer_audio: v }))}
                />
              </div>
            </div>
            <label className="vq-check" style={{ marginTop: '12px' }}>
              <input
                type="checkbox"
                id="vq-prefer-repack"
                checked={!!p?.prefer_repack}
                onChange={(event) => {
                  const on = event.target.checked;
                  change((cur) => ({ ...cur, prefer_repack: on }));
                }}
              />{' '}
              <span className="vq-check-box"></span> <span>Prefer REPACK / PROPER re-releases</span>
            </label>
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Audio language{' '}
              <span className="vq-hint">
                for dubbed or MULTI releases. a release with no language tag is the original audio
              </span>
            </div>
            <Seg
              id="vq-lang-mode"
              attr="data-vq-lang-mode"
              value={p ? langMode : undefined}
              choices={[
                ['off', "Don't care"],
                ['prefer', 'Prefer'],
                ['require', 'Require'],
              ]}
              pick={(v) => change((cur) => ({ ...cur, audio_language_mode: v }))}
            />
            <div
              className="vq-chips"
              id="vq-langs"
              style={{ marginTop: '10px' }}
              hidden={!p || langMode === 'off'}
            >
              {p
                ? LANG_ORDER.map((k) => {
                    const on = langs.includes(k);
                    // vq-chip--pick: a language you want, not a tag you reject
                    return (
                      <button
                        key={k}
                        type="button"
                        className={on ? 'vq-chip vq-chip--pick on' : 'vq-chip vq-chip--pick'}
                        data-vq-lang={k}
                        aria-pressed={on ? 'true' : 'false'}
                        onClick={() =>
                          change((cur) => ({
                            ...cur,
                            audio_languages: flip(cur.audio_languages, k),
                          }))
                        }
                      >
                        {LANG_LABEL[k] || k}
                      </button>
                    );
                  })
                : null}
            </div>
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Save to{' '}
              <span className="vq-hint">
                which library new titles on this profile are filed in. the 4K library is used once
                its folder is switched on in Settings &gt; Folders, until then they go to the main
                one. titles you already have stay put
              </span>
            </div>
            <Seg
              id="vq-library"
              attr="data-vq-library"
              value={p ? (p.library === '4k' ? '4k' : 'main') : undefined}
              choices={[
                ['main', 'Main library'],
                ['4k', '4K library'],
              ]}
              pick={(v) => change((cur) => ({ ...cur, library: v }))}
            />
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Max size{' '}
              <span className="vq-hint">
                skip anything bigger — set per type so a movie and an episode aren't judged the same
              </span>
            </div>
            <div className="vq-grid">
              <Slider
                key={`mv-${rows}`}
                id="vq-movie-size"
                labelId="vq-movie-label"
                title="Movie"
                min="0"
                max="200"
                step="1"
                value={p?.max_movie_gb || 0}
                label={sizeLabel}
                parse={(raw) => parseInt(raw, 10) || 0}
                commit={(v) => change((cur) => ({ ...cur, max_movie_gb: v }))}
              />
              <Slider
                key={`ep-${rows}`}
                id="vq-episode-size"
                labelId="vq-episode-label"
                title="TV episode"
                min="0"
                max="200"
                step="1"
                value={p?.max_episode_gb || 0}
                label={sizeLabel}
                parse={(raw) => parseInt(raw, 10) || 0}
                commit={(v) => change((cur) => ({ ...cur, max_episode_gb: v }))}
              />
            </div>
          </div>
          <div className="vq-block">
            <div className="vq-label">
              Preferred size{' '}
              <span className="vq-hint">
                a tie-break only: among releases equal on quality and custom formats, the one
                closest to this size ranks first. off at 0, never past the max size
              </span>
            </div>
            <div className="vq-grid">
              <Slider
                key={`mp-${rows}`}
                id="vq-movie-pref"
                labelId="vq-movie-pref-label"
                title="Movie"
                min="0"
                max="100"
                step="0.5"
                value={p?.preferred_movie_gb || 0}
                label={prefLabel}
                parse={(raw) => parseFloat(raw) || 0}
                commit={(v) => change((cur) => ({ ...cur, preferred_movie_gb: v }))}
              />
              <Slider
                key={`epp-${rows}`}
                id="vq-episode-pref"
                labelId="vq-episode-pref-label"
                title="TV episode"
                min="0"
                max="10"
                step="0.1"
                value={p?.preferred_episode_gb || 0}
                label={prefLabel}
                parse={(raw) => parseFloat(raw) || 0}
                commit={(v) => change((cur) => ({ ...cur, preferred_episode_gb: v }))}
              />
            </div>
          </div>
          <Seeders
            key={`sd-${rows}`}
            value={p ? seedersOf(p) : 1}
            commit={(v) => change((cur) => ({ ...cur, min_seeders: v }))}
          />
        </div>
        <CustomFormats
          formats={formats}
          setFormats={setFormats}
          rows={rows}
          bump={() => setRows((n) => n + 1)}
          profile={p}
          change={change}
        />
      </Section>
      <YoutubeQuality shown={shown} />
    </>
  );
}

function flip(list: string[] | undefined, k: string) {
  const arr = Array.isArray(list) ? [...list] : [];
  const i = arr.indexOf(k);
  if (i === -1) arr.push(k);
  else arr.splice(i, 1);
  return arr;
}

function moveTier(p: VideoProfile, key: string, dir: number): VideoProfile {
  const arr = [...(p.tiers || [])];
  const i = arr.findIndex((t) => t.key === key);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= arr.length) return p;
  [arr[i], arr[j]] = [arr[j], arr[i]];
  return { ...p, tiers: arr };
}

function KindDefault({
  space,
  kind,
  words,
  title,
  value,
  options,
  busy,
  pick,
}: {
  space: boolean;
  kind: string;
  words: string;
  title: string;
  value: string;
  options: Array<{ id: number; name: string }>;
  busy: boolean;
  pick: (raw: string, label: string) => void;
}) {
  return (
    <>
      {space ? ' ' : null}
      <label style={{ display: 'inline-flex', gap: '8px', alignItems: 'center' }}>
        <span>{words}</span>{' '}
        <select
          className="library-source-filter-select"
          data-vq-kind-default={kind}
          title={title}
          value={value}
          disabled={busy}
          onChange={(event) =>
            pick(event.target.value, event.target.options[event.target.selectedIndex]?.text || '')
          }
        >
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function Seeders({ value, commit }: { value: number; commit: (v: number) => void }) {
  const [dragging, setDragging] = useState<string | null>(null);
  const ref = useCommit<HTMLInputElement>((el) => {
    setDragging(null);
    commit(parseInt(el.value, 10) || 0);
  });
  return (
    <div className="vq-block">
      <div className="vq-label">
        Minimum seeders{' '}
        <span className="vq-hint" id="vq-seeders-label">
          {seedersLabel(dragging ?? value)}
        </span>
      </div>
      <input
        type="range"
        id="vq-min-seeders"
        className="vq-slider"
        min="0"
        max="100"
        step="1"
        ref={ref}
        value={dragging ?? value}
        onChange={(event) => setDragging(event.target.value)}
      />
      <div className="setting-help-text">
        A torrent nobody is seeding never finishes — it parks on "downloading metadata" or stalls at
        0% and quietly fills your client with dead entries. Set 0 to grab regardless. Usenet and
        Soulseek have no seeders and are never affected.
      </div>
    </div>
  );
}

// ── custom formats ──

const split = (s: string) =>
  s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

function CustomFormats({
  formats,
  setFormats,
  rows,
  bump,
  profile,
  change,
}: {
  formats: Format[];
  setFormats: (update: (list: Format[]) => Format[]) => void;
  rows: number;
  bump: () => void;
  profile: VideoProfile | null;
  change: (update: (p: VideoProfile) => VideoProfile) => void;
}) {
  const overrides = profile?.format_scores || {};
  const minRef = useCommit<HTMLInputElement>((el) =>
    change((cur) => ({ ...cur, min_format_score: parseInt(el.value, 10) || 0 })),
  );

  async function saveRow(row: HTMLElement) {
    const val = (k: string) =>
      row.querySelector<HTMLInputElement>(`[data-vq-fmt-f="${k}"]`)?.value || '';
    const f = {
      id: parseInt(row.getAttribute('data-vq-fmt') || '', 10),
      name: val('name'),
      include: split(val('include')),
      exclude: split(val('exclude')),
      score: parseInt(val('score'), 10) || 0,
    };
    try {
      const res = await postJson<Format & { success?: boolean }>(`${QUALITY_URL}/formats`, f);
      if (!res?.success) {
        toast('A format needs a name and at least one term', 'error');
        return;
      }
      setFormats((list) =>
        list.map((x) =>
          x.id === res.id
            ? {
                id: res.id,
                name: res.name,
                include: res.include,
                exclude: res.exclude,
                score: res.score,
              }
            : x,
        ),
      );
    } catch {
      toast('Couldn’t save the format', 'error');
    }
  }

  // one listener for the rows, as the page delegated it: an override is the
  // selected profile's, any other field saves the format
  const host = useCommitAll((target) => {
    const row = target.closest<HTMLElement>('[data-vq-fmt]');
    if (!row) return;
    if (target.matches('[data-vq-fmt-f="override"]')) {
      if (!profile) return;
      const id = row.getAttribute('data-vq-fmt') || '';
      const v = (target as HTMLInputElement).value.trim();
      change((cur) => {
        const fs = { ...cur.format_scores };
        if (v === '') delete fs[id];
        else fs[id] = parseInt(v, 10) || 0;
        return { ...cur, format_scores: fs };
      });
      return;
    }
    void saveRow(row);
  });

  async function remove(id: number) {
    try {
      const r = await fetch(`${QUALITY_URL}/formats/${id}`, { method: 'DELETE' });
      const res = r.ok ? ((await r.json()) as { success?: boolean }) : null;
      if (!res?.success) throw new Error();
      setFormats((list) => list.filter((f) => f.id !== id));
      bump();
    } catch {
      toast('Couldn’t delete the format', 'error');
    }
  }

  async function add() {
    try {
      const res = await postJson<Format & { success?: boolean }>(`${QUALITY_URL}/formats`, {
        name: 'New format',
        include: ['REPLACE-ME'],
        score: 10,
      });
      if (!res?.success) throw new Error();
      setFormats((list) => [
        ...list,
        {
          id: res.id,
          name: res.name,
          include: res.include,
          exclude: res.exclude,
          score: res.score,
        },
      ]);
      bump();
    } catch {
      toast('Couldn’t add a format', 'error');
    }
  }

  return (
    <div className="settings-group" data-stg={TAB}>
      <h3>Custom Formats</h3>
      <div className="setting-help-text">
        Score releases by their <strong>name</strong> — prefer trusted groups, bury x265, chase
        freeleech, anything a pattern can see. Terms are plain substrings, or regex written{' '}
        <code>/x265|hevc/</code>. A format matches when <em>all</em> "match" terms hit and{' '}
        <em>no</em> "never" term does; matching formats add their score to a release's rank
        (negative buries without rejecting). The "this profile" column overrides a format's score
        for the profile selected above.
      </div>
      <div id="vq-format-rows" ref={host}>
        {formats.length ? (
          formats.map((f) => {
            const ov = overrides[String(f.id)];
            return (
              <div className="vq-fmt-row" data-vq-fmt={f.id} key={`${f.id}-${rows}`}>
                <input
                  className="vq-fmt-in"
                  data-vq-fmt-f="name"
                  defaultValue={f.name}
                  placeholder="Name"
                />
                <input
                  className="vq-fmt-in"
                  data-vq-fmt-f="include"
                  defaultValue={(f.include || []).join(', ')}
                  placeholder="match: term, /regex/"
                />
                <input
                  className="vq-fmt-in"
                  data-vq-fmt-f="exclude"
                  defaultValue={(f.exclude || []).join(', ')}
                  placeholder="never: term, /regex/"
                />
                <input
                  className="vq-fmt-in vq-fmt-num"
                  data-vq-fmt-f="score"
                  type="number"
                  defaultValue={f.score}
                  title="Default score"
                />
                <input
                  className="vq-fmt-in vq-fmt-num"
                  data-vq-fmt-f="override"
                  type="number"
                  defaultValue={ov == null ? '' : ov}
                  placeholder="—"
                  title="Score for the selected profile (blank = default)"
                />
                <button
                  className="vq-fmt-del"
                  type="button"
                  data-vq-fmt-del={f.id}
                  title="Delete format"
                  onClick={() => void remove(f.id)}
                >
                  ✕
                </button>
              </div>
            );
          })
        ) : rows ? (
          <div className="settings-hint" style={{ padding: '6px 0' }}>
            No custom formats yet — releases rank purely by the ladder + tie-breakers.
          </div>
        ) : null}
      </div>
      <button
        className="test-button"
        type="button"
        data-vq-format-add=""
        onClick={() => void add()}
      >
        + Add format
      </button>
      <div className="form-group" style={{ marginTop: '10px' }}>
        <label>Minimum format score (this profile):</label>
        <input
          type="number"
          id="vq-min-format-score"
          style={{ maxWidth: '120px' }}
          key={`min-${rows}`}
          defaultValue={(profile && profile.min_format_score) || 0}
          ref={minRef}
        />
        <small className="settings-hint">
          Releases whose summed format score falls below this are rejected outright. 0 = off.
        </small>
      </div>
    </div>
  );
}

/** the browser's change event from anything inside (the format rows) */
function useCommitAll(commit: (target: HTMLElement) => void) {
  const ref = useRef<HTMLDivElement>(null);
  const latest = useRef(commit);
  latest.current = commit;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const listener = (event: Event) => latest.current(event.target as HTMLElement);
    el.addEventListener('change', listener);
    return () => el.removeEventListener('change', listener);
  }, []);
  return ref;
}

// ── YouTube quality ──

interface YtProfile {
  max_resolution?: string;
  video_codec?: string;
  container?: string;
  prefer_60fps?: boolean;
  allow_hdr?: boolean;
  [key: string]: unknown;
}

function YoutubeQuality({ shown }: { shown: number }) {
  const [p, setP] = useState<YtProfile | null>(null);
  const latest = useRef(p);
  latest.current = p;

  useEffect(() => {
    if (!shown) return;
    getJson<YtProfile>(YT_QUALITY_URL)
      .then((d) => {
        if (d) setP(d);
      })
      .catch(() => {});
  }, [shown]);

  async function save(next: YtProfile | null = latest.current) {
    if (!next) return;
    try {
      const d = await postJson<YtProfile>(YT_QUALITY_URL, next);
      if (d) setP(d);
    } catch {
      // ignore
    }
  }
  useEffect(() => onVideoSave(() => save()), []); // eslint-disable-line react-hooks/exhaustive-deps

  function change(update: (cur: YtProfile) => YtProfile) {
    if (!latest.current) return;
    const next = update(latest.current);
    setP(next);
    latest.current = next;
    void save(next);
  }

  return (
    <Section
      tab={TAB}
      title="YouTube Quality"
      hint="Resolution ceiling, codec & container for followed channels"
    >
      <div className="settings-group" data-stg={TAB}>
        <div className="setting-help-text">
          A separate, simpler profile for the channels you follow — YouTube is grabbed with yt-dlp,
          so it's just a resolution ceiling, a codec preference and a container.
        </div>
        <div className="vq-block">
          <div className="vq-label">
            Max resolution{' '}
            <span className="vq-hint">
              the most yt-dlp will grab — it steps down to the next available below this
            </span>
          </div>
          <select
            id="yq-resolution"
            className="vq-select"
            value={p ? p.max_resolution || '1080p' : 'best'}
            onChange={(event) => {
              const v = event.target.value;
              change((cur) => ({ ...cur, max_resolution: v }));
            }}
          >
            <option value="best">Best available</option>
            <option value="4320p">8K · 4320p</option>
            <option value="2160p">4K · 2160p</option>
            <option value="1440p">1440p</option>
            <option value="1080p">1080p</option>
            <option value="720p">720p</option>
            <option value="480p">480p</option>
            <option value="360p">360p</option>
          </select>
        </div>
        <div className="vq-block">
          <div className="vq-label">
            Preferences{' '}
            <span className="vq-hint">
              codec is a soft preference; container is the final muxed file
            </span>
          </div>
          <div className="vq-prefs">
            <div className="vq-pref-row">
              <span className="vq-pref-name">Codec</span>
              <Seg
                id="yq-codec"
                attr="data-yq-codec"
                value={p?.video_codec}
                choices={[
                  ['any', 'Best'],
                  ['av1', 'AV1'],
                  ['vp9', 'VP9'],
                  ['h264', 'H.264'],
                ]}
                pick={(v) => change((cur) => ({ ...cur, video_codec: v }))}
              />
            </div>
            <div className="vq-pref-row">
              <span className="vq-pref-name">Container</span>
              <Seg
                id="yq-container"
                attr="data-yq-container"
                value={p?.container}
                choices={[
                  ['mp4', 'MP4'],
                  ['mkv', 'MKV'],
                  ['webm', 'WebM'],
                ]}
                pick={(v) => change((cur) => ({ ...cur, container: v }))}
              />
            </div>
          </div>
          <label className="vq-check" style={{ marginTop: '12px' }}>
            <input
              type="checkbox"
              id="yq-60fps"
              checked={!!p?.prefer_60fps}
              onChange={(event) => {
                const on = event.target.checked;
                change((cur) => ({ ...cur, prefer_60fps: on }));
              }}
            />{' '}
            <span className="vq-check-box"></span> <span>Prefer 60fps when available</span>
          </label>{' '}
          <label className="vq-check" style={{ marginTop: '10px' }}>
            <input
              type="checkbox"
              id="yq-hdr"
              checked={!!p?.allow_hdr}
              onChange={(event) => {
                const on = event.target.checked;
                change((cur) => ({ ...cur, allow_hdr: on }));
              }}
            />{' '}
            <span className="vq-check-box"></span> <span>Allow HDR (off = always grab SDR)</span>
          </label>
        </div>
      </div>
    </Section>
  );
}
