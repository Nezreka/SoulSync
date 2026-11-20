/**
 * The help sheet (plan 6.3): opens from the ? button, ? and F1, Ctrl K at
 * the search field; focus goes in and comes back to the ?; Esc closes;
 * phone keeps Tab inside; the back stack; member vs admin Home; the amber
 * dot only for an admin with an error.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { pageTabs } from '@/features/settings/page-tabs';
import { HttpResponse, http, server } from '@/test/msw';

import { sentence } from './home';
import { HelpHost, installHelpApi, installHelpKeys, uninstallHelpKeys } from './host';
import { pageIdFrom } from './page';
import { registerPageHelp } from './pages';
import { useHelpStore } from './store';

type Profile = { profileId: number; isAdmin: boolean };

const ADMIN: Profile = { profileId: 1, isAdmin: true };
const MEMBER: Profile = { profileId: 4, isAdmin: false };

type Finding = { id: string; severity: string; area: string; title: string };
type Report = {
  status: string;
  checked_at: string;
  counts: Record<string, number>;
  findings: Finding[];
};

const AT = '2026-11-17T21:42:07Z';
const report = (findings: Finding[]): Report => ({
  status: findings.some((f) => f.severity === 'error')
    ? 'error'
    : findings.some((f) => f.severity === 'warning')
      ? 'warning'
      : 'ok',
  checked_at: AT,
  counts: {
    error: findings.filter((f) => f.severity === 'error').length,
    warning: findings.filter((f) => f.severity === 'warning').length,
  },
  findings,
});
const DISK = {
  id: 'disk.movies',
  severity: 'warning',
  area: 'disk',
  title: 'Movies drive is nearly full',
};
const OK_HEALTH = report([
  { id: 'services.metadata', severity: 'ok', area: 'services', title: 'Deezer' },
]);
const WARN_HEALTH = report([DISK]);
const ERROR_HEALTH = report([
  {
    id: 'services.media_server',
    severity: 'error',
    area: 'services',
    title: "Jellyfin isn't answering",
  },
  { id: 'paths.download', severity: 'error', area: 'paths', title: '/downloads isn’t writable' },
  DISK,
]);

/** the P1 routes (scrap/help/HELP_API.md): /status is {errors, checked_at, refreshing} */
function serve(health: Report = OK_HEALTH) {
  const hits: string[] = [];
  server.use(
    http.get('*/api/help/status', () => {
      hits.push('status');
      return HttpResponse.json({ errors: health.counts.error, checked_at: AT, refreshing: false });
    }),
    http.get('*/api/help/health', () => {
      hits.push('health');
      return HttpResponse.json(health);
    }),
    http.get('*/api/update-check', () =>
      HttpResponse.json({
        update_available: false,
        current_version: '3.6.7',
        latest_version: '3.6.7',
        severity: null,
        release_url: null,
        is_docker: true,
      }),
    ),
  );
  return hits;
}

function mount(profile: Profile = ADMIN) {
  window.getCurrentProfileContext = () => profile as never;
  const button = document.createElement('button');
  button.id = 'helper-float-btn';
  button.textContent = '?';
  button.addEventListener('click', () => window.SoulSyncHelp?.toggle());
  document.body.appendChild(button);
  installHelpApi();
  installHelpKeys();
  const view = render(<HelpHost />);
  return { button, ...view };
}

const sheet = () =>
  screen.queryByRole('dialog', { name: /Help|Search|What's new|Shortcuts|Health|Logs|Report/ });

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  window.history.replaceState(null, '', '/active-downloads');
  try {
    localStorage.removeItem('soulsync_helper_version_seen');
  } catch {
    /* none */
  }
});

afterEach(() => {
  uninstallHelpKeys();
  delete window.SoulSyncHelp;
  delete window.getCurrentProfileContext;
  document.getElementById('helper-float-btn')?.remove();
  vi.unstubAllGlobals();
});

