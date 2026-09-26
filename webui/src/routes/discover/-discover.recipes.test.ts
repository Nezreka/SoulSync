import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { Recipe, RecipeMixCard } from './-discover.recipes';

import {
  deleteRecipe,
  fetchRecipes,
  formFromRecipe,
  keepRecipe,
  parseNames,
  RECIPE_FORM_DEFAULTS,
  RECIPE_SCHEDULES,
  recipeFormProblem,
  recipeFromForm,
  recipeMix,
  recipeVerb,
  refreshRecipe,
  saveRecipe,
} from './-discover.recipes';

const RECIPE: Recipe = {
  name: 'Prog',
  seeds: ['Tool', 'Soen'],
  genres: [],
  year_from: 2010,
  year_to: null,
  mix: { library: 0.6, discovery: 0.3, trending: 0.1 },
  length: 40,
  schedule: 'weekly',
};

afterEach(() => server.resetHandlers());

describe('the form', () => {
  it('round-trips a recipe', () => {
    const form = formFromRecipe(RECIPE);
    expect(form).toEqual({
      name: 'Prog',
      seeds: 'Tool, Soen',
      genres: '',
      yearFrom: '2010',
      yearTo: '',
      library: 60,
      discovery: 30,
      trending: 10,
      length: 40,
      schedule: 'weekly',
    });
    expect(recipeFromForm(form)).toEqual({
      ...RECIPE,
      mix: { library: 60, discovery: 30, trending: 10 },
    });
  });

  it('says what stops it being saved', () => {
    expect(recipeFormProblem(RECIPE_FORM_DEFAULTS)).toBe('Give the mix a name.');
    expect(recipeFormProblem({ ...RECIPE_FORM_DEFAULTS, name: 'x' })).toBe(
      'Add at least one artist or genre to build from.',
    );
    expect(
      recipeFormProblem({
        ...RECIPE_FORM_DEFAULTS,
        name: 'x',
        genres: 'metal',
        library: 0,
        discovery: 0,
        trending: 0,
      }),
    ).toBe('Turn up at least one of library, discovery or trending.');
    expect(recipeFormProblem({ ...RECIPE_FORM_DEFAULTS, name: 'x', genres: 'metal' })).toBe('');
  });

  it('reads comma lists and plain years', () => {
    expect(parseNames(' Tool,, Soen ,')).toEqual(['Tool', 'Soen']);
    expect(
      recipeFromForm({ ...RECIPE_FORM_DEFAULTS, yearFrom: 'soon', yearTo: '1999' }).year_from,
    ).toBeNull();
    expect(RECIPE_SCHEDULES.map((s) => s.value)).toEqual(['daily', 'weekly', 'manual']);
  });
});

describe('the mix card', () => {
  const card: RecipeMixCard = {
    key: 'recipe_7',
    recipe_id: 7,
    name: 'Prog',
    recipe: RECIPE,
    explanation: { kind: 'listened', seeds: [{ name: 'Tool' }, { name: 'Soen' }] },
    tracks: [{ name: 'x' }],
  };

  it('is a mix with its own actions and a why for a subtitle', () => {
    const mix = recipeMix(card);
    expect(mix.title).toBe('Prog');
    expect(mix.subtitle).toBe('Because you listen to Tool & Soen');
    expect(mix.actions?.map((a) => a.onclick)).toEqual([
      'download',
      'recipe-refresh:7',
      'recipe-keep:7',
      'recipe-edit:7',
    ]);
  });

  it('reads its verbs back and nothing else', () => {
    expect(recipeVerb('recipe-keep:7')).toEqual(['recipe-keep', 7]);
    expect(recipeVerb('recipe-edit:12')).toEqual(['recipe-edit', 12]);
    expect(recipeVerb('recipe-drop:7')).toBeNull();
    expect(recipeVerb('sync')).toBeNull();
  });
});

describe('the calls', () => {
  it('lists, saves, refreshes, keeps and deletes', async () => {
    const seen: string[] = [];
    server.use(
      http.get('*/api/discover/recipes', () => HttpResponse.json({ success: true, mixes: [] })),
      http.post('*/api/discover/recipes', () => {
        seen.push('create');
        return HttpResponse.json({ success: true });
      }),
      http.put('*/api/discover/recipes/:id', ({ params }) => {
        seen.push(`update ${String(params.id)}`);
        return HttpResponse.json({ success: false, error: 'no such recipe' }, { status: 404 });
      }),
      http.post('*/api/discover/recipes/:id/refresh', () => {
        seen.push('refresh');
        return HttpResponse.json({ success: true });
      }),
      http.post('*/api/discover/recipes/:id/keep', () => {
        seen.push('keep');
        return HttpResponse.json({ success: true, playlist_id: 5 });
      }),
      http.delete('*/api/discover/recipes/:id', () => {
        seen.push('delete');
        return HttpResponse.json({ success: true });
      }),
    );
    expect((await fetchRecipes()).mixes).toEqual([]);
    await saveRecipe(RECIPE);
    // a 404 comes back as an answer, not a throw, so the form can say it
    expect((await saveRecipe(RECIPE, 3)).error).toBe('no such recipe');
    await refreshRecipe(3);
    expect((await keepRecipe(3)).playlist_id).toBe(5);
    await deleteRecipe(3);
    expect(seen).toEqual(['create', 'update 3', 'refresh', 'keep', 'delete']);
  });
});
