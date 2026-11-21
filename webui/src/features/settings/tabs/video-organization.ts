/**
 * The video side's organization settings (/api/video/organization), the
 * object video-settings.js loadOrganization filled the Organization card's
 * Video panel from and saveOrganization posted back whole (SETTINGS_PORT.md,
 * video pass V2b): the path templates and their previews, the post-processing
 * switches, the subtitles (provider chain, Whisper, the advanced knobs) and
 * the Missing Subtitles job's status line.
 *
 * It loads once a page, the first time the video settings page or the Library
 * tab is shown, as loadSharedLibrary did; a failed load can be retried. The
 * job status loads each time the video settings page is shown, as it did.
 */

import { useSyncExternalStore } from 'react';

import { videoShown } from './video-shown';

export const ORG_URL = '/api/video/organization';
export const WHISPER_URL = '/api/video/subtitles/whisper';
export const MISSING_SUBS_JOB = 'missing_subtitles';

export type Org = Record<string, unknown>;
export type Form = Record<string, string | boolean>;
export type LoadState = 'idle' | 'loading' | 'loaded' | 'failed';

export interface WhisperStatus {
  enabled?: boolean;
  downloaded?: boolean;
  engine?: boolean;
  engine_hint?: string;
  model?: string;
  disk_model?: string;
  size_bytes?: number;
  downloaded_at?: string;
  models?: Record<string, { label: string; blurb: string }>;
  download?: {
    state?: string;
    error?: string;
    model?: string;
    total_bytes?: number;
    downloaded_bytes?: number;
  };
}

export interface Job {
  job_id: string;
  enabled?: boolean;
  interval_hours?: number;
  settings?: { dry_run?: boolean };
  last_run?: { finished_at?: string; auto_fixed?: number } | null;
  pending_findings_count?: number;
}

interface State {
  org: Org | null;
  form: Form;
  /** the subtitle chain: network providers, top first */
  order: string[];
  status: LoadState;
  loads: number;
  whisper: WhisperStatus | null;
  /** a failed download, or one that could not start */
  whisperError: string;
  langsError: string;
  /** null: no status yet (or none to show) */
  job: Job | null;
}

// ── the template previews: mirrors of core/video/organization ──

// sample values for the live preview (what the importer feeds the engine)
export const MOVIE_EG = {
  title: 'The Matrix',
  titlefirst: 'T',
  year: '1999',
  quality: 'Bluray-1080p',
  resolution: '1080p',
  source: 'Bluray',
  codec: 'HEVC',
  edition: '',
  audiolangs: 'EN',
  sublangs: 'EN+ES',
  tmdbid: '603',
  imdbid: 'tt0133093',
};
// a title with a colon, so the colon setting shows in the preview
export const EPISODE_EG = {
  series: 'Dexter: Resurrection',
  season: '01',
  seasonraw: '1',
  episode: '01',
  episodetitle: 'A Beating Heart',
  year: '2025',
  quality: 'WEBDL-1080p',
  resolution: '1080p',
  source: 'WEBDL',
  codec: 'H264',
  audiolangs: 'EN',
  sublangs: 'EN',
  tvdbid: '448003',
  tmdbid: '259909',
  imdbid: 'tt31846306',
};
export const YOUTUBE_EG = {
  channel: 'Veritasium',
  title: 'How Electricity Actually Works',
  year: '2024',
  date: '2024-03-15',
  month: '03',
  day: '15',
  sxe: 's2024e0315',
  videoid: 'oI_X2cMHNe0',
};