describe('opening and closing', () => {
  it('the ? button opens it, focus goes to the search field, and Esc closes it back to the button', async () => {
    serve();
    const { button } = mount();
    expect(sheet()).toBeNull();
    expect(button).toHaveAttribute('aria-expanded', 'false');

    button.focus();
    fireEvent.click(button);
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    expect(dialog).toHaveAttribute('aria-modal', 'false');
    expect(button).toHaveAttribute('aria-expanded', 'true');
    const search = within(dialog).getByRole('searchbox', {
      name: 'Search help, settings and actions',
    });
    await waitFor(() => expect(search).toHaveFocus());

    fireEvent.keyDown(search, { key: 'Escape' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    await waitFor(() => expect(button).toHaveFocus());
    await waitFor(() => expect(sheet()).toBeNull());
  });

  it('the close button closes it', async () => {
    serve();
    const { button } = mount();
    fireEvent.click(button);
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close help' }));
    await waitFor(() => expect(button).toHaveFocus());
    expect(useHelpStore.getState().open).toBe(false);
  });

  it('? and F1 open it from the page, and ? is ignored while typing', async () => {
    serve();
    mount();
    const field = document.createElement('input');
    document.body.appendChild(field);
    field.focus();
    fireEvent.keyDown(field, { key: '?' });
    expect(useHelpStore.getState().open).toBe(false);
    field.remove();

    fireEvent.keyDown(document.body, { key: '?' });
    expect(await screen.findByRole('dialog', { name: 'Help' })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'F1' });
    expect(useHelpStore.getState().open).toBe(false);
    fireEvent.keyDown(document.body, { key: 'F1' });
    expect(useHelpStore.getState().open).toBe(true);
  });

  it('Ctrl K with the sheet open puts the caret in its search field (closed, it opens the palette)', async () => {
    serve();
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    const field = within(dialog).getByRole('searchbox');
    await waitFor(() => expect(field).toHaveFocus());
    field.blur();
    fireEvent.keyDown(document.body, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(field).toHaveFocus());
  });

  it('Esc from the page beside the open sheet closes it too', async () => {
    serve();
    mount();
    act(() => window.SoulSyncHelp!.open());
    await screen.findByRole('dialog', { name: 'Help' });
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(useHelpStore.getState().open).toBe(false);
  });

  it('Esc in the search field clears the text first, then closes', async () => {
    serve();
    mount();
    act(() => window.SoulSyncHelp!.open());
    const search = await screen.findByRole('searchbox');
    fireEvent.change(search, { target: { value: 'path' } });
    fireEvent.keyDown(search, { key: 'Escape' });
    expect(useHelpStore.getState().query).toBe('');
    expect(useHelpStore.getState().open).toBe(true);
    fireEvent.keyDown(search, { key: 'Escape' });
    expect(useHelpStore.getState().open).toBe(false);
  });
});

describe('on a phone', () => {
  it('is modal and keeps Tab inside', async () => {
    serve();
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        matches: true,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    // no keyboard popping up over the sheet: the title takes focus, not the field
    await waitFor(() =>
      expect(within(dialog).getByRole('heading', { name: 'Help' })).toHaveFocus(),
    );

    const close = within(dialog).getByRole('button', { name: 'Close help' });
    const buttons = within(dialog).getAllByRole('button');
    const last = buttons[buttons.length - 1];
    last.focus();
    fireEvent.keyDown(last, { key: 'Tab' });
    // wraps to the first focusable thing in the sheet, the close button (no back on Home)
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
  });
});

describe('the view stack', () => {
  it('a tile pushes its view, Back pops it, and typed text shows results', async () => {
    serve();
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    expect(within(dialog).queryByRole('button', { name: 'Back' })).toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Shortcuts' }));
    expect(await screen.findByRole('dialog', { name: 'Shortcuts' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog', { name: 'Help' })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'pa' } });
    expect(await screen.findByRole('dialog', { name: 'Search' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(useHelpStore.getState().query).toBe('');
    expect(await screen.findByRole('dialog', { name: 'Help' })).toBeInTheDocument();
  });

  it('opens straight at a view from the api, with Home under it', async () => {
    serve();
    mount();
    act(() => window.SoulSyncHelp!.open('whats-new'));
    expect(await screen.findByRole('dialog', { name: "What's new" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog', { name: 'Help' })).toBeInTheDocument();
  });
});

describe('admin and member Home', () => {
  it('an admin gets the status card, Logs, Report a problem and the platform footer', async () => {
    serve(ERROR_HEALTH);
    mount(ADMIN);
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    const card = await within(dialog).findByRole('button', { name: /2 things need you/ });
    expect(card).toHaveTextContent("Jellyfin isn't answering, and /downloads isn’t writable.");
    expect(within(dialog).getByRole('button', { name: 'Logs' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /Report a problem/ })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /GitHub issues/ })).toBeInTheDocument();
    expect(await within(dialog).findByText('SoulSync 3.6.7 · Docker')).toBeInTheDocument();
    expect(within(dialog).getByText('Up to date')).toBeInTheDocument();

    fireEvent.click(card);
    expect(await screen.findByRole('dialog', { name: 'Health' })).toHaveAttribute('data-wide');
  });

  it('a member gets no status, Logs, Report or GitHub; Something wrong? instead', async () => {
    const hits = serve(ERROR_HEALTH);
    mount(MEMBER);
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    expect(
      within(dialog).getByRole('searchbox', { name: 'Search help and pages' }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('heading', { name: 'Something wrong?' })).toBeInTheDocument();
    expect(within(dialog).getByText('Report a song, album or title')).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: /need you|Everything is running|Health/ }),
    ).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'Logs' })).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Report a problem/ })).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /GitHub/ })).toBeNull();
    expect(await within(dialog).findByText('SoulSync 3.6.7')).toBeInTheDocument();
    expect(within(dialog).queryByText(/Docker|Up to date/)).toBeNull();
    // a member never asks for the admin status
    expect(hits).toEqual([]);
  });

  it('a member cannot be sent to an admin view', async () => {
    serve();
    mount(MEMBER);
    act(() => window.SoulSyncHelp!.open('health'));
    expect(await screen.findByRole('dialog', { name: 'Help' })).toBeInTheDocument();
    act(() => useHelpStore.getState().push({ view: 'report' }));
    expect(screen.getByRole('dialog', { name: 'Help' })).toBeInTheDocument();
    expect(useHelpStore.getState().stack).toEqual([{ view: 'home' }]);
  });
});

