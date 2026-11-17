/**
 * Health (plan 4.5, 6.3): findings land in the group their severity says,
 * with the word and not just the colour; every fix kind does its own thing;
 * Check again skips the cache and keeps the last result on screen; errors
 * read calmly; a member never asks.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { HealthFinding, HealthReport } from './api';
import type { ViewProps } from './view-props';

import { HealthView, summarize, withCode } from './health';
import { useHelpStore } from './store';

const AT = new Date(Date.now() - 12_000).toISOString();

const MEDIA: HealthFinding = {
  id: 'services.media_server',
  severity: 'error',
  area: 'services',
  title: "Jellyfin isn't answering",
  detail: "http://jellyfin:8096 didn't reply in 4 seconds.",
  fix: [
    {
      label: 'Test again',
      kind: 'post',
      url: '/api/test-connection',
      body: { service: 'server' },
      recheck: true,
    },
    { label: 'Open Connections', kind: 'settings', tab: 'connections' },
  ],
  docs: 'gs-connecting',
};
const DISK: HealthFinding = {
  id: 'video.movies_space',
  severity: 'warning',
  area: 'disk',
  title: 'Movies drive is nearly full',
  detail: '38.4 GB free.',
  fix: [{ label: 'Open Library folders', kind: 'settings', tab: 'library', media: 'video' }],
};
const REVIEW: HealthFinding = {
  id: 'downloads.review',
  severity: 'warning',
  area: 'downloads',
  title: '4 downloads are waiting for you',
  fix: [{ label: 'Open review', kind: 'page', path: '/active-downloads' }],
};
const TOOLS: HealthFinding = {
  id: 'video.repair_errors',
  severity: 'warning',
  area: 'video',
  title: 'Repair jobs failed',
  fix: [
    { label: 'Open Tools', kind: 'page', path: '/tools', media: 'video' },
    { label: 'Open the logs', kind: 'logs' },
  ],
};
const UPDATE: HealthFinding = {
  id: 'updates.version',
  severity: 'info',
  area: 'updates',
  title: '3.6.8 is out',
  fix: [
    { label: 'Release notes', kind: 'external', href: 'http://host.docker.internal:5030/releases' },
  ],
};
const SHARES: HealthFinding = {
  id: 'setup.slskd_shares',
  severity: 'info',
  area: 'setup',
  title: 'Share some music in slskd',
  fix: [
    {
      label: 'Open slskd',
      kind: 'checklist',
      item: 'slskd_shares',
      href: 'http://localhost:5030/shares',
    },
  ],
};
const TORRENT: HealthFinding = {
  id: 'setup.torrent',
  severity: 'info',
  area: 'setup',
  title: 'Add a torrent client',
  fix: [{ label: 'Set up', kind: 'checklist', item: 'torrent' }],
};
const TIMEOUT: HealthFinding = {
  id: 'checks.media_server',
  severity: 'warning',
  area: 'checks',
  title: "Couldn't check the media server",
  detail: "It didn't answer in 4 seconds.",
  fix: [{ label: 'Check again', kind: 'recheck' }],
};
const OKS: HealthFinding[] = [
  { id: 'services.metadata', severity: 'ok', area: 'services', title: 'Deezer metadata' },
  { id: 'disk.tv', severity: 'ok', area: 'disk', title: 'TV drive', detail: '1.2 TB free' },
];

function report(findings: HealthFinding[], summary?: string): HealthReport {
  const count = (s: string) => findings.filter((f) => f.severity === s).length;
  return {
    status: count('error') ? 'error' : count('warning') ? 'warning' : 'ok',
    checked_at: AT,
    took_ms: 840,
    counts: {
      error: count('error'),
      warning: count('warning'),
      info: count('info'),
      ok: count('ok'),
    },
    summary,
    findings,
  };
}

const FULL = report(
  [MEDIA, DISK, REVIEW, TOOLS, UPDATE, SHARES, TORRENT, TIMEOUT, ...OKS],
  'One thing needs you. Five are worth a look. Everything else, 2 checks, is running.',
);

interface Hits {
  health: string[];
  posts: { url: string; body: unknown }[];
}

function serve(first: HealthReport = FULL, after: HealthReport = first, post = 200): Hits {
  const hits: Hits = { health: [], posts: [] };
  server.use(
    http.get('*/api/help/health', ({ request }) => {
      const url = new URL(request.url);
      hits.health.push(url.search);
      return HttpResponse.json(url.searchParams.get('fresh') ? after : first);
    }),
    http.post('*/api/*', async ({ request }) => {
      hits.posts.push({ url: new URL(request.url).pathname, body: await request.json() });
      return post === 200
        ? HttpResponse.json({ success: true })
        : HttpResponse.json({ success: false, error: 'Jellyfin said no.' }, { status: post });
    }),
  );
  return hits;
}

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  entry: { view: 'health' },
  query: '',
  isAdmin: true,
  page: 'active-downloads',
  ...over,
});

