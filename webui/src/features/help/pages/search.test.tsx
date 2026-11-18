/**
 * Search and Discover answers from /status: Spotify's rate limit with the
 * time left, the metadata source not answering (Settings for the admin
 * only), the download client down on Search only; nothing when all is well.
 */

import { waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { answer, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';
import { sourceAnswers, useDiscoverAnswers, useSearchAnswers } from './search';

let status: Record<string, unknown> = {};
beforeEach(() => {
  status = {
    metadata_source: { source: 'deezer', connected: true },
    spotify: { rate_limited: false },
    soulseek: { connected: true, source: 'hybrid' },
  };
  server.use(http.get('/status', () => HttpResponse.json(status)));
});

describe('search and discover answers', () => {
  it('registers on both pages', () => {
    expect(pageHelpFor('search').useAnswers).toBe(useSearchAnswers);
    expect(pageHelpFor('discover').useAnswers).toBe(useDiscoverAnswers);
  });

  it('all well says nothing', async () => {
    const { container } = renderAnswers(useSearchAnswers);
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
  });

  it('Spotify rate limited, with the time left', async () => {
    status = {
      ...status,
      spotify: { rate_limited: true, rate_limit: { remaining_seconds: 1140 } },
    };
    const { container } = renderAnswers(useDiscoverAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toEqual(['Why is Spotify paused?']));
    expect(answer(container, 'spotify-limit').textContent).toContain(
      "Spotify asked SoulSync to slow down, so it's paused for about 19 min more.",
    );
  });

  it('the metadata source down: Settings only for the admin', async () => {
    status = { ...status, metadata_source: { source: 'deezer', connected: false } };
    const admin = renderAnswers(useSearchAnswers);
    await waitFor(() =>
      expect(titles(admin.container)).toEqual(['Why are searches coming back empty?']),
    );
    const a = answer(admin.container, 'metadata-down');
    expect(a.textContent).toContain("Deezer isn't answering right now");
    expect(within(a).getByRole('button', { name: 'Open Sources' })).toBeTruthy();
    const member = renderAnswers(useSearchAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(member.container)).toHaveLength(1));
    expect(member.container.querySelectorAll('button')).toHaveLength(0);
  });

  it('a download client down shows on Search, not Discover', () => {
    const down = { ...status, soulseek: { connected: false, source: 'soulseek' } };
    expect(
      sourceAnswers(down, { page: 'search', isAdmin: true }, 'search').map((a) => a.title),
    ).toEqual(["Why won't downloads start from here?"]);
    expect(sourceAnswers(down, { page: 'discover', isAdmin: true }, 'discover')).toEqual([]);
  });

  it('a failed read says nothing', () => {
    expect(sourceAnswers(undefined, { page: 'search', isAdmin: true }, 'search')).toEqual([]);
  });
});
