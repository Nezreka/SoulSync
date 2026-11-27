/**
 * Connections > Give back (#2335): three switches, off until turned on, the
 * MusicBrainz account linked with a pasted code, what was given so far, and
 * Add to MusicBrainz posting the filled-in add release form.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type GiveBack, GiveBackSection, many, openReleaseEditor } from './connections-giveback';

const zero = { submitted: 0, present: 0, skipped: 0, rejected: 0 };
const base: GiveBack = {
  switches: { acoustid: false, isrc: false, releases: false },
  acoustid: { ready: false, message: '' },
  musicbrainz: { available: true, connected: false, username: '', message: '' },
  counts: { acoustid: { ...zero }, isrc: { ...zero } },
  running: false,
  last_at: null,
};

let calls: Array<{ url: string; method: string; body?: string }>;
let answers: Record<string, unknown[]>;
const answer = (key: string, ...a: unknown[]) => (answers[key] = a);

beforeEach(() => {
  calls = [];
  answers = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      calls.push({ url, method, body: init?.body as string | undefined });
      const list = answers[`${method} ${url}`] || [];
      const next = list.length > 1 ? list.shift() : list[0];
      const status = (next as { status?: number } | undefined)?.status ?? 200;
      return Promise.resolve({ ok: status < 400, json: () => Promise.resolve(next ?? {}) });
    }),
  );
  vi.stubGlobal('open', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Give back', () => {
  it('starts with everything off and says what was given', async () => {
    answer('GET /api/giveback', {
      ...base,
      counts: {
        acoustid: { ...zero, submitted: 1234 },
        isrc: { ...zero, submitted: 1, present: 3 },
      },
    });
    const { container } = render(<GiveBackSection opened={0} />);
    await waitFor(() => expect(container.querySelector('#giveback-acoustid')).not.toBeNull());
    for (const id of ['giveback-acoustid', 'giveback-isrc', 'giveback-releases'])
      expect((container.querySelector(`#${id}`) as HTMLInputElement).checked).toBe(false);
    expect(container.querySelector('#giveback-acoustid-count')?.textContent).toBe(
      '1,234 fingerprints',
    );
    expect(container.querySelector('#giveback-isrc-count')?.textContent).toBe(
      '1 ISRC, 3 already there',
    );
    // nothing to send now while both are off
    expect(container.textContent).not.toContain('Send now');
  });

  it('a switch saves through its own endpoint', async () => {
    answer('GET /api/giveback', base);
    answer('PUT /api/giveback/settings', {
      ...base,
      switches: { ...base.switches, acoustid: true },
      acoustid: { ready: false, message: 'link your AcoustID account first' },
    });
    const { container, findByText } = render(<GiveBackSection opened={0} />);
    await waitFor(() => expect(container.querySelector('#giveback-acoustid')).not.toBeNull());
    fireEvent.click(container.querySelector('#giveback-acoustid') as HTMLInputElement);
    await findByText('link your AcoustID account first');
    const put = calls.find((c) => c.method === 'PUT');
    expect(JSON.parse(put?.body || '{}')).toEqual({ acoustid: true });
    expect(container.textContent).toContain('Send now');
  });

  it('links MusicBrainz with the pasted code', async () => {
    answer('GET /api/giveback', base);
    answer('POST /api/giveback/musicbrainz/connect', {
      success: true,
      url: 'https://musicbrainz.org/oauth2/authorize?x=1',
    });
    answer('POST /api/giveback/musicbrainz/code', {
      ...base,
      musicbrainz: { ...base.musicbrainz, connected: true, username: 'someone' },
    });
    const { container, findByText } = render(<GiveBackSection opened={0} />);
    await waitFor(() => expect(container.querySelector('#giveback-mb-connect')).not.toBeNull());
    fireEvent.click(container.querySelector('#giveback-mb-connect') as HTMLButtonElement);
    await waitFor(() => expect(container.querySelector('#giveback-mb-code')).not.toBeNull());
    expect(window.open).toHaveBeenCalledWith(
      'https://musicbrainz.org/oauth2/authorize?x=1',
      '_blank',
      'noopener',
    );
    fireEvent.change(container.querySelector('#giveback-mb-code') as HTMLInputElement, {
      target: { value: ' abc ' },
    });
    fireEvent.click(await findByText('Finish'));
    await findByText('Connected as someone');
    const sent = calls.find((c) => c.url === '/api/giveback/musicbrainz/code');
    expect(JSON.parse(sent?.body || '{}')).toEqual({ code: 'abc' });
  });

  it('says why ISRCs wait when SoulSync is not registered yet', async () => {
    answer('GET /api/giveback', {
      ...base,
      musicbrainz: { available: false, connected: false, username: '', message: 'arrives later' },
    });
    const { container, findByText } = render(<GiveBackSection opened={0} />);
    await findByText('arrives later');
    expect((container.querySelector('#giveback-isrc') as HTMLInputElement).disabled).toBe(true);
    expect(container.querySelector('#giveback-mb-connect')).toBeNull();
  });

  it('lists albums to add once the switch is on', async () => {
    answer('GET /api/giveback', { ...base, switches: { ...base.switches, releases: true } });
    answer('GET /api/giveback/releases', {
      success: true,
      releases: [
        {
          id: 5,
          title: 'New Record',
          artist: 'Artist',
          year: '2024',
          source: 'Spotify',
          source_url: 'https://open.spotify.com/album/x',
        },
      ],
    });
    const { findByText } = render(<GiveBackSection opened={0} />);
    await findByText('New Record');
    await findByText('Add to MusicBrainz');
  });
});

describe('openReleaseEditor', () => {
  it('posts the seeded fields to the release editor', async () => {
    answer('GET /api/giveback/releases/5/seed', {
      success: true,
      action: 'https://musicbrainz.org/release/add',
      fields: [
        ['name', 'New Record'],
        ['mediums.0.track.0.name', 'One'],
        ['mediums.0.track.0.length', '200500'],
      ],
    });
    let posted: HTMLFormElement | null = null;
    const submit = vi
      .spyOn(HTMLFormElement.prototype, 'submit')
      .mockImplementation(function (this: HTMLFormElement) {
        posted = this.cloneNode(true) as HTMLFormElement;
      });
    await act(() => openReleaseEditor(5));
    expect(submit).toHaveBeenCalledTimes(1);
    const form = posted as unknown as HTMLFormElement;
    expect(form.method).toBe('post');
    expect(form.action).toBe('https://musicbrainz.org/release/add');
    expect(form.target).toBe('soulsync-mb-release-add');
    const fields = [...form.querySelectorAll('input')].map((i) => [i.name, i.value]);
    expect(fields).toEqual([
      ['name', 'New Record'],
      ['mediums.0.track.0.name', 'One'],
      ['mediums.0.track.0.length', '200500'],
    ]);
    // the form does not stay in the page
    expect(document.querySelector('form[target="soulsync-mb-release-add"]')).toBeNull();
    submit.mockRestore();
  });

  it('counts in plain words', () => {
    expect(many(0, 'fingerprint')).toBe('0 fingerprints');
    expect(many(1, 'ISRC')).toBe('1 ISRC');
  });
});
