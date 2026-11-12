/**
 * The rail's "Up next" (Sept 2026 dashboard redesign): the automations card
 * in compact form. The next three and a way to the rest, and Boulder's quick
 * switches are NOT in it any more, they live in the page footer.
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AutomationsCard } from './automations-card';

const ROWS = [
  'Clean Downloads',
  'Seeding Sweep',
  'Process Wishlist',
  'Update Database',
  'Backup',
].map((name, i) => ({
  id: i + 1,
  name,
  enabled: 1,
  trigger_type: 'schedule',
  trigger_config: { interval: 1, unit: 'hours' },
  action_type: 'clean_downloads',
  next_run: null,
  run_count: 3,
}));

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      Promise.resolve({
        ok: true,
        json: async () => (String(url).endsWith('/progress') ? {} : ROWS),
      }),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, 'navigateToPage');
});

async function mount(compact: boolean, timeline = false) {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<AutomationsCard compact={compact} timeline={timeline} />);
  });
  await vi.waitFor(() => expect(view.container.querySelector('.dash-autom-row')).not.toBeNull());
  return view;
}

describe('Up next on the board (the timeline)', () => {
  it('draws the next hour as a line and lists five, not three', async () => {
    const soon = new Date(Date.now() + 15 * 60000).toISOString().slice(0, 19).replace('T', ' ');
    const later = new Date(Date.now() + 3 * 3600000).toISOString().slice(0, 19).replace('T', ' ');
    const rows = [...ROWS, { ...ROWS[0], id: 6, name: 'Sixth' }].map((row, i) => ({
      ...row,
      next_run: i === 0 ? soon : i === 1 ? later : null,
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () => (String(url).endsWith('/progress') ? {} : rows),
        }),
      ),
    );
    const view = await mount(true, true);
    expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(5);
    expect(view.container.querySelector('.dash-side-more')!.textContent).toContain('6');
    // only the run inside the hour is on the line; three hours out is in the list
    const marks = view.container.querySelectorAll<HTMLElement>('.dash-autom-lane .dash-autom-mark');
    expect(marks).toHaveLength(1);
    expect(marks[0].title).toBe('Clean Downloads');
    expect(parseFloat(marks[0].style.left)).toBeGreaterThan(20);
    expect(parseFloat(marks[0].style.left)).toBeLessThan(30);
    expect(
      Array.from(view.container.querySelectorAll('.dash-autom-tick')).map((t) => t.textContent),
    ).toEqual(['now', '30m', '1h']);
  });

  it('the narrow rail (the video dashboard) keeps three rows and no line', async () => {
    const view = await mount(true);
    expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(3);
    expect(view.container.querySelector('.dash-autom-lane')).toBeNull();
  });
});

describe('Up next', () => {
  it('shows the next three, titled for the rail, without the quick switches', async () => {
    const view = await mount(true);
    expect(view.container.querySelector('.dash-side-title')!.textContent).toBe('Up next');
    expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(3);
    expect(view.container.querySelector('.dash-quick-settings')).toBeNull();
  });

  it('links to the Automations page for the rest', async () => {
    const navigate = vi.fn();
    window.navigateToPage = navigate as never;
    const view = await mount(true);
    fireEvent.click(view.container.querySelector('.dash-side-link')!);
    expect(navigate).toHaveBeenCalledWith('automations');
  });

  it('See all opens the rest right there, and Show less folds it back', async () => {
    const view = await mount(true);
    const more = view.container.querySelector<HTMLButtonElement>('.dash-side-more')!;
    expect(more.textContent).toBe('See all 5 automations');
    fireEvent.click(more);
    expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(5);
    expect(more.textContent).toBe('Show less');
    fireEvent.click(more);
    expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(3);
  });

  it('the full card still lists everything, with no See all', async () => {
    const view = await mount(false);
    expect(view.container.querySelector('.dash-side-more')).toBeNull();
  });

  it('the full card still lists everything', async () => {
    const view = await mount(false);
    expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(5);
  });
});