const summaryBox = () => within(screen.getByRole('region', { name: 'Health summary' }));

const card = (title: string) =>
  screen.getByRole('article', { name: new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  useHelpStore.setState({
    isAdmin: true,
    open: true,
    stack: [{ view: 'home' }, { view: 'health' }],
  });
  window.history.replaceState(null, '', '/active-downloads');
  document.body.removeAttribute('data-side');
  window.navigateToPage = vi.fn();
  window.switchSettingsTab = vi.fn();
  window._switchAppSide = vi.fn((side) => document.body.setAttribute('data-side', side));
  vi.stubGlobal('open', vi.fn());
});

afterEach(() => {
  delete window.navigateToPage;
  delete window.switchSettingsTab;
  delete window._switchAppSide;
  document.querySelectorAll('.nav-button').forEach((el) => el.remove());
  document.body.removeAttribute('data-side');
  vi.unstubAllGlobals();
});

describe('the findings', () => {
  it('group by severity, each says its severity in words, and the passed checks fold away', async () => {
    serve();
    render(<HealthView {...props()} />);
    const needs = await screen.findByRole('region', { name: 'Needs you' });
    expect(within(needs).getAllByRole('article')).toHaveLength(1);
    expect(within(needs).getByText("Jellyfin isn't answering")).toBeInTheDocument();
    expect(within(needs).getByText('Error')).toBeInTheDocument();

    const worth = screen.getByRole('region', { name: 'Worth a look' });
    const worthTitles = within(worth)
      .getAllByRole('article')
      .map((el) => el.getAttribute('data-finding'));
    expect(worthTitles).toEqual([
      'video.movies_space',
      'downloads.review',
      'video.repair_errors',
      'updates.version',
      'checks.media_server',
    ]);
    expect(within(worth).getAllByText('Warning')).toHaveLength(4);
    expect(within(worth).getByText('Info')).toBeInTheDocument();

    const setup = screen.getByRole('region', { name: 'Finish setting up' });
    expect(within(setup).getAllByRole('article')).toHaveLength(2);
    expect(within(setup).getAllByText('Setup')).toHaveLength(2);

    const fine = screen.getByRole('region', { name: 'Running fine' });
    const details = fine.querySelector('details')!;
    expect(details.open).toBe(false);
    expect(within(fine).getByText(/2 checks passed/)).toBeInTheDocument();
    expect(within(fine).getByText('TV drive · 1.2 TB free')).toBeInTheDocument();
    // nothing ok sneaks into the groups above
    expect(screen.queryByRole('article', { name: /Deezer/ })).toBeNull();
  });

  it('the summary is the server sentence, its first sentence stressed, and it is announced', async () => {
    serve();
    render(<HealthView {...props()} />);
    const lead = await screen.findByText('One thing needs you.');
    expect(lead.tagName).toBe('EM');
    expect(screen.getByText(/Checked 1\d seconds ago, took 840 ms/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'One thing needs you. Five are worth a look.',
      ),
    );
    // the dot and Home read the same result
    expect(useHelpStore.getState().status?.errors).toBe(1);
  });

  it('builds a sentence from the counts when the server sent none, and says all fine plainly', () => {
    expect(summarize(report([MEDIA, MEDIA, DISK, ...OKS]))).toBe(
      'Two things need you. One is worth a look. Everything else, 2 checks, is running.',
    );
    expect(summarize(report(OKS))).toBe('Everything is running, all 2 checks.');
  });

  it('paths and addresses in a detail read as code, the words around them do not', () => {
    render(<p>{withCode("http://jellyfin:8096 didn't reply, and /downloads isn't writable.")}</p>);
    expect(screen.getByText('http://jellyfin:8096').tagName).toBe('CODE');
    expect(screen.getByText('/downloads').tagName).toBe('CODE');
    expect(document.querySelectorAll('code')).toHaveLength(2);
  });
});

