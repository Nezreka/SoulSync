/**
 * Settings > You > Playback: skip intros, credits and recaps (ask,
 * automatically, never) and "Play next episode at credits", saved with the
 * profile.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const saved = vi.hoisted(() => ({ calls: [] as Record<string, unknown>[] }));

vi.mock('../../-player.api', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const settings = {
    share_activity: true,
    autoplay_next: true,
    still_watching_after: 3,
    skip_intro: 'auto',
    next_at_credits: true,
  };
  return {
    ...real,
    playerSettingsQuery: () => ({
      queryKey: ['player-settings-skip-test'],
      queryFn: async () => ({ settings }),
    }),
    savePlayerSettings: vi.fn(async (changes: Record<string, unknown>) => {
      saved.calls.push(changes);
      Object.assign(settings, changes);
      return { ...settings };
    }),
  };
});

import { PlaybackSection } from './you';

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PlaybackSection />
    </QueryClientProvider>,
  );
}

describe('skip settings', () => {
  it('shows each choice with what the profile has', async () => {
    mount();
    const intro = await screen.findByRole('radiogroup', { name: 'Skip intros' });
    await waitFor(() =>
      expect(
        within(intro).getByRole('radio', { name: 'Automatically' }).getAttribute('aria-checked'),
      ).toBe('true'),
    );
    const credits = screen.getByRole('radiogroup', { name: 'Skip credits' });
    expect(within(credits).getByRole('radio', { name: 'Ask' }).getAttribute('aria-checked')).toBe(
      'true',
    );
    expect(screen.getByRole('radiogroup', { name: 'Skip recaps' })).toBeTruthy();
  });

  it('saves a pick and the credits switch', async () => {
    mount();
    const credits = await screen.findByRole('radiogroup', { name: 'Skip credits' });
    await waitFor(() =>
      expect(within(credits).getByRole('radio', { name: 'Never' }).hasAttribute('disabled')).toBe(
        false,
      ),
    );
    fireEvent.click(within(credits).getByRole('radio', { name: 'Never' }));
    await waitFor(() => expect(saved.calls).toContainEqual({ skip_credits: 'never' }));
    const sw = screen.getByRole('switch', { name: 'Play next episode at credits' });
    await waitFor(() => expect(sw.hasAttribute('disabled')).toBe(false));
    fireEvent.click(sw);
    await waitFor(() => expect(saved.calls).toContainEqual({ next_at_credits: false }));
  });
});