/** core/video/organization.replace_colons */
export function replaceColons(v: string, mode: string) {
  const s = v;
  if (!s.includes(':')) return s;
  if (mode === 'dash') return s.split(':').join('-');
  if (mode === 'space_dash_space') return s.replace(/\s*:\s*/g, ' - ');
  if (mode === 'smart')
    return s
      .replace(/\s*:(?:\s+|$)/g, ' - ')
      .split(':')
      .join('-');
  return s;
}
function sanitize(v: string) {
  return (
    v
      // oxlint-disable-next-line no-control-regex
      .replace(/[\\/:*?"<>|\x00-\x1f]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/[ .]+$/, '')
  );
}
/** core/video/organization.render_template + _tidy_component, kept in
 *  lockstep, so the preview matches what lands on disk */
export function renderTemplate(tmpl: string, vals: Record<string, string>, colons: string) {
  const clean = (v: string) => sanitize(replaceColons(v, colons));
  const keys = Object.keys(vals).sort((a, b) => b.length - a.length);
  let out = String(tmpl || '');
  for (const k of keys) out = out.split('${' + k + '}').join(clean(vals[k]));
  for (const k of keys) out = out.split('$' + k).join(clean(vals[k]));
  return out
    .split('/')
    .map((p) =>
      p
        .replace(/\{\s*(?:[A-Za-z]+-)?\s*\}/g, '') // '{imdb-}' / '{}' from an empty id
        .replace(/\s+-\s+(?=(\s|$))/g, ' ')
        .replace(/\(\s*\)/g, '')
        .replace(/\[\s*\]/g, '')
        .replace(/\((\d{4})\)(?:\s*\(\d{4}\))+/g, '($1)')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/^-+|-+$/g, '')
        .trim()
        .replace(/[ .]+$/, ''),
    )
    .filter((p) => p !== '')
    .join('/');
}

// ── the subtitle chain ──

export const SUBCHAIN_NETWORK_IDS = ['opensubtitles', 'subdl', 'subsource', 'embedded'];
/** pinned last by the backend, never reorderable */
export const SUBCHAIN_BACKGROUND_IDS = ['whisper'];
export const SUBCHAIN_META: Record<string, { name: string; emoji: string }> = {
  opensubtitles: { name: 'OpenSubtitles', emoji: '\u{1F4AC}' },
  subdl: { name: 'SubDL', emoji: '\u{1F4E5}' },
  subsource: { name: 'SubSource', emoji: '\u{1F310}' },
  embedded: { name: 'Embedded', emoji: '\u{1F4BD}' },
  whisper: { name: 'Whisper (local)', emoji: '\u{1F399}' },
};
/** unknown ids show as themselves: a provider newer than this list */
export const subchainMeta = (id: string) =>
  SUBCHAIN_META[id] || { name: String(id), emoji: '\u{1F4AC}' };

/** the stored order (a JSON list of provider ids), OpenSubtitles when it is
 *  garbage or missing, without the background-only providers */
export function providerOrder(raw: unknown) {
  let value = raw;
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value);
    } catch {
      value = null;
    }
  }
  const list: string[] = [];
  if (Array.isArray(value))
    for (const p of value) {
      const id = String(p ?? '');
      if (id && !list.includes(id)) list.push(id);
    }
  return (list.length ? list : ['opensubtitles']).filter(
    (id) => !SUBCHAIN_BACKGROUND_IDS.includes(id),
  );
}

// ── the form ──

/** a stored value as the field shows it (an object is not one) */
const scalar = (v: unknown) =>
  typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : '';
const text = (v: unknown, fallback = '') => scalar(v) || fallback;
const or = (v: unknown, fallback: number) => (v == null ? String(fallback) : scalar(v));

/** fillOrg: the fields from the stored settings, with the page's defaults */
export function fillForm(o: Org): Form {
  return {
    movie_template: text(o.movie_template),
    episode_template: text(o.episode_template),
    youtube_template: text(o.youtube_template),
    transfer_mode: text(o.transfer_mode, 'copy'),
    colon_replacement: text(o.colon_replacement, 'delete'),
    verify_with_ffprobe: !!o.verify_with_ffprobe,
    replace_existing: !!o.replace_existing,
    carry_subtitles: !!o.carry_subtitles,
    save_artwork: !!o.save_artwork,
    write_nfo: !!o.write_nfo,
    download_subtitles: !!o.download_subtitles,
    subtitle_langs: text(o.subtitle_langs, 'en'),
    subtitle_min_score: or(o.subtitle_min_score, 24),
    subtitle_daily_quota: or(o.subtitle_daily_quota, 20),
    subtitle_upgrade_min_delta: or(o.subtitle_upgrade_min_delta, 15),
    subtitle_worker_interval_min: or(o.subtitle_worker_interval_min, 30),
    subtitle_worker_batch: or(o.subtitle_worker_batch, 10),
    subtitle_hi_forced: text(o.subtitle_hi_forced, 'off'),
    subtitle_forced: !!o.subtitle_forced,
    subtitle_use_embedded: o.subtitle_use_embedded !== false,
    subtitle_ignore_pgs: !!o.subtitle_ignore_pgs,
    subtitle_ignore_vobsub: !!o.subtitle_ignore_vobsub,
    subtitle_ignore_ass: !!o.subtitle_ignore_ass,
    subtitle_autosync: !!o.subtitle_autosync,
    whisper_model: text(o.whisper_model, 'small'),
    whisper_enabled: !!o.whisper_enabled,
    recycle_deletes: !!o.recycle_deletes,
    recycle_keep_days: text(o.recycle_keep_days || '', '7'),
    recycle_path: text(o.recycle_path),
    min_free_disk_gb: text(o.min_free_disk_gb || '', '0'),
    youtube_follow_count: or(o.youtube_follow_count, 5),
    youtube_sponsorblock: text(o.youtube_sponsorblock, 'off'),
    youtube_embed_subs: !!o.youtube_embed_subs,
  };
}