describe('the amber dot on the ?', () => {
  it('shows for an admin when Health has an error', async () => {
    serve(ERROR_HEALTH);
    const { button } = mount(ADMIN);
    await waitFor(() => expect(button).toHaveAttribute('data-help-alert'));
    expect(button).toHaveAttribute('aria-label', 'Help, something needs you');
  });

  it('never for warnings only', async () => {
    const hits = serve(WARN_HEALTH);
    const { button } = mount(ADMIN);
    await waitFor(() => expect(hits.length).toBeGreaterThan(0));
    await waitFor(() => expect(useHelpStore.getState().status).not.toBeNull());
    expect(button).not.toHaveAttribute('data-help-alert');
    expect(button).toHaveAttribute('aria-label', 'Help');
  });

  it('never for a member, who never asks', async () => {
    const hits = serve(ERROR_HEALTH);
    const { button } = mount(MEMBER);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(hits).toEqual([]);
    expect(button).not.toHaveAttribute('data-help-alert');
  });

  it('goes away when the admin switches to a member', async () => {
    serve(ERROR_HEALTH);
    const { button } = mount(ADMIN);
    await waitFor(() => expect(button).toHaveAttribute('data-help-alert'));
    window.getCurrentProfileContext = () => MEMBER as never;
    act(() => {
      window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
    });
    expect(button).not.toHaveAttribute('data-help-alert');
  });
});

