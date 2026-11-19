/**
 * Quality > Movies and TV and Quality > YouTube (SETTINGS_PORT.md, video pass
 * V1; on the settings parts in settings-bic phase 3): the named profiles and
 * each kind's default, the ladder on the shared order list (a switch per
 * quality), the cutoff, the rejects, the tie-breakers, the audio language,
 * the library, the size guards, the seeders floor, custom formats; and the
 * YouTube profile. They still save through /api/video/downloads/quality*,
 * every change at once, and load each time the video settings page is shown
 * (or its pill opens on the music side).
 *
 * The fields a person types in commit on the browser's change event (a text
 * field on leaving it, a slider on letting go), as the page did, not on each
 * keystroke: useCommit wires that.
 */

import { useEffect, useRef, useState } from 'react';

import {
  Btn,
  Card,
  Chips,
  FieldLine,
  Hero,
  NumberField,
  OrderList,
  Row,
  SectionLabel,
  Segmented,
  Select,
  Switch,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
import { useCommit } from './commit';
import { videoSentence, youtubeSentence } from './quality/words';
import { onVideoSave, useVideoShown } from './video-shown';

const QUALITY_URL = '/api/video/downloads/quality';
const YT_QUALITY_URL = '/api/video/downloads/youtube-quality';

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
const USED_FOR: Array<[string, string]> = [
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
  return n <= 0 ? 'No minimum: dead swarms too' : `At least ${n} seeder${n === 1 ? '' : 's'}`;
};
/** absent from a profile stored before the gate existed means the default 1 */
const seedersOf = (p: VideoProfile) =>
  p.min_seeders === undefined || p.min_seeders === null ? 1 : p.min_seeders;

/** a slider whose words follow the drag and which saves on letting go */
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
    <Row sub label={title} htmlFor={id}>
      <span className={s.rangeLine}>
        <span className={s.rangeSay} id={labelId}>
          {label(shown)}
        </span>
        <input
          type="range"
          id={id}
          className={s.range}
          min={min}
          max={max}
          step={step}
          ref={ref}
          value={shown}
          onChange={(event) => setDragging(parse(event.target.value))}
        />
      </span>
    </Row>
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
  const tierName = (key: string) => TIER_LABEL[key] || key;
  const on = tiers.filter((t) => t.enabled);

  return (
    <div data-video-quality="">
      <Hero
        stats={
          p
            ? [
                {
                  value: profileOptions.length,
                  label: profileOptions.length === 1 ? 'Profile' : 'Profiles',
                },
                { value: `${on.length} of ${tiers.length}`, label: 'Qualities on' },
                { value: formats.length, label: formats.length === 1 ? 'Format' : 'Formats' },
              ]
            : undefined
        }
      >
        {p
          ? videoSentence({
              name: selected?.name || 'Default',
              tiers: on.map((t) => tierName(t.key)),
              cutoff: p.cutoff_resolution,
            })
          : 'Loading the video quality profiles.'}
      </Hero>

      <SectionLabel
        title="Profiles"
        hint="A title uses the profile picked for it, else its kind's default below."
      />
      <Card>
        <Row
          label="Profile you're editing"
          htmlFor="vq-profile-select"
          field
          help="Pick one for a title in its Get or Manual search, or its Manage panel."
          more="Quality is read from the release name: pick the qualities you will accept, where to stop upgrading, what to reject outright, then the soft tie-breakers."
        >
          <FieldLine>
            <Select
              id="vq-profile-select"
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
            </Select>
            <Btn
              small
              icon="plus"
              data-vq-profile-new=""
              title="Duplicate what's on screen as a new named profile"
              onClick={() => void newProfile()}
            >
              New profile
            </Btn>
          </FieldLine>
        </Row>
        <Row
          sub
          field
          hidden={!named}
          label="Name"
          htmlFor="vq-profile-name"
          help="Saved when you leave the field."
        >
          <TextField
            id="vq-profile-name"
            data-vq-profile-name=""
            placeholder="Profile name"
            key={`name-${rows}-${selectedId}`}
            defaultValue={named ? selected?.name || '' : ''}
            ref={(el: HTMLInputElement | null) => {
              nameField.current = el;
              nameRef(el);
            }}
          />
        </Row>
        {named ? (
          <Row
            sub
            label="Used for"
            htmlFor="vq-used-for"
            help="Members asking for a movie only see Movies and Both profiles; for a show, TV and Both."
          >
            <span data-vq-used-for="">
              <Segmented
                id="vq-used-for"
                label="Used for"
                itemAttr="data-vq-used-for-pick"
                value={selected?.media_type || 'both'}
                options={USED_FOR.map(([value, label]) => ({ value, label }))}
                onChange={(v) => void setUsedFor(v)}
              />
            </span>
          </Row>
        ) : null}
        <Row
          sub
          hidden={!named}
          label="Delete this profile"
          help="Titles using it go back to Default."
        >
          <Btn small kind="danger" data-vq-profile-delete="" onClick={() => void deleteProfile()}>
            Delete
          </Btn>
        </Row>
        <div data-vq-kind-defaults="">
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
          ).map(([kind, words, title]) => (
            <KindDefault
              key={kind}
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
      </Card>

      <SectionLabel
        title="Quality ladder"
        hint="Switch on what you will accept. First is best. Drag to reorder."
      />
      <Card>
        <div className={s.cardPad} id="vq-tier-rows" data-ui="">
          {tiers.length ? (
            <OrderList
              label={`Quality ladder for ${selected?.name || 'Default'}`}
              items={tiers.map((t) => ({
                id: t.key,
                name: tierName(t.key),
                mark: null,
                keepAction: true,
                action: (
                  <Switch
                    data-vq-tier-toggle={t.key}
                    aria-label={`Accept ${tierName(t.key)}`}
                    checked={!!t.enabled}
                    onChange={(event) => {
                      const enabled = event.target.checked;
                      change((cur) => ({
                        ...cur,
                        tiers: (cur.tiers || []).map((x) =>
                          x.key === t.key ? { ...x, enabled } : x,
                        ),
                      }));
                    }}
                  />
                ),
              }))}
              role={(id) =>
                tiers.find((t) => t.key === id)?.enabled
                  ? 'Accepted'
                  : 'Switched off: never grabbed'
              }
              dropText={(_id, index) => `Drop here: place ${index + 1}`}
              onChange={(next) =>
                change((cur) => {
                  const all = cur.tiers || [];
                  return {
                    ...cur,
                    tiers: next
                      .map((key) => all.find((t) => t.key === key))
                      .filter((t): t is Tier => !!t),
                  };
                })
              }
              minItems={tiers.length}
              removeLabel="Remove"
              keepText="Switch it off instead"
            />
          ) : (
            <p className={s.listEmpty}>{p ? 'This profile has no qualities.' : 'Loading...'}</p>
          )}
        </div>
        <Row
          label="Upgrade until"
          htmlFor="vq-cutoff"
          field
          help="Once you have this resolution or better, upgrades stop."
        >
          <Select
            id="vq-cutoff"
            value={p ? (p.cutoff_resolution == null ? '1080p' : p.cutoff_resolution) : '2160p'}
            onChange={(event) => {
              const v = event.target.value;
              change((cur) => ({ ...cur, cutoff_resolution: v }));
            }}
            options={[
              { value: '2160p', label: '4K · 2160p' },
              { value: '1080p', label: '1080p' },
              { value: '720p', label: '720p' },
              { value: '480p', label: '480p (SD)' },
              { value: '', label: 'Best available, never stop upgrading' },
            ]}
          />
        </Row>
        <Row label="Never grab" stack help="Hard rejects: skipped whatever else matches.">
          <Chips
            id="vq-rejects"
            label="Never grab"
            itemAttr="data-vq-reject"
            options={p ? REJECT_ORDER.map((k) => ({ key: k, label: REJECT_LABEL[k] || k })) : []}
            value={p?.rejects || []}
            onToggle={(k) => change((cur) => ({ ...cur, rejects: flip(cur.rejects, k) }))}
          />
        </Row>
      </Card>

      <SectionLabel
        title="Preferences"
        hint="Soft tie-breakers: they rank releases, they never reject on their own"
      />
      <Card>
        <Row label="Codec" htmlFor="vq-codec">
          <Segmented
            id="vq-codec"
            label="Codec"
            itemAttr="data-vq-codec"
            value={p?.prefer_codec ?? ''}
            options={[
              { value: 'any', label: 'Any' },
              { value: 'hevc', label: 'HEVC / x265' },
              { value: 'av1', label: 'AV1' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, prefer_codec: v }))}
          />
        </Row>
        <Row
          label="HDR"
          htmlFor="vq-hdr"
          help={p?.prefer_hdr === 'require' ? 'Require rejects a release without HDR.' : undefined}
        >
          <Segmented
            id="vq-hdr"
            label="HDR"
            itemAttr="data-vq-hdr"
            value={p?.prefer_hdr ?? ''}
            options={[
              { value: 'off', label: "Don't care" },
              { value: 'prefer', label: 'Prefer' },
              { value: 'require', label: 'Require' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, prefer_hdr: v }))}
          />
        </Row>
        <Row label="Audio" htmlFor="vq-audio">
          <Segmented
            id="vq-audio"
            label="Audio"
            itemAttr="data-vq-audio"
            value={p?.prefer_audio ?? ''}
            options={[
              { value: 'any', label: 'Any' },
              { value: 'surround', label: 'Surround' },
              { value: 'lossless', label: 'Lossless' },
              { value: 'atmos', label: 'Atmos' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, prefer_audio: v }))}
          />
        </Row>
        <Row label="Prefer REPACK / PROPER re-releases" htmlFor="vq-prefer-repack">
          <Switch
            id="vq-prefer-repack"
            checked={!!p?.prefer_repack}
            onChange={(event) => {
              const yes = event.target.checked;
              change((cur) => ({ ...cur, prefer_repack: yes }));
            }}
          />
        </Row>
        <Row
          label="Audio language"
          htmlFor="vq-lang-mode"
          help="For dubbed or MULTI releases. A release with no language tag is the original audio."
        >
          <Segmented
            id="vq-lang-mode"
            label="Audio language"
            itemAttr="data-vq-lang-mode"
            value={p ? langMode : ''}
            options={[
              { value: 'off', label: "Don't care" },
              { value: 'prefer', label: 'Prefer' },
              { value: 'require', label: 'Require' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, audio_language_mode: v }))}
          />
        </Row>
        <Row sub stack label="Languages" hidden={!p || langMode === 'off'}>
          <Chips
            id="vq-langs"
            label="Audio languages"
            itemAttr="data-vq-lang"
            options={p ? LANG_ORDER.map((k) => ({ key: k, label: LANG_LABEL[k] || k })) : []}
            value={langs}
            onToggle={(k) =>
              change((cur) => ({ ...cur, audio_languages: flip(cur.audio_languages, k) }))
            }
          />
        </Row>
        <Row
          label="Save to"
          htmlFor="vq-library"
          help="Which library new titles on this profile are filed in."
          more="The 4K library is used once its folder is switched on in Settings, Library, Folders; until then they go to the main one. Titles you already have stay where they are."
        >
          <Segmented
            id="vq-library"
            label="Save to"
            itemAttr="data-vq-library"
            value={p ? (p.library === '4k' ? '4k' : 'main') : ''}
            options={[
              { value: 'main', label: 'Main library' },
              { value: '4k', label: '4K library' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, library: v }))}
          />
        </Row>
      </Card>

      <SectionLabel title="Size and seeders" hint="Saved when you let go" />
      <Card>
        <Row
          label="Max size"
          help="Skip anything bigger. Set per type, so a movie and an episode are not judged the same."
        />
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
        <Row
          label="Preferred size"
          help="A tie-break only: among releases equal on quality and custom formats, the one closest to this size ranks first."
          more="Off at 0, and never past the max size."
        />
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
        <Seeders
          key={`sd-${rows}`}
          value={p ? seedersOf(p) : 1}
          commit={(v) => change((cur) => ({ ...cur, min_seeders: v }))}
        />
      </Card>

      <CustomFormats
        formats={formats}
        setFormats={setFormats}
        rows={rows}
        bump={() => setRows((n) => n + 1)}
        profile={p}
        change={change}
      />
    </div>
  );
}