const EMPTY_FORM: Form = {
  movie_template: '',
  episode_template: '',
  youtube_template: '',
  transfer_mode: 'copy',
  colon_replacement: 'delete',
  verify_with_ffprobe: false,
  replace_existing: false,
  carry_subtitles: false,
  save_artwork: false,
  write_nfo: false,
  download_subtitles: false,
  subtitle_langs: '',
  subtitle_min_score: '',
  subtitle_daily_quota: '',
  subtitle_upgrade_min_delta: '',
  subtitle_worker_interval_min: '',
  subtitle_worker_batch: '',
  subtitle_hi_forced: 'off',
  subtitle_forced: false,
  subtitle_use_embedded: false,
  subtitle_ignore_pgs: false,
  subtitle_ignore_vobsub: false,
  subtitle_ignore_ass: false,
  subtitle_autosync: false,
  whisper_model: 'tiny',
  whisper_enabled: false,
  recycle_deletes: false,
  recycle_keep_days: '',
  recycle_path: '',
  min_free_disk_gb: '',
  youtube_follow_count: '',
  youtube_sponsorblock: 'off',
  youtube_embed_subs: false,
};

/** the backend normalize() bounds, so nothing posted is clamped to something else */
export function clampNum(v: unknown, lo: number, hi: number, fb: number) {
  const n = parseFloat(String(v));
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fb;
}
export function clampInt(v: unknown, lo: number, hi: number, fb: number) {
  const n = parseInt(String(v), 10);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fb;
}

/** the download state the blob echoes back: it is replaced whole on save, so
 *  these round-trip or a save wipes the "downloaded" state */
export interface Echo {
  whisper_model_downloaded: boolean;
  whisper_model_path: string;
  whisper_model_size_bytes: number;
  whisper_model_downloaded_at: string;
}

/** collectOrg */
export function orgBody(f: Form, order: string[], echo: Echo) {
  const s = (k: string) => String(f[k] ?? '');
  const b = (k: string) => !!f[k];
  return {
    movie_template: s('movie_template'),
    episode_template: s('episode_template'),
    youtube_template: s('youtube_template'),
    transfer_mode: s('transfer_mode'),
    colon_replacement: s('colon_replacement') || 'delete',
    verify_with_ffprobe: b('verify_with_ffprobe'),
    replace_existing: b('replace_existing'),
    carry_subtitles: b('carry_subtitles'),
    save_artwork: b('save_artwork'),
    write_nfo: b('write_nfo'),
    download_subtitles: b('download_subtitles'),
    subtitle_langs: s('subtitle_langs'),
    // the editable chain; the backend normalises it (parse_provider_order)
    subtitle_provider_order: order.slice(),
    subtitle_min_score: clampNum(f.subtitle_min_score, 0, 200, 24),
    subtitle_daily_quota: clampInt(f.subtitle_daily_quota, 1, 1000, 20),
    subtitle_upgrade_min_delta: clampNum(f.subtitle_upgrade_min_delta, 0, 200, 15),
    subtitle_worker_interval_min: clampInt(f.subtitle_worker_interval_min, 5, 1440, 30),
    subtitle_worker_batch: clampInt(f.subtitle_worker_batch, 1, 200, 10),
    subtitle_hi_forced: s('subtitle_hi_forced') || 'off',
    subtitle_forced: b('subtitle_forced'),
    subtitle_use_embedded: b('subtitle_use_embedded'),
    subtitle_ignore_pgs: b('subtitle_ignore_pgs'),
    subtitle_ignore_vobsub: b('subtitle_ignore_vobsub'),
    subtitle_ignore_ass: b('subtitle_ignore_ass'),
    subtitle_autosync: b('subtitle_autosync'),
    whisper_enabled: b('whisper_enabled'),
    whisper_model: s('whisper_model'),
    ...echo,
    recycle_deletes: b('recycle_deletes'),
    recycle_keep_days: s('recycle_keep_days'),
    recycle_path: s('recycle_path'),
    min_free_disk_gb: s('min_free_disk_gb'),
    youtube_follow_count: s('youtube_follow_count'),
    youtube_sponsorblock: s('youtube_sponsorblock'),
    youtube_embed_subs: b('youtube_embed_subs'),
  };
}

