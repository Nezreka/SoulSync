import { createMemoryHistory } from '@tanstack/react-router';
import { cleanup, render, screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
/**
 * Anchors that cannot rot (plan 6.1). Every data-tour a tour or What's new
 * names must be written by its owner, and every data-tour in the code must
 * be one somebody names:
 *
 *   - the React pages a tour visits are rendered for real (the Search and
 *     Downloads routes, MSW fixtures as their route tests do)
 *   - the shell and the video side are parsed from index.html
 *   - the download window, built from a template at runtime, is read from
 *     shared-helpers.js
 *
 * Renaming a component, or dropping an attribute in a redesign, fails here.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { resetPersistedSearch } from '@/routes/search/-search.use-controller';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { RELEASES } from '../release-notes';
import { ANCHORS, ANCHOR_IDS, type AnchorId } from './anchors';
import { TOURS } from './tours';

const WEBUI = process.cwd();
const read = (file: string) => readFileSync(resolve(WEBUI, file), 'utf8');

const byCheck = (check: 'html' | 'source' | 'render') =>
  ANCHOR_IDS.filter((id) => ANCHORS[id].check === check);

/** what names an anchor: tour steps, their advanceOn, and What's new's Show me */
function namedAnchors(): Set<string> {
  const named = new Set<string>();
  for (const tour of TOURS) {
    for (const step of tour.steps) {
      named.add(step.anchor);
      if (step.advanceOn) named.add(step.advanceOn);
    }
  }
  for (const release of RELEASES)
    for (const item of release.items) if (item.anchor) named.add(item.anchor);
  return named;
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name === '__fixtures__') continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.(tsx?|jsx?|html)$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

/** every data-tour value written anywhere in the app's code */
function anchorsInCode(): Map<string, string[]> {
  const found = new Map<string, string[]>();
  const files = [
    resolve(WEBUI, 'index.html'),
    ...walk(resolve(WEBUI, 'static')),
    ...walk(resolve(WEBUI, 'src')),
  ];
  for (const file of files) {
    for (const m of readFileSync(file, 'utf8').matchAll(/data-tour="([^"$]+)"/g)) {
      found.set(m[1], [...(found.get(m[1]) ?? []), file]);
    }
  }
  return found;
}

describe('the registry', () => {
  it('names every anchor a tour step or What’s new uses', () => {
    for (const id of namedAnchors()) expect(ANCHOR_IDS, id).toContain(id as AnchorId);
  });

  it('has no anchor nobody uses', () => {
    const named = namedAnchors();
    expect(ANCHOR_IDS.filter((id) => !named.has(id))).toEqual([]);
  });

  it('every data-tour in the code is in the registry, written by the file it names', () => {
    const inCode = anchorsInCode();
    expect([...inCode.keys()].filter((id) => !(id in ANCHORS))).toEqual([]);
    for (const id of ANCHOR_IDS) {
      expect(read(ANCHORS[id].file), `${id} in ${ANCHORS[id].file}`).toContain(`data-tour="${id}"`);
    }
  });

  it('the three tours are the approved three, every step with a title and a body', () => {
    expect(TOURS.map((t) => t.id)).toEqual([
      'getting-around',
      'first-download',
      'first-video-request',
    ]);
    for (const tour of TOURS) {
      expect(tour.steps.length).toBeGreaterThan(2);
      // the first step is something every viewer has, so a tour never opens on nothing
      for (const step of tour.steps) {
        expect(step.title).toBeTruthy();
        expect(step.body).toBeTruthy();
      }
    }
  });
});

