/**
 * Logs (plan 9.1): every log with its backups, sizes and last written in
 * words; one file, one log with its backups, or everything; secrets
 * removed unless the admin says otherwise, and that asks once. The Settings
 * Logs tab's Download opens this view. A member can't get here.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { ViewProps } from './view-props';

import { HelpHost, installHelpApi, installHelpKeys, uninstallHelpKeys } from './host';
import { LogsView, resetRawConfirm } from './logs';
import { useHelpStore } from './store';

const min = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const LIST = {
  log_dir: '/app/logs',
  zip_name: 'soulsync-logs-3.6.7-2026-11-17.zip',
  total_bytes: 39_000_000,
  logs: [
    {
      name: 'app',
      label: 'App log',
      description: 'Everything SoulSync does.',
      bytes: 37_800_000,
      last_written: min(3),
      files: [
        { name: 'app.log', bytes: 6_851_071, last_written: min(3), backup: false },
        { name: 'app.log.1', bytes: 10_485_542, last_written: min(180), backup: true },
        { name: 'app.log.2', bytes: 10_485_000, last_written: min(60 * 30), backup: true },
      ],
    },
    {
      name: 'acoustid',
      label: 'AcoustID fingerprints',
      bytes: 0,
      last_written: null,
      files: [{ name: 'acoustid.log', bytes: 0, last_written: null, backup: false }],
    },
  ],
};

function serve() {
  const hits: string[] = [];
  server.use(
    http.get('*/api/help/logs', () => {
      hits.push('logs');
      return HttpResponse.json(LIST);
    }),
  );
  return hits;
}

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  entry: { view: 'logs' },
  query: '',
  isAdmin: true,
  page: 'settings',
  ...over,
});

let anchors: string[];

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  useHelpStore.setState({ isAdmin: true });
  resetRawConfirm();
  anchors = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
    function (this: HTMLAnchorElement) {
      anchors.push(this.getAttribute('href') ?? '');
    },
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

const query = (href: string) => Object.fromEntries(new URL(href, 'http://x').searchParams);
const app = () => screen.getByRole('article', { name: /App log/ });

describe('the list', () => {
  it('every log with its backups, sizes, and when it was last written, in words', async () => {
    serve();
    render(<LogsView {...props()} />);
    expect(await screen.findByText('2 logs, 37 MB in all')).toBeInTheDocument();
    expect(screen.getByText('/app/logs')).toBeInTheDocument();
    expect(
      within(app()).getByText('36 MB, 2 backups · last written 3 minutes ago'),
    ).toBeInTheDocument();
    expect(within(app()).getByText('app.log.1')).toBeInTheDocument();
    expect(within(app()).getByText('10 MB · 3 hours ago')).toBeInTheDocument();
    expect(within(app()).getByText('10 MB · yesterday')).toBeInTheDocument();
    const empty = screen.getByRole('article', { name: /AcoustID/ });
    // its size, and on its button; never written, so no time
    expect(within(empty).getAllByText('0 bytes')).toHaveLength(2);
    expect(within(empty).queryByText(/last written/)).toBeNull();
    // no backups, no zip button
    expect(within(empty).queryByRole('button', { name: /With backups/ })).toBeNull();
  });
});

