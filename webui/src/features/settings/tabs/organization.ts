/**
 * Library > Organization rules (SETTINGS_PORT.md, step 8b): what the naming
 * fields show on load, the check that holds a save back
 * (validateFileOrganizationTemplates), the card summary and the path
 * preview's request.
 */

import type { SettingsPayload } from '../settings-store';

type Section = Record<string, unknown>;

/** what a field shows when the saved template is empty (loadSettingsData) */
export const TEMPLATE_DEFAULTS = {
  album_path: '$albumartist/$albumartist - $album/$track - $title',
  compilation_path: 'Compilations/$album/$track - $artist - $title',
  single_path: '$albumartist/$albumartist - $title/$title',
  playlist_path: '$playlist/$artist - $title',
  playlist_item: '',
  video_path: '$artist/$title-video',
  podcast_path: '$show/Season $season/$title',
  sample_path: '$artist/$track - $chop',
} as const;

export type TemplateKey = keyof typeof TEMPLATE_DEFAULTS;

/** what Reset to Defaults writes (resetFileOrganizationTemplates). It wrote
 *  the old $artist single default, which the server and the next load both
 *  upgrade to this one, so it writes this one. The compilation template and
 *  playlist file naming are left as they are, as before. */
export const RESET_TEMPLATES = {
  album_path: '$albumartist/$albumartist - $album/$track - $title',
  single_path: '$albumartist/$albumartist - $title/$title',
  playlist_path: '$playlist/$artist - $title',
  video_path: '$artist/$title-video',
  podcast_path: '$show/Season $season/$title',
  sample_path: '$artist/$track - $chop',
} as const;
export const RESET_AUDIOBOOK_TEMPLATE = '$author/$series/$seriespos - $title';

const str = (value: unknown) =>
  typeof value === 'string' ? value : typeof value === 'number' ? String(value) : '';

/** the templates as the fields show them */
export function templatesOf(settings: SettingsPayload): Record<TemplateKey, string> {
  const saved = ((settings.file_organization || {}) as Section).templates as Section | undefined;
  const out = {} as Record<TemplateKey, string>;
  for (const key of Object.keys(TEMPLATE_DEFAULTS) as TemplateKey[]) {
    out[key] = str(saved?.[key]) || TEMPLATE_DEFAULTS[key];
  }
  // $albumartist honors the Collaborative Album Artist mode; the old $artist
  // default filed multi-artist singles under "A, B & C". A stored old default
  // upgrades server-side too (core/imports/paths.py)
  if (str(saved?.single_path) === '$artist/$artist - $title/$title') {
    out.single_path = TEMPLATE_DEFAULTS.single_path;
  }
  return out;
}

const VALID_VARS = {
  album: [
    '$artist',
    '$albumartist',
    '$artistletter',
    '$albumartistletter',
    '$album',
    '$albumtype',
    '$atypes',
    '$title',
    '$track',
    '$disc',
    '$discnum',
    '$discalways',
    '$discnumalways',
    '$cdnum',
    '$year',
    '$quality',
    '$disambiguation',
    '$label',
    '$explicit',
    '$contentrating',
    '$contentratingfull',
    '$albumartistdisambiguation',
    '$albumartistcountry',
    '$albumartistgenre',
    '$albumartistinfo',
    '$albumartistcomposite',
    '$albumartistclash',
    '$artistdisambiguation',
    '$artistcountry',
    '$artistgenre',
    '$artistinfo',
    '$artistcomposite',
    '$artistclash',
  ],
  single: [
    '$artist',
    '$albumartist',
    '$artistletter',
    '$albumartistletter',
    '$album',
    '$albumtype',
    '$atypes',
    '$title',
    '$track',
    '$year',
    '$quality',
    '$explicit',
    '$contentrating',
    '$contentratingfull',
    '$albumartistdisambiguation',
    '$albumartistcountry',
    '$albumartistgenre',
    '$albumartistinfo',
    '$albumartistcomposite',
    '$albumartistclash',
    '$artistdisambiguation',
    '$artistcountry',
    '$artistgenre',
    '$artistinfo',
    '$artistcomposite',
    '$artistclash',
  ],
  playlist: ['$artist', '$artistletter', '$playlist', '$title', '$year', '$quality'],
  podcast: [
    '$show',
    '$podcast',
    '$author',
    '$artist',
    '$title',
    '$season',
    '$seasonnum',
    '$episode',
    '$episodenum',
    '$year',
    '$date',
    '$type',
  ],
  sample: ['$artist', '$track', '$album', '$chop', '$stem'],
} as const;

