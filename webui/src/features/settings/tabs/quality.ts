/**
 * What the Quality tab of Settings knows, apart from its markup
 * (SETTINGS_PORT.md, step 6). Ported from settings.js's quality profile
 * functions: the ranked target list, the presets, the profile a page edit is
 * saved to, and the one-line summary under each profile in the side panel.
 *
 * Everything on the tab belongs to a quality profile, not to config. The
 * profile shown is the save target: the active default unless a profile was
 * picked in the side panel. The server copies the default profile's values
 * into config itself (api/quality_profiles.py), so this tab never posts them
 * to /api/settings. The one plain setting here is verify_flac_decode.
 */

import type { SettingsPayload } from '../settings-store';

/** lossless formats take bit depth and sample rate, lossy a minimum bitrate */
export const RT_LOSSLESS_FORMATS = ['flac', 'alac', 'wav', 'dsf'];
export const RT_LOSSY_FORMATS = ['mp3', 'aac', 'ogg', 'opus', 'wma'];
/** a "group:" pick adds one target per format in it, at the end of the list */
export const RT_GROUPS: Record<string, string[]> = {
  'group:lossless': RT_LOSSLESS_FORMATS,
  'group:lossy': RT_LOSSY_FORMATS,
};

export const PRESET_LABELS: Record<string, string> = {
  audiophile: 'Audiophile',
  balanced: 'Balanced',
  space_saver: 'Space Saver',
};

export interface RankedTarget {
  format: string;
  label?: string;
  bit_depth?: number;
  min_sample_rate?: number;
  min_bitrate?: number;
}

/** the ladder: what populateQualityProfileUI put on the page */
export interface Ladder {
  preset: string;
  targets: RankedTarget[];
  fallback: boolean;
  searchMode: 'priority' | 'best_quality';
  rankCandidates: boolean;
  releaseImportMode: 'requested_tracks' | 'album_tracks';
  upgradePolicy: 'none' | 'acceptable' | 'until_cutoff';
  cutoff: number;
}

/** the eight controls a profile captures beyond the ladder */
export interface Extras {
  acoustidRequired: boolean;
  downsample: boolean;
  deepVerify: boolean;
  replaceLower: boolean;
  lossyEnabled: boolean;
  lossyCodec: string;
  lossyBitrate: string;
  lossyDeleteOriginal: boolean;
}

/** a profile as the server sends it (GET /api/quality-profile, /custom/<id>,
 *  /apply, the preset routes) */
export type ServerProfile = Record<string, unknown> & {
  id?: number;
  is_default?: boolean;
  preset?: string;
  ranked_targets?: unknown;
  upgrade_cutoff_index?: unknown;
};

/** a row of GET /api/quality-profile/custom: the table row, ranked_targets
 *  still a JSON string */
export interface ProfileRow {
  id: number;
  name: string;
  is_default?: boolean;
  ranked_targets?: string;
  acoustid_required?: boolean;
  deep_audio_verify?: boolean;
  downsample_enabled?: boolean;
  lossy_copy_enabled?: boolean;
  lossy_copy_codec?: string;
  lossy_copy_delete_original?: boolean;
  upgrade_policy?: string;
  upgrade_cutoff_index?: number | string;
}

/** settings.js rtLabel */
export function rtLabel(t: RankedTarget): string {
  const fmt = (t.format || 'any').toUpperCase();
  if (RT_LOSSLESS_FORMATS.includes(t.format)) {
    const bd = t.bit_depth ? `${t.bit_depth}-bit` : '';
    const sr = t.min_sample_rate ? `≥${t.min_sample_rate / 1000}kHz` : '';
    const detail = [bd, sr].filter(Boolean).join('/');
    return detail ? `${fmt} ${detail}` : fmt;
  }
  return t.min_bitrate ? `${fmt} ≥${t.min_bitrate}kbps` : fmt;
}

export function rtSelectionIsLossless(value: string): boolean {
  return value === 'group:lossless' || RT_LOSSLESS_FORMATS.includes(value);
}

/** the cutoff select only holds indexes of the list (renderUpgradeCutoffOptions) */
export function clampCutoff(index: number, targets: RankedTarget[]): number {
  if (!targets.length) return 0;
  const max = Math.max(0, targets.length - 1);
  return Number.isFinite(index) ? Math.min(Math.max(index, 0), max) : 0;
}

/** an empty page, as the markup was before a profile loaded */
export const EMPTY_LADDER: Ladder = {
  preset: 'balanced',
  targets: [],
  fallback: true,
  searchMode: 'priority',
  rankCandidates: false,
  releaseImportMode: 'requested_tracks',
  upgradePolicy: 'none',
  cutoff: 0,
};

/**
 * settings.js populateQualityProfileUI. `previous` is the page before it: a
 * profile with no cutoff keeps the one on screen, as the select did.
 */