describe('check again', () => {
  it('skips the cache, keeps the last result on screen while it runs, then shows the new one', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const hits: string[] = [];
    server.use(
      http.get('*/api/help/health', async ({ request }) => {
        const url = new URL(request.url);
        hits.push(url.search);
        if (url.searchParams.get('fresh')) {
          await gate;
          return HttpResponse.json(report(OKS));
        }
        return HttpResponse.json(FULL);
      }),
    );
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    expect(hits).toEqual(['']);

    fireEvent.click(summaryBox().getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('Checking again')).toBeInTheDocument();
    // optimistic: the old findings stay, dimmed, and the button holds
    expect(screen.getByText("Jellyfin isn't answering")).toBeInTheDocument();
    expect(document.querySelector('[data-stale]')).not.toBeNull();
    expect(summaryBox().getByRole('button', { name: 'Checking' })).toBeDisabled();
    expect(hits).toEqual(['', '?fresh=1']);

    await act(async () => release());
    await waitFor(() => expect(screen.queryByText("Jellyfin isn't answering")).toBeNull());
    expect(document.querySelector('[data-stale]')).toBeNull();
    expect(summaryBox().getByText('Everything is running, all 2 checks.')).toBeInTheDocument();
    expect(useHelpStore.getState().status?.errors).toBe(0);
  });

  it('a failed first read says so calmly, with a way to try again', async () => {
    let fail = true;
    server.use(
      http.get('*/api/help/health', () =>
        fail
          ? HttpResponse.json(
              { success: false, error: "Health couldn't run, the app log says why" },
              { status: 500 },
            )
          : HttpResponse.json(FULL),
      ),
    );
    render(<HealthView {...props()} />);
    expect(await screen.findByText("Couldn't check just now.")).toBeInTheDocument();
    expect(summaryBox().getByText(/the app log says why/)).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText("Jellyfin isn't answering")).toBeInTheDocument();
  });

  it('a failed check again keeps the last result and says it is the last one', async () => {
    server.use(
      http.get('*/api/help/health', ({ request }) =>
        new URL(request.url).searchParams.get('fresh')
          ? HttpResponse.json({ success: false, error: 'Timed out.' }, { status: 500 })
          : HttpResponse.json(FULL),
      ),
    );
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    fireEvent.click(summaryBox().getByRole('button', { name: 'Check again' }));
    expect(
      await screen.findByText(/Couldn't check again: Timed out. This is the last result./),
    ).toBeInTheDocument();
    expect(screen.getByText("Jellyfin isn't answering")).toBeInTheDocument();
  });
});

