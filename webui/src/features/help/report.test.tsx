/**
 * Report a problem (plan 4.6, 6.3): a switch flips the preview and the size
 * at once by the server's join rule, then the server's own text takes
 * over; the file and the copy are exactly the preview; the GitHub url is
 * used as the server sent it; nothing goes anywhere before a click; a
 * member never asks.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { ViewProps } from './view-props';

import { joinBundle } from './api';
import { formatBytes, textBytes } from './format';
import { ReportView } from './report';
import { useHelpStore } from './store';

const HEADER = 'SoulSync diagnostics, 2026-11-17 21:42 UTC';
const GITHUB =
  'https://github.com/Nezreka/SoulSync/issues/new?template=bug_report.yml&title=Lateralus&version=3.6.7';
const PARTS = [
  ['system', 'Version and platform', true, '== Version and platform ==\nVersion   3.6.7'],
  ['services', 'Services and Health', true, '== Services and Health ==\nHealth  2 errors'],
  [
    'settings',
    'Settings, secrets removed',
    true,
    '== Settings, secrets removed ==\nplex.token  ***',
  ],
  ['paths', 'Folder paths', true, '== Folder paths ==\nDownload  /downloads'],
  ['logs', 'Last 200 log lines', true, '== Last 200 log lines ==\n21:40 ERROR boom'],
  ['errors', 'Recent errors only', false, '== Recent errors only ==\n21:40 ERROR boom'],
] as const;

interface Body {
  parts?: string[];
  note: string;
  where: string;
}

/** the P1 route: every part's text, included or not, and the text by the join rule */
function bundleFor(body: Body) {
  const inc = new Set<string>(body.parts ?? PARTS.filter((p) => p[2]).map((p) => p[0]));
  const parts = PARTS.map(([id, label, def, text]) => ({
    id,
    label,
    hint: `${label} hint`,
    default: def,
    included: inc.has(id),
    text,
    bytes: textBytes(text),
  }));
  // the server's note is redacted; mark it so a test can tell whose text is on screen
  const note = body.note.replace(/hunter2/g, '***');
  const text = joinBundle(HEADER, note, body.where, parts);
  return {
    generated_at: '2026-11-17T21:42:07Z',
    version: '3.6.7',
    filename: 'soulsync-diagnostics-3.6.7-2026-11-17.txt',
    parts,
    text,
    bytes: textBytes(text),
    discord: `**SoulSync 3.6.7** on Docker\n> ${note || 'no note'}`,
    github: { url: `${GITHUB}&n=${encodeURIComponent(note)}`, length: 120 },
  };
}

function serve(opts: { fail?: boolean } = {}) {
  const posts: Body[] = [];
  const other: string[] = [];
  server.use(
    http.post('*/api/help/bundle', async ({ request }) => {
      const body = (await request.json()) as Body;
      posts.push(body);
      if (opts.fail) return HttpResponse.json({ success: false, error: 'Boom.' }, { status: 500 });
      return HttpResponse.json(bundleFor(body));
    }),
    http.get('*/api/help/logs', () => {
      other.push('logs');
      return HttpResponse.json({
        log_dir: '/app/logs',
        zip_name: 'z.zip',
        total_bytes: 41_200_000,
        logs: [],
      });
    }),
  );
  return { posts, other };
}

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  entry: { view: 'report' },
  query: '',
  isAdmin: true,
  page: 'active-downloads',
  ...over,
});

const preview = () => screen.getByTestId('bundle-preview').textContent ?? '';
const toggle = (name: string) => screen.getByRole('switch', { name });

