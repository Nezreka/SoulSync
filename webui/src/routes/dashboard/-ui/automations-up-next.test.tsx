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

async function mount(compact: boolean) {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<AutomationsCard compact={compact} />);
  });
  await vi.waitFor(() => expect(view.container.querySelector('.dash-autom-row')).not.toBeNull());
  return view;
}

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
