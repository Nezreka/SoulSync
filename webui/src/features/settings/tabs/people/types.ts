/**
 * The shapes P0's people and permissions API sends (scrap/permissions/API.md,
 * api/permissions_routes.py), plus the profile, invite and audit rows the
 * Settings > People tab reads from the older profile routes.
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

export type Mode = 'none' | 'ask' | 'download';
export interface Count {
  n: number;
  days: number;
}
/** every stored value a row can hold */
export type PermValue = boolean | string | number | null | Count | Array<number | string>;
export type PermMap = Record<string, PermValue>;

export interface Category {
  id: string;
  label: string;
  help?: string;
}

export interface CatalogueRow {
  id: string;
  kind: Kind;
  category: string;
  label: string;
  help: string;
  default: PermValue;
  values: string[];
  value_labels: Record<string, string>;
  pages: string[];
  scope: string;
  group: string;
  also_in: string[];
  needs: string[];
  set_row: string | null;
  media: 'music' | 'movie' | 'tv' | null;
  presentation: boolean;
  stored: boolean;
}

export interface Catalogue {
  categories: Category[];
  rows: CatalogueRow[];
  ratings: string[];
  follow: string;
  all: string;
  quality_tokens: string[];
  open_policy: PermMap;
}

export interface QualityOption {
  id: number;
  name: string;
  is_default?: boolean;
  media_type?: string | null;
  tags?: string[];
}
export interface QualityOptions {
  music: QualityOption[];
  movies: QualityOption[];
  tv: QualityOption[];
}

export interface Person {
  id: number;
  name: string;
  avatar_color?: string | null;
  avatar_url?: string | null;
  is_admin: boolean;
  has_pin?: boolean;
  has_password?: boolean;
  disabled?: boolean;
  created_at?: string | null;
}

export interface SummaryLine {
  category: string;
  text: string;
}
export interface Summary {
  sentence: string;
  lines: SummaryLine[];
  never: string[];
}

export interface PersonPerms {
  profile: { id: number; name: string; is_admin: boolean };
  mode: 'admin' | 'template' | 'legacy';
  template: { id: number; name: string; slug: string | null } | null;
  overrides: PermMap;
  effective: PermMap;
  changed_categories: string[];
  capped: string[];
  needs_look: string[];
  summary: Summary;
}

export interface Follower {
  id: number;
  name: string;
}

export interface Sidebar {
  off: string[];
  locked: boolean;
}

export interface Template {
  id: number;
  slug: string | null;
  name: string;
  description: string | null;
  builtin: boolean;
  sort: number | null;
  perms: PermMap | null;
  readable: boolean;
  sidebar: Sidebar | null;
  followers: Follower[];
  updated_at?: string | null;
}

export interface ImpactRow {
  id: string;
  before: PermValue;
  after: PermValue;
}
export interface Impact {
  reaches: number;
  changes: Array<{ profile_id: number; name: string | null; rows: ImpactRow[] }>;
  not_following: Follower[];
}

export interface Invite {
  id: number;
  note: string | null;
  preset: Record<string, unknown> | null;
  state: 'open' | 'used' | 'revoked' | 'expired';
  expires_at?: string | null;
  used_at?: string | null;
  used_by?: number | null;
  revoked_at?: string | null;
  created_at?: string | null;
}

export interface AuditEntry {
  id: number;
  actor_id: number | null;
  actor_name: string | null;
  action: string;
  target_id: number | null;
  target_name: string | null;
  detail: string | null;
  created_at: string | null;
}

export interface Feature {
  id: string;
  label: string;
  desc?: string;
  group?: string;
  pages?: string[];
}
export interface FeatureCatalogue {
  features: Feature[];
  groups: Array<{ id: string; label: string }>;
  presets: Array<{ id: string; label: string; desc?: string; off: string[] }>;
}