describe('each fix kind', () => {
  it('post: posts the route with its body, then checks again with the cache skipped and says it is fixed', async () => {
    const hits = serve(FULL, report([DISK, ...OKS]));
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    fireEvent.click(
      within(card("Jellyfin isn't answering")).getByRole('button', { name: /Test again/ }),
    );
    await waitFor(() => expect(hits.health).toEqual(['', '?fresh=1']));
    expect(hits.posts).toEqual([{ url: '/api/test-connection', body: { service: 'server' } }]);
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent("Jellyfin isn't answering: fixed."),
    );
    expect(screen.queryByText("Jellyfin isn't answering")).toBeNull();
  });

  it('post: a refusal shows the reason on the card and does not check again', async () => {
    const hits = serve(FULL, FULL, 502);
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    fireEvent.click(
      within(card("Jellyfin isn't answering")).getByRole('button', { name: /Test again/ }),
    );
    expect(await within(card("Jellyfin isn't answering")).findByRole('alert')).toHaveTextContent(
      'Jellyfin said no.',
    );
    expect(hits.health).toEqual(['']);
  });

  it('post: a url that is not the app api is never called', async () => {
    const evil = report([
      { ...MEDIA, fix: [{ label: 'Test again', kind: 'post', url: 'https://evil.test/x' }] },
    ]);
    const hits = serve(evil);
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    fireEvent.click(screen.getByRole('button', { name: /Test again/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "That fix isn't one SoulSync knows.",
    );
    expect(hits.posts).toEqual([]);
  });

  it('settings: opens Settings on that tab', async () => {
    serve();
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    fireEvent.click(screen.getByRole('button', { name: 'Open Connections' }));
    expect(window.switchSettingsTab).toHaveBeenCalledWith('connections');
    expect(window.navigateToPage).toHaveBeenCalledWith('settings', undefined);
    expect(window._switchAppSide).not.toHaveBeenCalled();
  });

  it('settings with media video: the video side, its settings page, on that tab', async () => {
    serve();
    const nav = document.createElement('a');
    nav.className = 'nav-button';
    nav.dataset.videoPage = 'video-settings';
    const clicked = vi.fn();
    nav.addEventListener('click', (e) => {
      e.preventDefault();
      clicked();
    });
    document.body.appendChild(nav);
    render(<HealthView {...props()} />);
    await screen.findByText('Movies drive is nearly full');
    fireEvent.click(screen.getByRole('button', { name: 'Open Library folders' }));
    expect(window._switchAppSide).toHaveBeenCalledWith('video');
    expect(clicked).toHaveBeenCalledTimes(1);
    expect(window.switchSettingsTab).toHaveBeenCalledWith('library');
    expect(window.navigateToPage).not.toHaveBeenCalled();
  });

  it('page: goes to the page; with media video, to the video side page', async () => {
    serve();
    const nav = document.createElement('a');
    nav.className = 'nav-button';
    nav.dataset.videoPage = 'video-tools';
    const clicked = vi.fn();
    nav.addEventListener('click', clicked);
    document.body.appendChild(nav);
    render(<HealthView {...props()} />);
    await screen.findByText('4 downloads are waiting for you');
    fireEvent.click(screen.getByRole('button', { name: 'Open review' }));
    expect(window.navigateToPage).toHaveBeenCalledWith('active-downloads', undefined);

    fireEvent.click(screen.getByRole('button', { name: 'Open Tools' }));
    expect(window._switchAppSide).toHaveBeenCalledWith('video');
    expect(clicked).toHaveBeenCalledTimes(1);
  });

  it('external: a new tab, with a docker-side host swapped for the one in the browser', async () => {
    serve();
    render(<HealthView {...props()} />);
    await screen.findByText('3.6.8 is out');
    fireEvent.click(screen.getByRole('button', { name: /Release notes/ }));
    expect(window.open).toHaveBeenCalledWith(
      `http://${window.location.hostname}:5030/releases`,
      '_blank',
      'noopener,noreferrer',
    );
  });

  it("checklist: runs the Finish setting up card's own action for the item", async () => {
    serve();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<HealthView {...props()} />);
      await screen.findByText('Share some music in slskd');
      fireEvent.click(screen.getByRole('button', { name: 'Open slskd' }));
      expect(window.open).toHaveBeenCalledWith(
        `http://${window.location.hostname}:5030/shares`,
        '_blank',
        'noopener',
      );
      fireEvent.click(screen.getByRole('button', { name: 'Set up' }));
      expect(window.navigateToPage).toHaveBeenCalledWith('settings');
      await act(async () => {
        vi.advanceTimersByTime(100);
      });
      expect(window.switchSettingsTab).toHaveBeenCalledWith('downloads');
    } finally {
      vi.useRealTimers();
    }
  });

  it('logs: opens the Logs view in the sheet', async () => {
    serve();
    render(<HealthView {...props()} />);
    await screen.findByText('Repair jobs failed');
    fireEvent.click(screen.getByRole('button', { name: /Open the logs/ }));
    expect(useHelpStore.getState().stack.at(-1)).toEqual({ view: 'logs' });
  });

  it('recheck: checks again with the cache skipped and says whether it cleared', async () => {
    const hits = serve(FULL, FULL);
    render(<HealthView {...props()} />);
    await screen.findByText("Couldn't check the media server");
    fireEvent.click(
      within(card("Couldn't check the media server")).getByRole('button', { name: /Check again/ }),
    );
    await waitFor(() => expect(hits.health).toEqual(['', '?fresh=1']));
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        "Couldn't check the media server: still there.",
      ),
    );
  });

  it('learn more opens the docs article in the sheet', async () => {
    serve();
    render(<HealthView {...props()} />);
    await screen.findByText("Jellyfin isn't answering");
    fireEvent.click(
      within(card("Jellyfin isn't answering")).getByRole('button', { name: 'Learn more' }),
    );
    expect(useHelpStore.getState().stack.at(-1)).toEqual({
      view: 'article',
      params: { id: 'gs-connecting' },
    });
  });
});

describe('members', () => {
  it('never ask the server, and see nothing', async () => {
    const hits = serve();
    const { container } = render(<HealthView {...props({ isAdmin: false })} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(hits.health).toEqual([]);
    expect(container).toBeEmptyDOMElement();
  });
});
