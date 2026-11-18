/**
 * The shapes the people and permissions api sends (P0 contract,
 * scrap/permissions/API.md). Only what the editor reads.
 */

export type Kind =
  | 'bool'
  | 'restrict'
  | 'mode'
  | 'rating'
  | 'count'
  | 'set'
  | 'choice'
  | 'set_flag'
  | 'fixed'
  | 'admin'
  | 'presentation';

export type CategoryId =
  | 'general'
  | 'movies'
  | 'tv'
  | 'music'
  | 'books'
  | 'requests'
  | 'admin'
  | 'look';

export interface Category {
  id: CategoryId;
  label: string;
  help: string;
}

export interface CatalogueRow {
  id: string;
  kind: Kind;
  category: CategoryId;
  label: string;
  help: string;
  default: unknown;
  values: string[];
  value_labels: Record<string, string>;
  pages: string[];
  scope: string;
  group: string;
  also_in: CategoryId[];
  needs: string[];
  set_row: string | null;
  media: 'music' | 'movie' | 'tv' | null;
  presentation: boolean;
  stored: boolean;
  legacy: string | null;
}

export interface Catalogue {
  categories: Category[];
  rows: CatalogueRow[];
  ratings: string[];
  modes: string[];
  follow: string;
  all: string;
  quality_tokens: string[];
}

export type Count = { n: number; days: number };
export type IdList = Array<number | string>;
/** a stored permission value: bool, mode/rating string, count, '*' or ids, id or null */
export type Value = boolean | string | number | null | Count | IdList;
export type PermMap = Record<string, Value>;

export type Source = 'admin' | 'override' | 'template' | 'legacy' | 'floor';

export interface ProvenanceRow {
  id: string;
  value: Value;
  source: Source;
  template_value: Value;
  override: boolean;
  override_value: Value;
  ceiling: Value;
  capped: boolean;
  inert: boolean;
}

export interface Summary {
  sentence: string;
  lines: { category: string; text: string }[];
  never: string[];
}

export interface ProfilePermissions {
  success: boolean;
  profile: { id: number; name: string; is_admin: boolean };
  mode: 'admin' | 'template' | 'legacy';
  template: { id: number; name: string; slug: string | null } | null;
  overrides: PermMap;
  effective: PermMap;
  rows: ProvenanceRow[];
  changed_categories: string[];
  capped: string[];
  needs_look: string[];
  summary: Summary;
  /** PUT only: what the save changed */
  changes?: { id: string; before: Value; after: Value }[];
}

export interface Template {
  id: number;
  slug: string | null;
  name: string;
  description: string;
  builtin: boolean;
  sort: number;
  perms: PermMap;
  readable: boolean;
  followers: { id: number; name: string }[];
}

export interface QualityOption {
  id: number;
  name: string;
  media_type?: string | null;
  tags?: string[];
  /** what it fetches, "720p to 1080p" (video only) */
  range?: string;
  is_default?: boolean;
}

export interface QualityOptions {
  music: QualityOption[];
  movies: QualityOption[];
  tv: QualityOption[];
}

/** the bits of /api/profiles the header and General & account read */
export interface ProfileCard {
  id: number;
  name: string;
  avatar_color?: string | null;
  avatar_url?: string | null;
  is_admin?: boolean;
  has_pin?: boolean;
  has_password?: boolean;
  disabled?: boolean;
  home_page?: string | null;
  library_mode?: string | null;
  library_root?: string | null;
  /** how they sign in (/api/profiles, #2166) */
  signin?: 'oidc' | 'plex' | 'password' | 'pin' | 'picker';
}

/** /api/profiles/<id>/features (#2157, #2164) */
export interface FeaturesAdminPayload {
  features: { id: string; label: string; desc: string; group: string; pages: string[] }[];
  groups: { id: string; label: string }[];
  presets: { id: string; label: string; desc: string; off: string[] }[];
  off: string[];
  set_off?: string[];
  locked?: boolean;
  admin_set?: boolean;
  follows_default?: boolean;
  own_off?: string[] | null;
  lock_set?: boolean;
  /** what they follow when nobody set theirs: their template's Sidebar & look
   * (template names it), else what new profiles see */
  default?: { off: string[]; locked: boolean; template?: string; template_id?: number };
}

export interface SaveBody {
  /** the admin switch rides the same call (one atomic change, #2166) */
  is_admin?: boolean;
  template_id?: number;
  set: PermMap;
  reset: string[] | 'all';
}
