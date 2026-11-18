/**
 * Import answers, from /api/import/inbox the way api/import_routes.py sends
 * it: items held for review (with confidence and how it was identified),
 * ones it couldn't identify, a failure, new drops with auto-import off and
 * on, folders it couldn't list, a scan still reading; nothing for an empty
 * folder; a member reads the same why without the switch.
 */

import { fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ImportInboxItem, ImportInboxPayload } from '@/routes/import/-import.types';

import { HttpResponse, http, server } from '@/test/msw';

import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { importAnswers, useImportAnswers } from './import';
import { pageHelpFor } from './index';

const item = (over: Partial<ImportInboxItem>): ImportInboxItem => ({
  key: 'k',
  kind: 'album',
  name: 'Mezzanine',
  guessed_name: null,
  artist: 'Massive Attack',
  folder_name: 'Massive Attack - Mezzanine (1998)',
  folder_path: '/import/Massive Attack - Mezzanine (1998)',
  rel_path: 'Massive Attack - Mezzanine (1998)',
  in_staging: true,
  files: [],
  file_count: 11,
  total_duration_ms: 3_808_000,
  total_size: 412_000_000,
  formats: ['FLAC'],
  status: 'waiting',
  confidence: null,
  match: null,
  history_id: null,
  live: null,
  ...over,
});

const REVIEW = item({
  key: 'r',
  status: 'needs_review',
  confidence: 0.78,
  identification_method: 'tags',
  match: { matched_count: 10, total_tracks: 11, matches: [] },
});
const UNKNOWN = item({
  key: 'u',
  name: 'Track 01',
  folder_name: 'rips',
  status: 'needs_identify',
  error_message: 'No tags and no fingerprint match',
});
const FAILED = item({
  key: 'f',
  name: 'Blue Lines',
  status: 'failed',
  error_message: 'disk full while moving files',
});
const WAITING = item({ key: 'w', name: 'Protection', status: 'waiting' });
const IMPORTED = item({ key: 'i', name: 'Heligoland', status: 'imported' });

const payload = (over: Partial<ImportInboxPayload>): ImportInboxPayload => ({
  success: true,
  staging_path: '/import',
  items: [],
  worker: { available: true, running: true, paused: false, current_status: 'idle', stats: {} },
  ...over,
});

let inbox = payload({});
beforeEach(() => {
  inbox = payload({});
  asProfile(1, true);
  server.use(http.get('/api/import/inbox', () => HttpResponse.json(inbox)));
});
afterEach(() => {
  delete window.getCurrentProfileContext;
});

describe('import answers', () => {
  it('registers on the import page', () => {
    expect(pageHelpFor('import').useAnswers).toBe(useImportAnswers);
  });

  it('an empty or settled folder says nothing', async () => {
    inbox = payload({ items: [IMPORTED] });
    const { container } = renderAnswers(useImportAnswers);
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
  });

  it('says why each kind of item is held, review first', async () => {
    inbox = payload({ items: [UNKNOWN, FAILED, REVIEW] });
    const { container } = renderAnswers(useImportAnswers);
    await waitFor(() =>
      expect(titles(container)).toEqual([
        'Why is Mezzanine waiting for me?',
        "Why couldn't SoulSync tell what Track 01 is?",
        "Why didn't Blue Lines finish?",
      ]),
    );
    expect(answer(container, 'needs-review').textContent).toContain(
      '78% sure · tags · 10 of 11 tracks matched',
    );
    expect(answer(container, 'needs-identify').textContent).toContain(
      'No tags and no fingerprint match',
    );
    expect(answer(container, 'failed').textContent).toContain('Disk full while moving files.');
  });

  it('new drops with auto-import off: says so and flips the page switch', async () => {
    inbox = payload({
      items: [WAITING],
      worker: { available: true, running: false, paused: false, current_status: 'idle', stats: {} },
    });
    document.body.insertAdjacentHTML(
      'beforeend',
      '<button id="auto-import-enabled">switch</button>',
    );
    const flip = vi.fn();
    document.getElementById('auto-import-enabled')!.addEventListener('click', flip);
    const { container } = renderAnswers(useImportAnswers);
    await waitFor(() => expect(titles(container)).toEqual(["Why isn't Protection importing?"]));
    fireEvent.click(
      within(answer(container, 'auto-off')).getByRole('button', { name: 'Turn on auto-import' }),
    );
    expect(flip).toHaveBeenCalled();
    document.getElementById('auto-import-enabled')?.remove();
  });

  it('new drops with auto-import on wait for the next scan', () => {
    const out = importAnswers(payload({ items: [WAITING] }), { page: 'import', isAdmin: true });
    expect(out.map((a) => a.title)).toEqual(['When will Protection be imported?']);
  });

  it("names a folder it couldn't read", async () => {
    inbox = payload({ problems: [{ path: '/import/locked', error: 'Permission denied' }] });
    const { container } = renderAnswers(useImportAnswers);
    await waitFor(() =>
      expect(titles(container)).toEqual(['Why are some files missing from the list?']),
    );
    const a = answer(container, 'unreadable');
    expect(a.dataset.tone).toBe('warn');
    expect(a.textContent).toContain('/import/locked');
    expect(a.textContent).toContain('Permission denied');
  });

  it('a scan still reading shows its progress', () => {
    const out = importAnswers(
      payload({ scanning: true, progress: { scanned: 1240, total: 3000 } }),
      {
        page: 'import',
        isAdmin: true,
      },
    );
    expect(out[0].title).toBe('Why is the list still filling in?');
  });

  it('a member reads the why without the switch or the filter', async () => {
    asProfile(4, false);
    inbox = payload({
      items: [REVIEW, WAITING],
      worker: { available: true, running: false, paused: false, current_status: 'idle', stats: {} },
    });
    const { container } = renderAnswers(useImportAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toHaveLength(2));
    expect(container.querySelectorAll('button')).toHaveLength(0);
  });
});