/** what Reset to Defaults posts: the backend fills the Radarr/Sonarr layout */
export const RESET_BODY = {
  movie_template: '',
  episode_template: '',
  youtube_template: '',
  transfer_mode: 'copy',
  verify_with_ffprobe: true,
  replace_existing: true,
  carry_subtitles: true,
  save_artwork: false,
  write_nfo: false,
  download_subtitles: false,
  subtitle_langs: 'en',
  recycle_deletes: true,
  recycle_keep_days: 7,
  recycle_path: '',
  youtube_sponsorblock: 'off',
  youtube_embed_subs: false,
  min_free_disk_gb: 0,
  youtube_follow_count: 5,
};

/** the languages field: 2-3 letter codes, the separators parse_langs takes;
 *  the first one that is not, or null */
export function badLanguage(value: string) {
  const toks = String(value || '')
    .split(/[,\s;]+/)
    .filter(Boolean);
  return toks.find((t) => !/^[A-Za-z]{2,3}$/.test(t)) ?? null;
}

export function whisperBytes(n: unknown) {
  const v = Number(n) || 0;
  if (v >= 1e9) return (v / 1e9).toFixed(1) + ' GB';
  if (v >= 1e6) return Math.round(v / 1e6) + ' MB';
  if (v >= 1e3) return Math.round(v / 1e3) + ' KB';
  return v + ' B';
}

/** renderMissingSubsJob: the job's status line */
export function jobLine(job: Job | null) {
  if (!job) return '';
  const parts = [job.enabled ? 'On' : 'Off'];
  if (job.enabled) {
    const h = job.interval_hours || 168;
    parts.push(h % 24 === 0 ? `every ${h / 24} day${h === 24 ? '' : 's'}` : `every ${h}h`);
  }
  parts.push(job.settings?.dry_run ? 'dry run: findings only' : 'queues what’s missing');
  const last = job.last_run;
  if (last && last.finished_at)
    parts.push(
      `last run ${String(last.finished_at).slice(0, 10)}${last.auto_fixed ? `, queued ${last.auto_fixed}` : ''}`,
    );
  else parts.push('never run');
  if (job.pending_findings_count) parts.push(`${job.pending_findings_count} to review`);
  return parts.join(' · ');
}