let saved: { blob: Blob; name: string }[];
let anchors: { href: string; download: string }[];
let blobs: Blob[];

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  useHelpStore.setState({
    isAdmin: true,
    update: {
      update_available: false,
      current_version: '3.6.7',
      latest_version: '3.6.7',
      severity: null,
      release_url: null,
      is_docker: true,
    },
  });
  document.body.removeAttribute('data-side');
  saved = [];
  anchors = [];
  blobs = [];
  vi.stubGlobal('open', vi.fn());
  URL.createObjectURL = vi.fn((blob: Blob) => {
    blobs.push(blob);
    return `blob:${blobs.length}`;
  }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
    function (this: HTMLAnchorElement) {
      anchors.push({ href: this.getAttribute('href') ?? '', download: this.download });
      const i = Number((this.getAttribute('href') ?? '').replace('blob:', ''));
      if (i) saved.push({ blob: blobs[i - 1], name: this.download });
    },
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function ready() {
  render(<ReportView {...props()} />);
  await screen.findByTestId('bundle-preview');
}

describe('the join rule', () => {
  it('is the server one: header, the note block, each included part, a blank line between', () => {
    const parts = [
      { included: true, text: '== A ==\na' },
      { included: false, text: '== B ==\nb' },
      { included: true, text: '== C ==\nc' },
    ];
    expect(joinBundle('H', '', '', parts)).toBe('H\n\n== A ==\na\n\n== C ==\nc\n');
    expect(joinBundle('H', 'it broke', 'Downloads, Music', parts)).toBe(
      'H\n\nWhat happened\nit broke\nWhere: Downloads, Music\n\n== A ==\na\n\n== C ==\nc\n',
    );
    // a where alone still writes the block, as bundle.py join does
    expect(joinBundle('H', '', 'W', [])).toBe('H\n\nWhat happened\n\nWhere: W\n');
  });
});

describe('what is included', () => {
  it('opens on the server defaults, and the preview is the server text exactly', async () => {
    const { posts } = serve();
    await ready();
    expect(posts).toHaveLength(1);
    expect(posts[0]).toEqual({ note: '', where: 'Downloads, Music' });
    expect(toggle('Version and platform')).toHaveAttribute('aria-checked', 'true');
    expect(toggle('Recent errors only')).toHaveAttribute('aria-checked', 'false');
    const server = bundleFor({ note: '', where: 'Downloads, Music' });
    expect(preview()).toBe(server.text.replace(/\n$/, ''));
    expect(screen.getByText(formatBytes(server.bytes))).toBeInTheDocument();
    // secrets are marked so people see the redaction happened
    expect(screen.getByText('***').tagName).toBe('MARK');
  });

  it('a switch takes its part out of the preview and the size at once, then the server agrees', async () => {
    const { posts } = serve();
    await ready();
    const before = preview();
    expect(before).toContain('== Folder paths ==');
    fireEvent.click(toggle('Folder paths'));
    expect(toggle('Folder paths')).toHaveAttribute('aria-checked', 'false');
    // at once, by the join rule
    expect(preview()).not.toContain('== Folder paths ==');
    expect(preview()).toContain('== Last 200 log lines ==');
    const local = bundleFor({
      note: '',
      where: 'Downloads, Music',
      parts: ['system', 'services', 'settings', 'logs'],
    });
    expect(screen.getByText(formatBytes(local.bytes))).toBeInTheDocument();
    expect(formatBytes(local.bytes)).not.toBe(formatBytes(textBytes(before + '\n')));

    // then the server builds the same choice
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].parts).toEqual(['system', 'services', 'settings', 'logs']);
    expect(preview()).toBe(local.text.replace(/\n$/, ''));

    fireEvent.click(toggle('Recent errors only'));
    expect(preview()).toContain('== Recent errors only ==');
  });

  it('the note goes in as typed, and the server version (secrets removed) replaces it', async () => {
    const { posts } = serve();
    await ready();
    fireEvent.change(screen.getByRole('textbox', { name: 'What happened' }), {
      target: { value: 'it broke, password hunter2' },
    });
    expect(preview()).toContain(
      'What happened\nit broke, password hunter2\nWhere: Downloads, Music',
    );
    await waitFor(() => expect(posts.at(-1)?.note).toBe('it broke, password hunter2'));
    await waitFor(() => expect(preview()).toContain('it broke, password ***'));
  });
});