export function ladderFrom(profile: ServerProfile, previous: Ladder): Ladder {
  const targets = Array.isArray(profile.ranked_targets)
    ? (profile.ranked_targets as RankedTarget[]).map((t) => ({ ...t }))
    : [];
  const policy = profile.upgrade_policy;
  const raw = profile.upgrade_cutoff_index;
  const index = raw === undefined || raw === null ? previous.cutoff : Number(raw);
  return {
    preset: typeof profile.preset === 'string' ? profile.preset : previous.preset,
    targets,
    fallback: profile.fallback_enabled !== false,
    searchMode: profile.search_mode === 'best_quality' ? 'best_quality' : 'priority',
    rankCandidates: profile.rank_candidates_by_quality === true,
    releaseImportMode:
      profile.release_import_mode === 'album_tracks' ? 'album_tracks' : 'requested_tracks',
    upgradePolicy:
      policy === 'until_cutoff' || policy === 'until_top'
        ? 'until_cutoff'
        : policy === 'acceptable'
          ? 'acceptable'
          : 'none',
    cutoff: clampCutoff(index, targets),
  };
}

/** the list changed: the cutoff select re-renders and keeps what it can */
export function withTargets(ladder: Ladder, targets: RankedTarget[]): Ladder {
  return { ...ladder, targets, cutoff: clampCutoff(ladder.cutoff, targets) };
}

/** Opus tops out at 256 kbps: 320 is greyed out and falls to 256 */
export function lossyBitrateFor(codec: string, bitrate: string): string {
  return codec === 'opus' && bitrate === '320' ? '256' : bitrate;
}

/** what loadSettingsData put in the eight controls from config, before the
 *  profile itself arrived */