describe('on this page', () => {
  it('knows the page from the url, video side included', () => {
    expect(pageIdFrom('/active-downloads')).toBe('active-downloads');
    expect(pageIdFrom('/artist-detail/library/12')).toBe('artist-detail');
    expect(pageIdFrom('/tools', '?media=video')).toBe('video-tools');
    expect(pageIdFrom('/video-library')).toBe('video-library');
    expect(pageIdFrom('/')).toBe('dashboard');
  });

  it("shows the page's summary and articles, and a page file's live answers", async () => {
    serve();
    function useTestAnswers() {
      return [
        {
          id: 'stuck',
          title: 'Why are 3 downloads stuck in Queued?',
          live: true,
          open: true,
          body: <p>Queued on the uploader&apos;s side.</p>,
        },
      ];
    }
    registerPageHelp({ id: 'active-downloads', useAnswers: useTestAnswers });
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    const here = within(dialog).getByRole('region', { name: 'On this page: Downloads' });
    expect(here).toHaveTextContent('Everything SoulSync is fetching');
    expect(within(here).getByText('Why are 3 downloads stuck in Queued?')).toBeInTheDocument();
    expect(within(here).getByText('Live')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /Download Manager/ })).toBeInTheDocument();
    registerPageHelp({ id: 'active-downloads', useAnswers: undefined });
  });

  it('Read more opens the article inside the sheet, and Back comes home', async () => {
    serve();
    const docsPage = vi.fn();
    window.navigateToDocsSection = docsPage;
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    const row = within(dialog).getByRole('button', { name: /Download Manager/ });
    fireEvent.click(row);
    const top = useHelpStore.getState().stack.at(-1);
    expect(top?.view).toBe('article');
    expect(top?.params?.id).toBeTruthy();
    expect(useHelpStore.getState().open).toBe(true);
    // never off to the Help & Docs page
    expect(docsPage).not.toHaveBeenCalled();
    act(() => useHelpStore.getState().back());
    expect(useHelpStore.getState().stack.at(-1)?.view).toBe('home');
    delete window.navigateToDocsSection;
  });

  it('Settings names the tab you are on, and follows it', async () => {
    serve();
    window.history.replaceState(null, '', '/settings');
    pageTabs.reset();
    act(() => pageTabs.show('downloads'));
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    const here = within(dialog).getByRole('region', { name: 'On this page: Settings, Downloads' });
    expect(within(here).getByRole('heading', { name: 'Settings, Downloads' })).toBeInTheDocument();
    expect(here).toHaveTextContent(
      'Where downloads come from, in what order, and what gets skipped.',
    );
    act(() => pageTabs.show('advanced'));
    expect(
      within(dialog).getByRole('region', { name: 'On this page: Settings, System' }),
    ).toBeInTheDocument();
    pageTabs.reset();
  });

  it('a member on Settings keeps the plain heading', async () => {
    serve();
    window.history.replaceState(null, '', '/settings');
    pageTabs.reset();
    act(() => pageTabs.show('downloads'));
    mount(MEMBER);
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    expect(
      within(dialog).getByRole('region', { name: 'On this page: Settings' }),
    ).toHaveTextContent('Your own accounts, sign-in and what you see.');
    pageTabs.reset();
  });

  it('What’s new carries the version chip until it is opened', async () => {
    serve();
    mount();
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    const tile = await within(dialog).findByRole('button', { name: /What's new.*3\.6\.7 is new/ });
    fireEvent.click(tile);
    expect(localStorage.getItem('soulsync_helper_version_seen')).toBe('3.6.7');
  });
});

describe('the first Health run', () => {
  it('says it is checking, never that everything is running, until the run lands', async () => {
    server.use(
      http.get('*/api/help/status', () =>
        HttpResponse.json({ errors: 0, checked_at: null, refreshing: true }),
      ),
      http.get('*/api/help/health', () => HttpResponse.json({ error: 'nope' }, { status: 500 })),
      http.get('*/api/update-check', () => HttpResponse.json({})),
    );
    mount(ADMIN);
    act(() => window.SoulSyncHelp!.open());
    const dialog = await screen.findByRole('dialog', { name: 'Help' });
    await waitFor(() => expect(useHelpStore.getState().status?.refreshing).toBe(true));
    expect(within(dialog).getByRole('button', { name: /Checking SoulSync/ })).toBeInTheDocument();
    expect(within(dialog).queryByText('Everything is running')).toBeNull();
  });
});

describe("the status card's sentence", () => {
  it('keeps a name capitalised mid sentence, and lowers a plain word', () => {
    expect(sentence(["Download folder isn't writable", "Jellyfin isn't answering"])).toBe(
      "Download folder isn't writable, and Jellyfin isn't answering.",
    );
    expect(sentence(["Jellyfin isn't answering", "Download folder isn't writable"])).toBe(
      "Jellyfin isn't answering, and download folder isn't writable.",
    );
    expect(
      sentence(['Spotify is rate limited', 'Last.fm enrichment is paused', 'Disk is full']),
    ).toBe('Spotify is rate limited, Last.fm enrichment is paused, and 1 more.');
  });
});