function flip(list: string[] | undefined, k: string) {
  const arr = Array.isArray(list) ? [...list] : [];
  const i = arr.indexOf(k);
  if (i === -1) arr.push(k);
  else arr.splice(i, 1);
  return arr;
}

function KindDefault({
  kind,
  words,
  title,
  value,
  options,
  busy,
  pick,
}: {
  kind: string;
  words: string;
  title: string;
  value: string;
  options: Array<{ id: number; name: string }>;
  busy: boolean;
  pick: (raw: string, label: string) => void;
}) {
  return (
    <Row label={words} htmlFor={`vq-kind-default-${kind}`} field help={title}>
      <Select
        id={`vq-kind-default-${kind}`}
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
      </Select>
    </Row>
  );
}

function Seeders({ value, commit }: { value: number; commit: (v: number) => void }) {
  const [dragging, setDragging] = useState<string | null>(null);
  const ref = useCommit<HTMLInputElement>((el) => {
    setDragging(null);
    commit(parseInt(el.value, 10) || 0);
  });
  return (
    <Row
      label="Minimum seeders"
      htmlFor="vq-min-seeders"
      help="A torrent nobody is seeding never finishes. Set 0 to grab regardless."
      more="It parks on downloading metadata or stalls at 0% and quietly fills your client with dead entries. Usenet and Soulseek have no seeders and are never affected."
    >
      <span className={s.rangeLine}>
        <span className={s.rangeSay} id="vq-seeders-label">
          {seedersLabel(dragging ?? value)}
        </span>
        <input
          type="range"
          id="vq-min-seeders"
          className={s.range}
          min="0"
          max="100"
          step="1"
          ref={ref}
          value={dragging ?? value}
          onChange={(event) => setDragging(event.target.value)}
        />
      </span>
    </Row>
  );
}