function scalar(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

export function extrasFromConfig(settings: SettingsPayload): Extras {
  const codec = scalar(settings.lossy_copy?.codec) || 'mp3';
  return {
    acoustidRequired: settings.acoustid?.require_verified === true,
    downsample: settings.lossy_copy?.downsample_hires === true,
    deepVerify: settings.post_processing?.audio_completeness_check === true,
    replaceLower: settings.import?.replace_lower_quality === true,
    lossyEnabled: settings.lossy_copy?.enabled === true,
    lossyCodec: codec,
    lossyBitrate: lossyBitrateFor(codec, scalar(settings.lossy_copy?.bitrate) || '320'),
    lossyDeleteOriginal: settings.lossy_copy?.delete_original === true,
  };
}

/** settings.js applyFullQualityBundleToDom. A key the profile lacks keeps
 *  what the control showed (the legacy single-profile shape has none) */
export function extrasFrom(profile: ServerProfile, previous: Extras): Extras {
  const flag = (key: string, prev: boolean) => (key in profile ? profile[key] === true : prev);
  const text = (key: string, prev: string) =>
    typeof profile[key] === 'string' && profile[key] ? (profile[key] as string) : prev;
  return {
    acoustidRequired: flag('acoustid_required', previous.acoustidRequired),
    downsample: flag('downsample_enabled', previous.downsample),
    deepVerify: flag('deep_audio_verify', previous.deepVerify),
    replaceLower: flag('replace_lower_quality', previous.replaceLower),
    lossyEnabled: flag('lossy_copy_enabled', previous.lossyEnabled),
    lossyCodec: text('lossy_copy_codec', previous.lossyCodec),
    lossyBitrate: text('lossy_copy_bitrate', previous.lossyBitrate),
    lossyDeleteOriginal: flag('lossy_copy_delete_original', previous.lossyDeleteOriginal),
  };
}

/** settings.js collectQualityProfileFromUI: each target as small as it goes */
export function collectLadder(ladder: Ladder) {
  const ranked_targets = ladder.targets.map((t) => {
    const out: RankedTarget = { format: t.format };
    if (t.label) out.label = t.label;
    if (t.bit_depth) out.bit_depth = t.bit_depth;
    if (t.min_sample_rate) out.min_sample_rate = t.min_sample_rate;
    if (t.min_bitrate) out.min_bitrate = t.min_bitrate;
    return out;
  });
  return {
    version: 3,
    preset: ladder.preset || 'custom',
    fallback_enabled: ladder.fallback,
    search_mode: ladder.searchMode,
    rank_candidates_by_quality: ladder.rankCandidates,
    release_import_mode: ladder.releaseImportMode,
    upgrade_policy: ladder.upgradePolicy,
    upgrade_cutoff_index: clampCutoff(ladder.cutoff, ladder.targets),
    ranked_targets,
  };
}

/** settings.js collectFullQualityBundleFromUI: everything a profile saves */
export function collectBundle(ladder: Ladder, extras: Extras) {
  return {
    ...collectLadder(ladder),
    acoustid_required: extras.acoustidRequired,
    downsample_enabled: extras.downsample,
    deep_audio_verify: extras.deepVerify,
    replace_lower_quality: extras.replaceLower,
    lossy_copy_enabled: extras.lossyEnabled,
    lossy_copy_codec: extras.lossyCodec || 'mp3',
    lossy_copy_bitrate: extras.lossyBitrate || '320',
    lossy_copy_delete_original: extras.lossyDeleteOriginal,
  };
}

export type Bundle = ReturnType<typeof collectBundle>;

/** the "+ Add target" fields */
export interface AddFields {
  format: string;
  bitDepth: string;
  sampleRate: string;
  bitrate: string;
  custom: string;
}

export const EMPTY_ADD: AddFields = {
  format: 'group:lossless',
  bitDepth: '',
  sampleRate: '',
  bitrate: '320',
  custom: '320',
};

/** settings.js addRankedTarget: a group adds one target per format, and a
 *  target already in the list is not added twice */
export function addTargets(list: RankedTarget[], fields: AddFields): RankedTarget[] {
  const value = fields.format || 'flac';
  const constraints: Omit<RankedTarget, 'format'> = {};
  if (rtSelectionIsLossless(value)) {
    if (fields.bitDepth) constraints.bit_depth = parseInt(fields.bitDepth, 10);
    if (fields.sampleRate) constraints.min_sample_rate = parseInt(fields.sampleRate, 10);
  } else {
    const bitrate = fields.bitrate === 'custom' ? fields.custom : fields.bitrate;
    if (bitrate) constraints.min_bitrate = parseInt(bitrate, 10);
  }
  const sig = (t: RankedTarget) =>
    `${t.format}|${t.bit_depth || ''}|${t.min_sample_rate || ''}|${t.min_bitrate || ''}`;
  const existing = new Set(list.map(sig));
  const out = [...list];
  for (const format of RT_GROUPS[value] || [value]) {
    const target: RankedTarget = { format, ...constraints };
    if (existing.has(sig(target))) continue;
    target.label = rtLabel(target);
    out.push(target);
    existing.add(sig(target));
  }
  return out;
}

/** settings.js normalizeQualityProfileId: 0, blank or junk means "none" */
export function normalizeProfileId(id: unknown): number | null {
  if (id === null || id === undefined || id === '' || id === 0 || id === '0') return null;
  const n = parseInt(scalar(id), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** settings.js qpProfileSummary: what a profile does, under its name */
export function profileSummary(profile: ProfileRow): string {
  let targets: RankedTarget[] = [];
  try {
    targets = JSON.parse(profile.ranked_targets || '[]') as RankedTarget[];
  } catch {
    // unreadable reads as empty
  }
  const parts: string[] = [];
  if (targets.length) {
    const first = targets[0]?.label || 'Top target';
    parts.push(targets.length === 1 ? first : `${first} +${targets.length - 1} more`);
  } else {
    parts.push('Accepts anything');
  }
  if (profile.acoustid_required) parts.push('strict AcoustID');
  if (profile.deep_audio_verify) parts.push('deep verify');
  if (profile.downsample_enabled) parts.push('retain CD-quality (acquisition remembered)');
  if (profile.lossy_copy_enabled) {
    const codec = (profile.lossy_copy_codec || 'mp3').toUpperCase();
    parts.push(
      profile.lossy_copy_delete_original
        ? `retain ${codec} only (acquisition remembered)`
        : `lossless + ${codec} companion`,
    );
  }
  if (profile.upgrade_policy === 'acceptable') {
    parts.push('upgrade until any accepted target');
  } else if (profile.upgrade_policy === 'until_cutoff' || profile.upgrade_policy === 'until_top') {
    const index = Math.min(
      Math.max(parseInt(String(profile.upgrade_cutoff_index || '0'), 10) || 0, 0),
      Math.max(targets.length - 1, 0),
    );
    parts.push(`upgrade until ${targets[index]?.label || 'top target'}`);
  } else {
    parts.push('upgrades off');
  }
  return parts.join(' · ');
}

export function defaultProfileId(rows: ProfileRow[]): number | null {
  return rows.find((p) => p.is_default)?.id ?? null;
}

/** the store key a profile's pending save waits under. One per profile: an
 *  edit still goes to the profile it was made on after another is opened */
export const SAVE_KEY = 'quality.profile.';

type Json = Record<string, unknown> & { success?: boolean; error?: string };

/** fetch and read the body whatever the status: the routes put their error
 *  text in a 400 or 500 body, which the toasts show */
export async function call(url: string, init?: RequestInit): Promise<Json> {
  const response = await fetch(url, init);
  return (await response.json()) as Json;
}

export function postJson(url: string, body: unknown, method = 'POST'): Promise<Json> {
  return call(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