describe('sending', () => {
  it('nothing leaves before a click', async () => {
    serve();
    await ready();
    expect(window.open).not.toHaveBeenCalled();
    expect(anchors).toEqual([]);
  });

  it('GitHub: opens the url the server returned, as is, and saves the bundle exactly as previewed', async () => {
    serve();
    await ready();
    const shown = preview();
    fireEvent.click(screen.getByRole('button', { name: /Open a GitHub issue/ }));
    await waitFor(() => expect(window.open).toHaveBeenCalledTimes(1));
    const url = bundleFor({ note: '', where: 'Downloads, Music' }).github.url;
    expect(window.open).toHaveBeenCalledWith(url, '_blank', 'noopener,noreferrer');
    expect(saved).toHaveLength(1);
    expect(saved[0].name).toBe('soulsync-diagnostics-3.6.7-2026-11-17.txt');
    expect(await saved[0].blob.text()).toBe(shown + '\n');
    expect(await screen.findByRole('status')).toHaveTextContent(/GitHub is open in a new tab/);
  });

  it('GitHub after typing: the tab opens on the click and lands on the url built for the new note', async () => {
    const { posts } = serve();
    await ready();
    const tab = { opener: {} as unknown, location: { href: '' }, close: vi.fn() };
    vi.mocked(window.open).mockReturnValue(tab as unknown as Window);
    fireEvent.change(screen.getByRole('textbox', { name: 'What happened' }), {
      target: { value: 'Lateralus never shows up' },
    });
    // clicked before the pause in typing is over
    fireEvent.click(screen.getByRole('button', { name: /Open a GitHub issue/ }));
    expect(window.open).toHaveBeenCalledWith('', '_blank');
    await waitFor(() =>
      expect(tab.location.href).toBe(
        bundleFor({ note: 'Lateralus never shows up', where: 'Downloads, Music' }).github.url,
      ),
    );
    expect(tab.opener).toBeNull();
    expect(posts.at(-1)?.note).toBe('Lateralus never shows up');
    expect(await saved[0].blob.text()).toContain('What happened\nLateralus never shows up');
  });

  it('attach full logs: the zip downloads beside the bundle, and its size shows', async () => {
    const { other } = serve();
    await ready();
    fireEvent.click(toggle('Attach full logs (zip)'));
    expect(await screen.findByText('39 MB')).toBeInTheDocument();
    expect(other).toEqual(['logs']);
    fireEvent.click(screen.getByRole('button', { name: /Download the bundle/ }));
    await waitFor(() => expect(anchors).toHaveLength(2));
    expect(anchors[1].href).toMatch(/\/api\/help\/logs\/download-all$/);
    expect(saved).toHaveLength(1);
  });

  it('Discord: copies the short summary the server built', async () => {
    serve();
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);
    await ready();
    fireEvent.click(screen.getByRole('button', { name: /Copy for Discord/ }));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('**SoulSync 3.6.7** on Docker\n> no note'),
    );
    expect(await screen.findByRole('status')).toHaveTextContent(/Copied/);
    expect(window.open).not.toHaveBeenCalled();
  });

  it('download: the file is the preview, byte for byte', async () => {
    serve();
    await ready();
    fireEvent.click(toggle('Settings, secrets removed'));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 600));
    });
    const shown = preview();
    fireEvent.click(screen.getByRole('button', { name: /Download the bundle/ }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(await saved[0].blob.text()).toBe(shown + '\n');
    expect(shown).not.toContain('Settings, secrets removed');
  });
});

describe('when the server does not answer', () => {
  it('says so calmly and the send buttons wait', async () => {
    serve({ fail: true });
    render(<ReportView {...props()} />);
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/Couldn't build the diagnostics/)).toBeInTheDocument();
    expect(within(alert).getByText(/Nothing was sent/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open a GitHub issue/ })).toBeDisabled();
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('members', () => {
  it('never ask for a bundle, and see nothing', async () => {
    const { posts } = serve();
    const { container } = render(<ReportView {...props({ isAdmin: false })} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(posts).toEqual([]);
    expect(container).toBeEmptyDOMElement();
  });
});
