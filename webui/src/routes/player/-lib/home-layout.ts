/**
 * the home layout, as the server hands it out. the contract lives in
 * core/player/home_layout.py (the phone and tv apps follow the same one):
 *
 *   layout  = { version, billboard, rows: [{ id, style, on }] }
 *   style   = 'standard' | 'large' | 'wide' | 'ranked'
 *   ids     = the catalog's rows, or 'genre:<movie|show>:<genre>'
 *
 * the server cleans every layout before it sends it, and sends the catalog
 * (labels, hints, which styles each row takes) with it. this file only
 * reads them and edits a copy.
 */

export type RowStyle = 'standard' | 'large' | 'wide' | 'ranked';

export interface LayoutRow {
  id: string;
  style: RowStyle;
  on: boolean;
}

export interface HomeLayout {
  version?: number;
  billboard: boolean;
  rows: LayoutRow[];
}

export interface RowDef {
  id: string;
  label: string;
  hint: string;
  styles: RowStyle[];
  style: RowStyle;
  on: boolean;
}

export interface LayoutCatalog {
  version: number;
  styles: { id: RowStyle; label: string }[];
  rows: RowDef[];
}

/** a genre row: "genre:movie:Horror" */
export function genreRowId(kind: 'movie' | 'show', genre: string) {
  return `genre:${kind}:${genre}`;
}

export function parseGenreRow(id: string): { kind: 'movie' | 'show'; genre: string } | null {
  const m = /^genre:(movie|show):(.{1,60})$/.exec(id);
  return m ? { kind: m[1] as 'movie' | 'show', genre: m[2] } : null;
}

/** a row's definition from the catalog; genre rows are made from their id, as the server does */
export function rowDef(catalog: LayoutCatalog | undefined, id: string): RowDef | null {
  const known = catalog?.rows.find((r) => r.id === id);
  if (known) return known;
  const g = parseGenreRow(id);
  if (!g) return null;
  const noun = g.kind === 'movie' ? 'movies' : 'shows';
  return {
    id,
    label: `${g.genre} ${noun}`,
    hint: `${noun[0].toUpperCase()}${noun.slice(1)} you have in ${g.genre}`,
    styles: ['standard', 'large', 'wide', 'ranked'],
    style: 'standard',
    on: true,
  };
}

/** move a row up (-1) or down (+1); out of range does nothing */
export function moveRow(layout: HomeLayout, index: number, step: -1 | 1): HomeLayout {
  const to = index + step;
  if (index < 0 || to < 0 || to >= layout.rows.length) return layout;
  const rows = layout.rows.slice();
  [rows[index], rows[to]] = [rows[to], rows[index]];
  return { ...layout, rows };
}