const toast = (message: string, type: string) =>
  (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(message, type);

function createVideoOrganization() {
  let state: State = {
    org: null,
    form: EMPTY_FORM,
    order: [],
    status: 'idle',
    loads: 0,
    whisper: null,
    whisperError: '',
    langsError: '',
    job: null,
  };
  let echo: Echo = {
    whisper_model_downloaded: false,
    whisper_model_path: '',
    whisper_model_size_bytes: 0,
    whisper_model_downloaded_at: '',
  };
  let langsGood = 'en';
  let loading: Promise<boolean> | null = null;
  let poll: ReturnType<typeof setInterval> | null = null;
  const listeners = new Set<() => void>();
  const set = (next: Partial<State>) => {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  };

  /** fillOrg, and the Whisper status it asked for */
  function fill(o: Org) {
    langsGood = text(o.subtitle_langs, 'en');
    echo = {
      whisper_model_downloaded: !!o.whisper_model_downloaded,
      whisper_model_path: text(o.whisper_model_path),
      whisper_model_size_bytes: Number(o.whisper_model_size_bytes) || 0,
      whisper_model_downloaded_at: text(o.whisper_model_downloaded_at),
    };
    set({
      org: o,
      form: fillForm(o),
      order: providerOrder(o.subtitle_provider_order),
      loads: state.loads + 1,
    });
    void loadWhisper();
  }

  async function load(): Promise<boolean> {
    if (state.status === 'loaded') return true;
    if (loading) return loading;
    set({ status: 'loading' });
    loading = (async () => {
      try {
        const r = await fetch(ORG_URL, { headers: { Accept: 'application/json' } });
        const d = r.ok ? ((await r.json()) as Org | null) : null;
        if (!d) {
          set({ status: 'failed' });
          return false;
        }
        fill(d);
        set({ status: 'loaded' });
        return true;
      } catch {
        set({ status: 'failed' });
        return false;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  async function save(silent = false): Promise<void> {
    try {
      const r = await fetch(ORG_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(orgBody(state.form, state.order, echo)),
      });
      const d = r.ok ? ((await r.json()) as Org | null) : null;
      if (d) set({ org: d });
      if (!silent) toast('Library organization saved', 'success');
    } catch {
      // ignore
    }
  }

  function stopPoll() {
    if (poll) {
      clearInterval(poll);
      poll = null;
    }
  }

  async function loadWhisper(): Promise<boolean> {
    try {
      const r = await fetch(`${WHISPER_URL}/status`, { headers: { Accept: 'application/json' } });
      const d = r.ok ? ((await r.json()) as (WhisperStatus & { error?: string }) | null) : null;
      if (d && !d.error) {
        // a download may have finished in another tab, or just now
        echo = {
          ...echo,
          whisper_model_downloaded: !!d.downloaded,
          whisper_model_size_bytes: d.size_bytes || 0,
          whisper_model_downloaded_at: d.downloaded_at || '',
        };
        const dl = d.download || {};
        set({
          whisper: d,
          whisperError: dl.state === 'error' && dl.error ? `Download failed: ${dl.error}` : '',
        });
        if (dl.state === 'downloading') {
          if (!poll) poll = setInterval(() => void loadWhisper(), 2000);
        } else stopPoll();
      }
      return !!d;
    } catch {
      return false;
    }
  }

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    get: () => state,
    load,
    save,
    loadWhisper,
    /** a field changed, without saving (still being typed in) */
    edit: (values: Form) => set({ form: { ...state.form, ...values } }),
    /** a field changed, and the whole of it saved */
    change: (values: Form) => {
      set({ form: { ...state.form, ...values } });
      return save();
    },
    /** the languages field left: saved when every code is one, else put back */
    commitLangs: () => {
      const bad = badLanguage(String(state.form.subtitle_langs));
      if (bad !== null) {
        set({
          langsError: `Not a language code: "${bad}". Use 2-3 letters per code (e.g. en, es).`,
          form: { ...state.form, subtitle_langs: langsGood },
        });
        return;
      }
      langsGood = String(state.form.subtitle_langs);
      set({ langsError: '' });
      void save();
    },
    /** the chain changed: saved at once */
    setOrder: (order: string[]) => {
      set({ order });
      void save();
    },
    /** the Whisper switch: saved, then the status again (it pins or unpins
     *  the chain's last step) */
    toggleWhisper: async (on: boolean) => {
      set({ form: { ...state.form, whisper_enabled: on } });
      await save();
      await loadWhisper();
    },
    /** the picked model saved first, so the download names it */
    download: async () => {
      const model = String(state.form.whisper_model || 'small');
      set({ whisperError: '' });
      try {
        await save(true);
        const r = await fetch(`${WHISPER_URL}/download`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ model }),
        });
        const body = ((await r.json()) || {}) as { error?: string };
        if (!r.ok) {
          set({ whisperError: `Could not start download: ${body.error || r.status}` });
          return;
        }
        void loadWhisper();
      } catch {
        set({ whisperError: 'Could not start download (network error).' });
      }
    },
    reset: async () => {
      try {
        const r = await fetch(ORG_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(RESET_BODY),
        });
        const d = r.ok ? ((await r.json()) as Org | null) : null;
        if (d) {
          fill(d);
          toast('Reset to the standard layout', 'success');
        }
      } catch {
        // the page let this one go unhandled
      }
    },
    loadJob: async () => {
      try {
        const r = await fetch('/api/video/repair/jobs', {
          headers: { Accept: 'application/json' },
        });
        const d = r.ok ? ((await r.json()) as { jobs?: Job[] } | null) : null;
        const jobs = (d && d.jobs) || [];
        set({ job: jobs.find((j) => j.job_id === MISSING_SUBS_JOB) || null });
      } catch {
        // the status is best-effort
      }
    },
    /** for tests */
    resetState: () => {
      stopPoll();
      state = {
        org: null,
        form: EMPTY_FORM,
        order: [],
        status: 'idle',
        loads: 0,
        whisper: null,
        whisperError: '',
        langsError: '',
        job: null,
      };
      loading = null;
      langsGood = 'en';
    },
  };
}

export const videoOrganization = createVideoOrganization();
export type VideoOrganization = typeof videoOrganization;

export function useVideoOrganization() {
  return useSyncExternalStore(videoOrganization.subscribe, videoOrganization.get);
}

// loads the first time the video settings page or the Library tab is shown
// (loadSharedLibrary's two triggers); the job's status each time the video
// settings page is
if (typeof document !== 'undefined') {
  let seen = videoShown.get();
  videoShown.subscribe(() => {
    if (videoShown.get() === seen) return;
    seen = videoShown.get();
    void videoOrganization.load();
    void videoOrganization.loadJob();
  });
  // the Missing Subtitles job's line too: it was blank when Library opened
  // from the music side (settings-bic AUDIT 36)
  document.addEventListener('soulsync:library-settings-shown', () => {
    void videoOrganization.load();
    void videoOrganization.loadJob();
  });
}