describe('downloads, secrets removed by default', () => {
  it('one file, one log with its backups, one backup, and everything', async () => {
    serve();
    render(<LogsView {...props()} />);
    await screen.findByText('2 logs, 37 MB in all');
    expect(screen.getByRole('switch', { name: 'Remove secrets' })).toHaveAttribute(
      'aria-checked',
      'true',
    );

    fireEvent.click(within(app()).getByRole('button', { name: /^app\.log/ }));
    fireEvent.click(within(app()).getByRole('button', { name: /With backups/ }));
    fireEvent.click(within(app()).getByRole('button', { name: 'Download app.log.2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download everything' }));

    expect(anchors).toHaveLength(4);
    expect(anchors[0]).toMatch(/\/api\/help\/logs\/download\?/);
    expect(query(anchors[0])).toEqual({ name: 'app.log' });
    expect(query(anchors[1])).toEqual({ name: 'app', with_backups: '1' });
    expect(query(anchors[2])).toEqual({ name: 'app.log.2' });
    expect(anchors[3]).toMatch(/\/api\/help\/logs\/download-all$/);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Downloading soulsync-logs-3.6.7-2026-11-17.zip.',
    );
  });

  it('raw asks once first; no keeps them removed, yes makes every download raw', async () => {
    serve();
    const first = render(<LogsView {...props()} />);
    await screen.findByText('2 logs, 37 MB in all');
    const sw = screen.getByRole('switch', { name: 'Remove secrets' });

    fireEvent.click(sw);
    const ask = screen.getByRole('alertdialog', {
      name: 'Raw logs can contain API keys and tokens',
    });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    // nothing downloads while it asks
    expect(screen.getByRole('button', { name: 'Download everything' })).toBeDisabled();
    await waitFor(() =>
      expect(within(ask).getByRole('button', { name: 'Keep secrets removed' })).toHaveFocus(),
    );
    fireEvent.click(within(ask).getByRole('button', { name: 'Keep secrets removed' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(sw).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(sw);
    fireEvent.click(screen.getByRole('button', { name: 'Leave secrets in' }));
    expect(sw).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(within(app()).getByRole('button', { name: /^app\.log/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Download everything' }));
    expect(query(anchors[0])).toEqual({ name: 'app.log', raw: '1' });
    expect(anchors[1]).toMatch(/\/api\/help\/logs\/download-all\?raw=1$/);

    // on again, then off again: it already asked
    fireEvent.click(sw);
    expect(sw).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(sw);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(sw).toHaveAttribute('aria-checked', 'false');

    // a fresh view starts redacted again, without asking twice
    first.unmount();
    render(<LogsView {...props()} />);
    await screen.findByText('2 logs, 37 MB in all');
    expect(screen.getByRole('switch', { name: 'Remove secrets' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('a failed list says so calmly and can try again', async () => {
    let fail = true;
    server.use(
      http.get('*/api/help/logs', () =>
        fail
          ? HttpResponse.json({ success: false, error: 'Disk asleep.' }, { status: 500 })
          : HttpResponse.json(LIST),
      ),
    );
    render(<LogsView {...props()} />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent("Couldn't list the logs. Disk asleep.");
    fail = false;
    fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('2 logs, 37 MB in all')).toBeInTheDocument();
  });
});

describe('members', () => {
  afterEach(() => {
    uninstallHelpKeys();
    delete window.SoulSyncHelp;
    delete window.getCurrentProfileContext;
  });

  it('the view asks nothing and shows nothing', async () => {
    const hits = serve();
    const { container } = render(<LogsView {...props({ isAdmin: false })} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(hits).toEqual([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('Health, Report and Logs never open for a member, from any door', async () => {
    const asked: string[] = [];
    server.use(
      http.get('*/api/help/*', ({ request }) => {
        asked.push(new URL(request.url).pathname);
        return HttpResponse.json({});
      }),
      http.post('*/api/help/*', ({ request }) => {
        asked.push(new URL(request.url).pathname);
        return HttpResponse.json({});
      }),
      http.get('*/api/update-check', () => HttpResponse.json({ current_version: '3.6.7' })),
    );
    window.getCurrentProfileContext = () => ({ profileId: 4, isAdmin: false }) as never;
    installHelpApi();
    installHelpKeys();
    render(<HelpHost />);
    for (const view of ['health', 'report', 'logs']) {
      window.SoulSyncHelp!.open(view);
      expect(await screen.findByRole('dialog', { name: 'Help' })).toBeInTheDocument();
      useHelpStore.getState().push({ view: view as 'health' });
      expect(useHelpStore.getState().stack).toEqual([{ view: 'home' }]);
      window.SoulSyncHelp!.close();
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(asked.filter((p) => !p.endsWith('/status'))).toEqual([]);
  });
});
