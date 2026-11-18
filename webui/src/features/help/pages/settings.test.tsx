/**
 * Settings answers: the Health findings whose fix is the tab you're on
 * (music and video kept apart), that tab's own fix dropped, Check again
 * re-running Health; the two folders and the hybrid order on Downloads;
 * nothing for a member; nothing on a clean tab but the plain answers.
 */

import { act, fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { pageTabs } from '@/features/settings/page-tabs';
import { HttpResponse, http, server } from '@/test/msw';

import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';
import { hybridOrder, useMusicSettingsAnswers, useVideoSettingsAnswers } from './settings';

const FINDINGS = [
  {
    id: 'paths.download',
    severity: 'error',
    area: 'paths',
    title: "Download folder isn't writable",
    detail:
      "/downloads: SoulSync can't write there. Check the folder's owner matches PUID and PGID",
    fix: [{ label: 'Open download folder', kind: 'settings', tab: 'downloads' }],
    docs: 'gs-docker',
  },
  {
    id: 'services.media_server',
    severity: 'error',
    area: 'services',
    title: "Jellyfin isn't answering",
    detail: 'It did not answer within 5 seconds',
    fix: [
      {
        label: 'Test again',
        kind: 'post',
        url: '/api/test-connection',
        body: { service: 'jellyfin' },
      },
      { label: 'Open Connections', kind: 'settings', tab: 'connections' },
    ],
  },
  {
    id: 'video.movies_path',
    severity: 'warning',
    area: 'video',
    title: 'Movies folder is unreachable',
    fix: [{ label: 'Open Library folders', kind: 'settings', tab: 'library', media: 'video' }],
  },
  {
    id: 'paths.music_library_0',
    severity: 'error',
    area: 'paths',
    title: "Music library folder isn't writable",
    fix: [{ label: 'Open music library folder', kind: 'settings', tab: 'library' }],
  },
  { id: 'services.download.hifi', severity: 'ok', area: 'services', title: 'HiFi connected' },
  { id: 'services.download.youtube', severity: 'ok', area: 'services', title: 'YouTube connected' },
  {
    id: 'services.download.soulseek',
    severity: 'warning',
    area: 'services',
    title: "Soulseek (slskd) isn't answering",
    fix: [{ label: 'Open Downloads settings', kind: 'settings', tab: 'downloads' }],
  },
];

let fresh = 0;
beforeEach(() => {
  fresh = 0;
  pageTabs.reset();
  asProfile(1, true);
  server.use(
    http.get('/api/help/health', ({ request }) => {
      if (new URL(request.url).searchParams.get('fresh')) fresh += 1;
      return HttpResponse.json({ status: 'error', findings: FINDINGS });
    }),
  );
});
afterEach(() => {
  delete window.getCurrentProfileContext;
});

describe('settings answers', () => {
  it('registers on both settings pages', () => {
    expect(pageHelpFor('settings').useAnswers).toBe(useMusicSettingsAnswers);
    expect(pageHelpFor('video-settings').useAnswers).toBe(useVideoSettingsAnswers);
  });

  it("the Downloads tab: its problems first, this tab's fix dropped, then the plain answers", async () => {
    pageTabs.show('downloads');
    const { container } = renderAnswers(useMusicSettingsAnswers);
    await waitFor(() =>
      expect(titles(container)).toEqual([
        "Download folder isn't writable",
        "Soulseek (slskd) isn't answering",
        'Download folder or transfer folder?',
        'What does Hybrid do?',
      ]),
    );
    const a = answer(container, 'health:paths.download');
    expect(a).toHaveAttribute('open');
    expect(a.dataset.tone).toBe('warn');
    expect(a.textContent).toContain("Check the folder's owner matches PUID and PGID.");
    expect(within(a).queryByRole('button', { name: 'Open download folder' })).toBeNull();
    expect(within(a).getByRole('button', { name: 'Learn more' })).toBeTruthy();
    fireEvent.click(within(a).getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(fresh).toBe(1));
    expect(answer(container, 'hybrid').textContent).toContain(
      'Yours is HiFi, then YouTube, then Soulseek (slskd).',
    );
  });

  it('follows the tab: Connections shows the media server, with its own test', async () => {
    pageTabs.show('downloads');
    const { container } = renderAnswers(useMusicSettingsAnswers);
    await waitFor(() => expect(titles(container)[0]).toBe("Download folder isn't writable"));
    act(() => pageTabs.show('connections'));
    await waitFor(() => expect(titles(container)).toEqual(["Jellyfin isn't answering"]));
    expect(
      within(answer(container, 'health:services.media_server')).getByRole('button', {
        name: 'Test again',
      }),
    ).toBeTruthy();
  });

  it('a clean tab says nothing', async () => {
    pageTabs.show('appearance');
    const { container } = renderAnswers(useMusicSettingsAnswers);
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
  });

  it('video settings only shows video findings', async () => {
    pageTabs.show('library');
    const { container } = renderAnswers(useVideoSettingsAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['Movies folder is unreachable']));
  });

  it('a member gets nothing and Health is never asked', async () => {
    asProfile(4, false);
    let asked = 0;
    server.use(
      http.get('/api/help/health', () => ((asked += 1), HttpResponse.json({ findings: FINDINGS }))),
    );
    pageTabs.show('downloads');
    const { container } = renderAnswers(useMusicSettingsAnswers, { isAdmin: false });
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
    expect(asked).toBe(0);
  });
});

describe('hybridOrder', () => {
  it('reads the chain in the order Health checked it', () => {
    expect(hybridOrder(FINDINGS as never)).toEqual(['HiFi', 'YouTube', 'Soulseek (slskd)']);
  });
});
