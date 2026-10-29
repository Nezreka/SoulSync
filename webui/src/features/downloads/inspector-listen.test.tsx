import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from '../../test/vanilla-extract';
import { CandidateInspectorModal } from './inspector-modal';

/** #1911: hear a hit before taking it. */

function ndjson(lines: unknown[]) {
  return new Response(lines.map((l) => `${JSON.stringify(l)}\n`).join(''));
}

const accepted = { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.8 };

const SOUNDCLOUD_BEST = {
  display_name: 'Andrew Bundy - Christmas in July',
  filename: '111||https://soundcloud.com/ab/christmas-in-july||Andrew Bundy - Christmas in July',
  username: 'soundcloud',
  confidence: 0.8,
  size_display: '0.0 MB',
  quality: 'mp3',
  decision: accepted,
};
const SOUNDCLOUD_OTHER = {
  ...SOUNDCLOUD_BEST,
  display_name: 'Andrew Bundy - Christmas in July (slowed)',
  filename: '222||https://soundcloud.com/x/slowed||Andrew Bundy - Christmas in July (slowed)',
  confidence: 0.68,
  decision: { ...accepted, score: 0.68 },
};
const PEER_WMA = {
  display_name: 'Christmas in July.wma',
  filename: 'music/Christmas in July.wma',
  username: 'oldpeer',
  confidence: 0.5,
  size_display: '4 MB',
  decision: { ...accepted, score: 0.5 },
};
const TORRENT = {
  display_name: 'Christmas in July [FLAC]',
  filename: 't||1',
  username: 'torrent',
  confidence: 0.9,
  size_display: '400 MB',
  decision: { ...accepted, score: 0.9 },
};
const REJECTED = {
  display_name: 'Andrew Bundy - Christmas in July (Live)',
  filename: '333||Andrew Bundy - Christmas in July (Live)',
  username: 'youtube',
  confidence: 0.3,
  size_display: '5 MB',
  decision: {
    accepted: false,
    code: 'version_conflict',
    stage: 'version',
    detail: 'live',
    score: 0.3,
  },
};

const startStream = vi.fn(async (_result: Record<string, unknown>) => {});

function renderInspector(...lines: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ndjson([...lines, { done: true }])),
  );
  window.startStream = startStream as unknown as typeof window.startStream;
  window.showToast = vi.fn();
  render(
    <CandidateInspectorModal
      target={{
        name: 'Christmas in July',
        artist: 'Andrew Bundy',
        album: 'Christmas in July',
        searchUrl: '/api/wishlist/inspect',
        searchBody: { track_id: 'w1' },
        onGrab: vi.fn(async () => {}),
      }}
      onClose={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  startStream.mockClear();
  Reflect.deleteProperty(window, 'startStream');
  delete window.showToast;
});

describe('Listen in the inspector', () => {
  it('plays the row it sits on, through the stream player, and does not pick it', async () => {
    renderInspector({ source: 'soundcloud', candidates: [SOUNDCLOUD_BEST, SOUNDCLOUD_OTHER] });
    await screen.findByText('Andrew Bundy - Christmas in July (slowed)');
    const radios = document.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    expect(radios[0].checked).toBe(true);

    fireEvent.click(screen.getByLabelText('Listen to Andrew Bundy - Christmas in July (slowed)'));
    await vi.waitFor(() => expect(startStream).toHaveBeenCalledTimes(1));
    expect(startStream.mock.calls[0][0]).toMatchObject({
      username: 'soundcloud',
      filename: SOUNDCLOUD_OTHER.filename,
      title: 'Andrew Bundy - Christmas in July (slowed)',
      artist: 'Andrew Bundy',
      album: 'Christmas in July',
    });
    // listening is not choosing: the best row is still the pick
    expect(radios[0].checked).toBe(true);
    expect(radios[1].checked).toBe(false);
  });

  it('a whole release has nothing to play', async () => {
    renderInspector({ source: 'torrent', candidates: [TORRENT] });
    await screen.findByText('Christmas in July [FLAC]');
    expect(document.querySelector('.rdl-src-listen')).toBeNull();
  });

  it("says so when a peer's file won't play in the browser, instead of starting it", async () => {
    window.isAudioFormatSupported = () => false;
    window.getFileExtension = () => 'wma';
    renderInspector({ source: 'soulseek', candidates: [PEER_WMA] });
    await screen.findByText('Christmas in July.wma');
    fireEvent.click(screen.getByLabelText('Listen to Christmas in July.wma'));
    await vi.waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        "WMA won't play in the browser. Pick it to download instead.",
        'error',
      ),
    );
    expect(startStream).not.toHaveBeenCalled();
    delete window.isAudioFormatSupported;
    delete window.getFileExtension;
  });

  it('a rejected hit can be heard too, before grabbing it anyway', async () => {
    renderInspector({
      source: 'youtube',
      candidates: [],
      rejected: [REJECTED],
      rejected_total: 1,
      rejected_counts: { version_conflict: 1 },
    });
    fireEvent.click(await screen.findByText('Show 1 rejected'));
    fireEvent.click(screen.getByLabelText('Listen to Andrew Bundy - Christmas in July (Live)'));
    await vi.waitFor(() => expect(startStream).toHaveBeenCalledTimes(1));
    expect(startStream.mock.calls[0][0]).toMatchObject({ username: 'youtube' });
  });
});

describe("the player's title for a streaming key", () => {
  const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
  const extractTrackTitle = new Function(
    `${extractFunction('extractTrackTitle', source)};return extractTrackTitle;`,
  )() as (filename: string) => string;

  it("drops soundcloud's permalink, not just its id", () => {
    expect(extractTrackTitle(SOUNDCLOUD_BEST.filename)).toBe('Andrew Bundy - Christmas in July');
  });

  it('leaves the two-part keys and peer paths as they were', () => {
    expect(extractTrackTitle('333||Andrew Bundy - Christmas in July Live')).toBe(
      'Andrew Bundy - Christmas in July Live',
    );
    expect(extractTrackTitle('music/Andrew Bundy - Christmas in July.flac')).toBe(
      'Andrew Bundy - Christmas in July',
    );
  });
});