// ── custom formats ──

const split = (str: string) =>
  str
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
    <>
      <SectionLabel
        title="Custom formats"
        hint="Score releases by their name: trusted groups, x265, freeleech, anything a pattern can see"
      />
      <Card data-custom-formats="">
        <Row
          label="How a format scores"
          help="A format matches when all its match terms hit and no never term does; matching formats add their score to a release's rank."
          more={
            <>
              Terms are plain text, or a regex written <code>/x265|hevc/</code>. A negative score
              buries a release without rejecting it. The "this profile" field overrides a format's
              score for the profile picked above. Formats are shared by every profile.
            </>
          }
        />
        <div className={s.cardPad} id="vq-format-rows" ref={host}>
          {formats.length ? (
            <>
              <div className={s.fmtHead} aria-hidden="true">
                <span>Name</span>
                <span>Match</span>
                <span>Never</span>
                <span>Score</span>
                <span>This profile</span>
                <span />
              </div>
              {formats.map((f) => {
                const ov = overrides[String(f.id)];
                return (
                  <div className={s.fmtRow} data-vq-fmt={f.id} key={`${f.id}-${rows}`}>
                    <TextField
                      full
                      data-vq-fmt-f="name"
                      aria-label="Format name"
                      defaultValue={f.name}
                      placeholder="Name"
                    />
                    <TextField
                      full
                      mono
                      data-vq-fmt-f="include"
                      aria-label={`${f.name}: match terms`}
                      defaultValue={(f.include || []).join(', ')}
                      placeholder="term, /regex/"
                    />
                    <TextField
                      full
                      mono
                      data-vq-fmt-f="exclude"
                      aria-label={`${f.name}: never terms`}
                      defaultValue={(f.exclude || []).join(', ')}
                      placeholder="term, /regex/"
                    />
                    <TextField
                      full
                      type="number"
                      data-vq-fmt-f="score"
                      aria-label={`${f.name}: score`}
                      defaultValue={f.score}
                      title="Default score"
                    />
                    <TextField
                      full
                      type="number"
                      data-vq-fmt-f="override"
                      aria-label={`${f.name}: score for this profile`}
                      defaultValue={ov == null ? '' : ov}
                      placeholder="Same"
                      title="Score for the selected profile (empty: the format's own)"
                    />
                    <Btn
                      kind="ghost"
                      small
                      iconOnly
                      icon="x"
                      data-vq-fmt-del={f.id}
                      aria-label={`Delete ${f.name}`}
                      title="Delete format"
                      onClick={() => void remove(f.id)}
                    />
                  </div>
                );
              })}
            </>
          ) : rows ? (
            <p className={s.listEmpty}>
              No custom formats yet: releases rank by the ladder and the tie-breakers alone.
            </p>
          ) : null}
          <div>
            <Btn small icon="plus" data-vq-format-add="" onClick={() => void add()}>
              Add format
            </Btn>
          </div>
        </div>
        <Row
          label="Minimum format score"
          htmlFor="vq-min-format-score"
          field
          help="For this profile: a release whose formats add up to less is rejected. 0 is off."
        >
          <NumberField
            id="vq-min-format-score"
            key={`min-${rows}`}
            defaultValue={(profile && profile.min_format_score) || 0}
            ref={minRef}
          />
        </Row>
      </Card>
    </>
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

