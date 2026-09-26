/**
 * Renewable mixes: recipes you keep.
 *
 * The server owns the rules (core/personalized/recipes.py): one song per
 * artist, a short source hands its share to the others, a reserve replaces
 * tracks that won't download, and the mix renews on its schedule. This file
 * is the form, the mix card and the calls.
 */

import { apiClient, readJson } from '@/app/api-client';

import type { Explanation } from './-discover.explanation';
import type { DiscoverMix, MixAction } from './-discover.mixes';

import { explanationLine } from './-discover.explanation';

export type RecipeSchedule = 'daily' | 'weekly' | 'manual';

export interface Recipe {
  name: string;
  seeds: string[];
  genres: string[];
  year_from: number | null;
  year_to: number | null;
  mix: { library: number; discovery: number; trending: number };
  length: number;
  schedule: RecipeSchedule;
}

export interface RecipeMixCard {
  key: string;
  recipe_id: number;
  name: string;
  recipe: Recipe;
  explanation?: Explanation;
  tracks: unknown[];
  counts?: Record<string, number>;
  replaced?: number;
  generated_at?: string | null;
}

type Result = { success?: boolean; error?: string };

export function fetchRecipes(): Promise<Result & { mixes?: RecipeMixCard[] }> {
  return readJson(apiClient.get('discover/recipes'));
}

export function saveRecipe(
  recipe: Recipe,
  recipeId?: number,
): Promise<Result & { mix?: RecipeMixCard }> {
  return readJson(
    recipeId == null
      ? apiClient.post('discover/recipes', { json: recipe, throwHttpErrors: false })
      : apiClient.put(`discover/recipes/${recipeId}`, { json: recipe, throwHttpErrors: false }),
  );
}

export function deleteRecipe(recipeId: number): Promise<Result> {
  return readJson(apiClient.delete(`discover/recipes/${recipeId}`, { throwHttpErrors: false }));
}

export function refreshRecipe(recipeId: number): Promise<Result & { mix?: RecipeMixCard }> {
  return readJson(apiClient.post(`discover/recipes/${recipeId}/refresh`));
}

export function keepRecipe(recipeId: number): Promise<Result & { playlist_id?: number }> {
  return readJson(apiClient.post(`discover/recipes/${recipeId}/keep`, { throwHttpErrors: false }));
}

// ── the mix card ────────────────────────────────────────────────────────────

/** `recipe-keep:12` → ['recipe-keep', 12]; anything else → null. */
export function recipeVerb(onclick: string): [string, number] | null {
  const m = /^(recipe-(?:refresh|keep|edit)):(\d+)$/.exec(onclick);
  return m ? [m[1], Number(m[2])] : null;
}

export function recipeMix(card: RecipeMixCard): DiscoverMix {
  const id = card.recipe_id;
  const actions: MixAction[] = [
    { label: 'Download', closeFirst: true, onclick: 'download' },
    { label: 'New tracks', onclick: `recipe-refresh:${id}` },
    { label: 'Keep this one', primary: true, onclick: `recipe-keep:${id}` },
    { label: 'Edit mix', closeFirst: true, onclick: `recipe-edit:${id}` },
  ];
  return {
    key: card.key,
    title: card.name,
    subtitle: explanationLine(card.explanation) || 'Your mix',
    tracks: card.tracks,
    actions,
  };
}

// ── the form ────────────────────────────────────────────────────────────────

export interface RecipeForm {
  name: string;
  seeds: string;
  genres: string;
  yearFrom: string;
  yearTo: string;
  library: number;
  discovery: number;
  trending: number;
  length: number;
  schedule: RecipeSchedule;
}

export const RECIPE_FORM_DEFAULTS: RecipeForm = {
  name: '',
  seeds: '',
  genres: '',
  yearFrom: '',
  yearTo: '',
  library: 60,
  discovery: 30,
  trending: 10,
  length: 40,
  schedule: 'weekly',
};

export const RECIPE_SCHEDULES: { value: RecipeSchedule; label: string }[] = [
  { value: 'daily', label: 'Every day' },
  { value: 'weekly', label: 'Every week' },
  { value: 'manual', label: 'Only when I ask' },
];

/** "Tool, Deftones ,, Soen" → ['Tool', 'Deftones', 'Soen']. */
export function parseNames(text: string): string[] {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseYear(text: string): number | null {
  const n = Number(text.trim());
  return text.trim() && Number.isInteger(n) ? n : null;
}

/** What's wrong with the form, or '' when it can be saved. */
export function recipeFormProblem(form: RecipeForm): string {
  if (!form.name.trim()) return 'Give the mix a name.';
  if (!parseNames(form.seeds).length && !parseNames(form.genres).length)
    return 'Add at least one artist or genre to build from.';
  if (form.library + form.discovery + form.trending <= 0)
    return 'Turn up at least one of library, discovery or trending.';
  return '';
}

export function recipeFromForm(form: RecipeForm): Recipe {
  return {
    name: form.name.trim(),
    seeds: parseNames(form.seeds),
    genres: parseNames(form.genres),
    year_from: parseYear(form.yearFrom),
    year_to: parseYear(form.yearTo),
    mix: { library: form.library, discovery: form.discovery, trending: form.trending },
    length: form.length,
    schedule: form.schedule,
  };
}

export function formFromRecipe(recipe: Recipe): RecipeForm {
  const pct = (v: number) => Math.round((v ?? 0) * 100);
  return {
    name: recipe.name,
    seeds: recipe.seeds.join(', '),
    genres: recipe.genres.join(', '),
    yearFrom: recipe.year_from != null ? String(recipe.year_from) : '',
    yearTo: recipe.year_to != null ? String(recipe.year_to) : '',
    library: pct(recipe.mix.library),
    discovery: pct(recipe.mix.discovery),
    trending: pct(recipe.mix.trending),
    length: recipe.length,
    schedule: recipe.schedule,
  };
}
