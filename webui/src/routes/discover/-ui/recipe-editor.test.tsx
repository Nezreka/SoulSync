import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { RecipeEditor } from './recipe-editor';

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

const EDITING = {
  key: 'recipe_4',
  recipe_id: 4,
  name: 'Prog',
  tracks: [],
  recipe: {
    name: 'Prog',
    seeds: ['Tool'],
    genres: [],
    year_from: null,
    year_to: null,
    mix: { library: 0.6, discovery: 0.3, trending: 0.1 },
    length: 40,
    schedule: 'weekly' as const,
  },
};

describe('RecipeEditor', () => {
  it('says what is missing instead of sending a half recipe', () => {
    const onSaved = vi.fn();
    render(<RecipeEditor onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.click(screen.getByText('Build it'));
    expect(screen.getByRole('alert').textContent).toBe('Give the mix a name.');
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('builds a new mix from the form', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/discover/recipes', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true });
      }),
    );
    const onSaved = vi.fn();
    render(<RecipeEditor onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.change(screen.getByPlaceholderText('Late night prog'), { target: { value: 'Prog' } });
    fireEvent.change(screen.getByPlaceholderText('Tool, Deftones'), {
      target: { value: 'Tool, Soen' },
    });
    fireEvent.change(screen.getByLabelText('Trending share'), { target: { value: '0' } });
    fireEvent.click(screen.getByText('Build it'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Built Prog'));
    expect(body).toMatchObject({
      name: 'Prog',
      seeds: ['Tool', 'Soen'],
      mix: { library: 60, discovery: 30, trending: 0 },
      schedule: 'weekly',
    });
  });

  it('edits and deletes an existing one, asking once before deleting', async () => {
    const calls: string[] = [];
    server.use(
      http.put('*/api/discover/recipes/:id', ({ params }) => {
        calls.push(`put ${String(params.id)}`);
        return HttpResponse.json({ success: true });
      }),
      http.delete('*/api/discover/recipes/:id', ({ params }) => {
        calls.push(`delete ${String(params.id)}`);
        return HttpResponse.json({ success: true });
      }),
    );
    const onSaved = vi.fn();
    render(<RecipeEditor editing={EDITING} onClose={vi.fn()} onSaved={onSaved} />);
    expect(screen.getByDisplayValue('Tool')).toBeTruthy();
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Updated Prog'));
    fireEvent.click(screen.getByText('Delete'));
    expect(calls).toEqual(['put 4']);
    fireEvent.click(screen.getByText('Delete this mix?'));
    await waitFor(() => expect(calls).toEqual(['put 4', 'delete 4']));
    expect(onSaved).toHaveBeenLastCalledWith('Deleted Prog');
  });

  it('shows the server saying no', async () => {
    server.use(
      http.post('*/api/discover/recipes', () =>
        HttpResponse.json({ success: false, error: 'a recipe needs a name' }, { status: 400 }),
      ),
    );
    render(<RecipeEditor onClose={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('Late night prog'), { target: { value: 'x' } });
    fireEvent.change(screen.getByPlaceholderText('Tool, Deftones'), { target: { value: 'Tool' } });
    fireEvent.click(screen.getByText('Build it'));
    expect(await screen.findByText('a recipe needs a name')).toBeTruthy();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<RecipeEditor onClose={onClose} onSaved={vi.fn()} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