/** one template's problems, in validateFileOrganizationTemplates' words */
function check(
  name: keyof typeof VALID_VARS,
  value: string,
  { needsFolder }: { needsFolder: boolean },
): string[] {
  const errors: string[] = [];
  const path = value.trim();
  if (!path) return errors;
  const Name = name[0].toUpperCase() + name.slice(1);
  if (path.endsWith('/')) errors.push(`${Name} template cannot end with /`);
  if (path.startsWith('/')) errors.push(`${Name} template cannot start with /`);
  if (needsFolder && !path.includes('/')) {
    errors.push(`${Name} template must include at least one folder (use / separator)`);
  }
  if (path.includes('//')) errors.push(`${Name} template cannot have consecutive slashes //`);
  const valid: readonly string[] = VALID_VARS[name];
  for (const v of path.match(/\$\{([a-zA-Z]+)\}|\$([a-zA-Z]+)/g) || []) {
    // ${var} is checked as $var; case is checked separately
    const normalized = v.startsWith('${') ? '$' + v.slice(2, -1) : v;
    const lower = normalized.toLowerCase();
    if (!valid.some((ok) => ok.toLowerCase() === lower)) {
      errors.push(
        `Invalid variable "${normalized}" in ${name} template. Valid: ${valid.join(', ')}`,
      );
    } else if (normalized !== lower && valid.includes(lower)) {
      errors.push(`Variable "${normalized}" should be lowercase: "${lower}"`);
    }
  }
  return errors;
}

/** validateFileOrganizationTemplates: the save is refused while any is set */
export function templateErrors(settings: SettingsPayload): string[] {
  const t = templatesOf(settings);
  return [
    ...check('album', t.album_path, { needsFolder: true }),
    // a single may be a flat file ("$artist - $title")
    ...check('single', t.single_path, { needsFolder: false }),
    ...check('playlist', t.playlist_path, { needsFolder: true }),
    ...check('podcast', t.podcast_path, { needsFolder: false }),
    ...check('sample', t.sample_path, { needsFolder: false }),
  ];
}

/** LIBRARY_SUMMARIES['library-music-org'] */
export function musicOrgSummary(enabled: boolean, album: string): string {
  if (!enabled) return 'organizing off';
  const tpl = album.trim();
  // the template is what is being decided here, but it is long: its shape
  return tpl ? tpl.split('/').slice(-2).join('/') : 'organizing on';
}

/** the preview's fields, by the key the server answers under */
export const PREVIEW_FIELDS = [
  ['template-album-path', 'album_path'],
  ['template-compilation-path', 'compilation_path'],
  ['template-single-path', 'single_path'],
  ['template-playlist-item', 'playlist_item'],
  ['template-video-path', 'video_path'],
  ['template-sample-path', 'sample_path'],
  ['disc-label', 'disc_label'],
] as const;

export const PREVIEW_SCENARIOS = [
  ['standard', 'Standard'],
  ['explicit', 'Explicit'],
  ['multidisc', 'Multi-disc'],
  ['samename', 'Shared artist name'],
] as const;
export type Scenario = (typeof PREVIEW_SCENARIOS)[number][0];
export const SCENARIO_KEY = 'soulsync.pathPreviewScenario';

/** the scenario picked last time, standard when none or storage is blocked */
export function savedScenario(): Scenario {
  try {
    const saved = localStorage.getItem(SCENARIO_KEY);
    const hit = PREVIEW_SCENARIOS.find(([key]) => key === saved);
    if (hit) return hit[0];
  } catch {
    // storage blocked, standard it is
  }
  return 'standard';
}

/** what /api/settings/path-preview is asked (_fetchPathPreview) */
export function previewBody(settings: SettingsPayload, scenario: Scenario) {
  const t = templatesOf(settings);
  const org = (settings.file_organization || {}) as Section;
  return {
    scenario,
    templates: {
      album_path: t.album_path,
      compilation_path: t.compilation_path,
      single_path: t.single_path,
      playlist_item: t.playlist_item,
      video_path: t.video_path,
      sample_path: t.sample_path,
    },
    settings: {
      auto_disambiguation: org.auto_disambiguation !== false,
      collab_artist_mode: str(org.collab_artist_mode) || 'first',
      artistletter_symbol_fallback: org.artistletter_symbol_fallback === true,
      unicode_substitution: org.unicode_substitution === true,
      disc_label: str(org.disc_label) || 'Disc',
    },
  };
}
