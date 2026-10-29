import { waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'static/folder-check.js'), 'utf8');

type Check = { refresh: (group: string) => void };

function setup(results: Record<string, unknown>) {
  document.body.innerHTML = `
    <input id="dl" data-folder-check="g" data-folder-role="input" data-folder-label="Download folder" value="/downloads">
    <div class="path-input-group"><input id="lib" data-folder-check="g" data-folder-label="Library" value="/downloads/lib"></div>
    <input id="tv" data-folder-check="g" data-folder-label="TV" value="/tv">
    <div data-folder-optional hidden><input id="gone" data-folder-check="g" value="/x"></div>
    <input id="blank" data-folder-check="g" value="">`;
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ results })));
  vi.stubGlobal('fetch', fetcher);
  new Function(source)();
  const check = (window as unknown as { SoulSyncFolderCheck: Check }).SoulSyncFolderCheck;
  return { fetcher, check };
}

function line(id: string) {
  const input = document.getElementById(id)!;
  const anchor = input.closest('.path-input-group') ?? input;
  return anchor.nextElementSibling as HTMLElement | null;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

it('checks a group together and words each result', async () => {
  const { fetcher, check } = setup({
    dl: { state: 'ok', free_bytes: 2 * 1024 ** 4, resolved: '/downloads' },
    lib: { state: 'ok', free_bytes: 1, resolved: '/downloads/lib', inside: 'dl' },
    tv: { state: 'problem', message: "SoulSync can't write here", resolved: '/tv' },
  });
  check.refresh('g');
  await waitFor(() => expect(line('dl')?.textContent).toBe('Ready · 2.0 TB free'));
  const sent = JSON.parse(
    (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string,
  );
  // switched-off and blank rows are not sent
  expect(sent.paths).toEqual({ dl: '/downloads', lib: '/downloads/lib', tv: '/tv' });
  expect(line('lib')?.dataset.tone).toBe('warn');
  expect(line('lib')?.textContent).toContain('Inside your download folder');
  expect(line('lib')?.classList.contains('folder-check')).toBe(true);
  expect(line('tv')?.dataset.tone).toBe('bad');
});

it('notes a docker folder on no mapped volume and a folder still to be created', async () => {
  const { check } = setup({
    dl: { state: 'create', free_bytes: 50 * 1024 ** 3, resolved: '/downloads' },
    tv: { state: 'ok', free_bytes: null, resolved: '/tv', unmapped: true },
  });
  check.refresh('g');
  await waitFor(() => expect(line('dl')?.dataset.tone).toBe('info'));
  expect(line('dl')?.textContent).toContain('50.0 GB free');
  expect(line('tv')?.textContent).toContain('Not on a mapped volume');
});

it('a failed check shows nothing rather than an error', async () => {
  const { fetcher, check } = setup({});
  fetcher.mockResolvedValueOnce(new Response('', { status: 403 }));
  check.refresh('g');
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  await new Promise((done) => setTimeout(done, 20));
  expect(document.querySelectorAll('.folder-check:not([hidden])').length).toBe(0);
});

it('puts the line after a flex row, never beside the row button', async () => {
  document.body.innerHTML = `
    <div id="host"><div data-folder-row style="display:flex"><input id="x" data-folder-check="r" value="/music2"><button>×</button></div></div>`;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ results: { x: { state: 'ok', resolved: '/music2' } } })),
    ),
  );
  new Function(source)();
  (window as unknown as { SoulSyncFolderCheck: Check }).SoulSyncFolderCheck.refresh('r');
  const row = document.querySelector('[data-folder-row]')!;
  await waitFor(() =>
    expect(row.nextElementSibling?.classList.contains('folder-check')).toBe(true),
  );
  expect(row.querySelector('.folder-check')).toBeNull();
  expect(row.nextElementSibling?.textContent).toBe('Ready');
});

it('a missing extra folder is a warning, not "will be created"', async () => {
  document.body.innerHTML = `<input id="e" data-folder-check="x" data-folder-role="existing" value="/mnt/drive2">`;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ results: { e: { state: 'create', resolved: '/mnt/drive2' } } }),
        ),
    ),
  );
  new Function(source)();
  (window as unknown as { SoulSyncFolderCheck: Check }).SoulSyncFolderCheck.refresh('x');
  const input = document.getElementById('e')!;
  await waitFor(() => expect((input.nextElementSibling as HTMLElement)?.dataset.tone).toBe('warn'));
  expect(input.nextElementSibling?.textContent).toContain('Not found');
});