/** Quality > YouTube: one simple profile for the channels you follow */
export function YoutubeQuality({ shown }: { shown: number }) {
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
    <div data-youtube-quality="">
      <Hero>{p ? youtubeSentence(p) : 'Loading the YouTube profile.'}</Hero>
      <SectionLabel
        title="Followed channels"
        hint="YouTube is grabbed with yt-dlp, so this is a ceiling, a codec and a container"
      />
      <Card>
        <Row
          label="Max resolution"
          htmlFor="yq-resolution"
          field
          help="The most yt-dlp will grab. It steps down to the next one available below this."
        >
          <Select
            id="yq-resolution"
            value={p ? p.max_resolution || '1080p' : 'best'}
            onChange={(event) => {
              const v = event.target.value;
              change((cur) => ({ ...cur, max_resolution: v }));
            }}
            options={[
              { value: 'best', label: 'Best available' },
              { value: '4320p', label: '8K · 4320p' },
              { value: '2160p', label: '4K · 2160p' },
              { value: '1440p', label: '1440p' },
              { value: '1080p', label: '1080p' },
              { value: '720p', label: '720p' },
              { value: '480p', label: '480p' },
              { value: '360p', label: '360p' },
            ]}
          />
        </Row>
        <Row label="Codec" htmlFor="yq-codec" help="A preference, not a rule.">
          <Segmented
            id="yq-codec"
            label="Codec"
            itemAttr="data-yq-codec"
            value={p?.video_codec ?? ''}
            options={[
              { value: 'any', label: 'Best' },
              { value: 'av1', label: 'AV1' },
              { value: 'vp9', label: 'VP9' },
              { value: 'h264', label: 'H.264' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, video_codec: v }))}
          />
        </Row>
        <Row label="Container" htmlFor="yq-container" help="The file it is saved as.">
          <Segmented
            id="yq-container"
            label="Container"
            itemAttr="data-yq-container"
            value={p?.container ?? ''}
            options={[
              { value: 'mp4', label: 'MP4' },
              { value: 'mkv', label: 'MKV' },
              { value: 'webm', label: 'WebM' },
            ]}
            onChange={(v) => change((cur) => ({ ...cur, container: v }))}
          />
        </Row>
        <Row label="Prefer 60fps when available" htmlFor="yq-60fps">
          <Switch
            id="yq-60fps"
            checked={!!p?.prefer_60fps}
            onChange={(event) => {
              const yes = event.target.checked;
              change((cur) => ({ ...cur, prefer_60fps: yes }));
            }}
          />
        </Row>
        <Row label="Allow HDR" htmlFor="yq-hdr" help="Off always grabs SDR.">
          <Switch
            id="yq-hdr"
            checked={!!p?.allow_hdr}
            onChange={(event) => {
              const yes = event.target.checked;
              change((cur) => ({ ...cur, allow_hdr: yes }));
            }}
          />
        </Row>
      </Card>
    </div>
  );
}