describe('the shell and the video side (index.html)', () => {
  const doc = new DOMParser().parseFromString(read('index.html'), 'text/html');

  it.each(byCheck('html'))('%s is in index.html', (id) => {
    expect(doc.querySelector(`[data-tour="${id}"]`), id).not.toBeNull();
  });

  it('marks both sidebars, and both title pages’ buttons', () => {
    expect(doc.querySelectorAll('[data-tour="shell.nav"]')).toHaveLength(2);
    expect(doc.querySelector('.video-nav[data-tour="shell.nav"]')).not.toBeNull();
    const actions = doc.querySelectorAll('[data-tour="video.title-actions"]');
    expect(
      [...actions].map((a) =>
        a.closest('[data-video-subpage]')?.getAttribute('data-video-subpage'),
      ),
    ).toEqual(['video-show-detail', 'video-movie-detail']);
  });

  it('puts each video page anchor on its own page', () => {
    const page = (id: string) =>
      doc
        .querySelector(`[data-tour="${id}"]`)
        ?.closest('[data-video-subpage]')
        ?.getAttribute('data-video-subpage');
    expect(page('video.search-field')).toBe('video-search');
    expect(page('video.search-modes')).toBe('video-search');
    expect(page('video.search-results')).toBe('video-search');
    expect(page('video.requests')).toBe('video-requests');
  });
});

describe('the download window (shared-helpers.js)', () => {
  it.each(byCheck('source'))('%s is in its template', (id) => {
    expect(read(ANCHORS[id].file)).toContain(`data-tour="${id}"`);
  });

  it('marks the album and track window the Search page opens, and its Begin Analysis button', () => {
    const js = read('static/shared-helpers.js');
    const fn = js.slice(js.indexOf('async function openDownloadMissingModalForArtistAlbum'));
    const body = fn.slice(0, fn.indexOf('\nasync function ', 10));
    expect(body).toContain('data-tour="download.window"');
    expect(body).toMatch(
      /id="begin-analysis-btn-\$\{virtualPlaylistId\}" data-tour="download.start"/,
    );
  });
});

describe('the React pages, rendered', () => {
  function renderRoute(path: string) {
    const queryClient = createTestQueryClient();
    const history = createMemoryHistory({ initialEntries: [path] });
    const router = createAppRouter({ history, queryClient });
    return render(<AppRouterProvider router={router} queryClient={queryClient} />);
  }

  beforeEach(() => {
    resetPersistedSearch();
    window.SoulSyncWebShellBridge = createShellBridge();
    server.use(
      http.get('/status', () => HttpResponse.json({ metadata_source: { source: 'spotify' } })),
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'soulseek',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
        }),
      ),
      http.post('/api/search', () => HttpResponse.json({ results: [] })),
      http.get('/api/settings/config-status', () =>
        HttpResponse.json({ spotify: { configured: true }, soulseek: { configured: true } }),
      ),
      http.get('/api/downloads/all', () =>
        HttpResponse.json({ success: true, downloads: [], batches: [] }),
      ),
      http.get('/api/downloads/batch-history', () =>
        HttpResponse.json({ success: true, history: [] }),
      ),
      http.get('/api/verification/config', () =>
        HttpResponse.json({ success: true, acoustid_enabled: true, require_verified: false }),
      ),
      http.get('/api/quarantine/list', () => HttpResponse.json({ success: true, entries: [] })),
    );
  });

  afterEach(() => {
    cleanup();
    document.querySelectorAll('#search-page').forEach((n) => n.remove());
    delete window._searchPageSetQuery;
  });

  it('Search writes the first download tour’s anchors', async () => {
    renderRoute('/search');
    await screen.findByText('Find any artist, album or track, then download it tagged and filed');
    const ids = byCheck('render').filter((id) => id.startsWith('search.'));
    expect(ids).toEqual(['search.field', 'search.modes', 'search.source', 'search.results']);
    for (const id of ids) expect(document.querySelector(`[data-tour="${id}"]`), id).not.toBeNull();
    // the field is the search box itself, not something near it
    expect(
      document.querySelector('[data-tour="search.field"] input#enhanced-search-input'),
    ).not.toBeNull();
  });

  it('Downloads writes its header anchor, on the music and the video view', async () => {
    renderRoute('/active-downloads');
    await screen.findByRole('heading', { level: 1, name: 'Downloads' });
    expect(document.querySelector('[data-tour="downloads.header"]')).not.toBeNull();
    // the video view builds the same header
    expect(read('src/routes/active-downloads/-ui/vdl-header.tsx')).toContain('<DlHeader');
  });
});
