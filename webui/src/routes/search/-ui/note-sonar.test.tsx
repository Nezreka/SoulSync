import { QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { DISCLOSURE, NoteSonar, itemBoxArgs } from './note-sonar';

type Answer = { status: number; body: unknown };

let identify: (form: FormData) => Promise<Answer>;
let posted: FormData[];

function useEngine(available: boolean) {
  server.use(
    http.get('/api/listen/status', () =>
      HttpResponse.json({ success: true, available, reason: 'ffmpeg is missing' }),
    ),
  );
}

function renderSonar(onSearch = vi.fn()) {
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <NoteSonar onSearch={onSearch} />
    </QueryClientProvider>,
  );
  return onSearch;
}

const micButton = () => screen.findByRole('button', { name: 'Identify the song playing nearby' });

function pickFile(name = 'clip.m4a') {
  const input = screen.getByLabelText('Audio clip') as HTMLInputElement;
  const file = new File(['not really audio'], name, { type: 'audio/mp4' });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

const IDENTIFIED = {
  success: true,
  identification: {
    title: 'Teardrop',
    artist: 'Massive Attack',
    album: 'Mezzanine',
    cover_url: 'https://img/cover.jpg',
    shazam_id: '12345',
    isrc: 'GBAAA9800001',
    duration_s: 330,
    released: '1998',
  },
  match: null,
  in_library: true,
  query: 'Massive Attack Teardrop',
};

beforeEach(() => {
  posted = [];
  identify = async () => ({ status: 200, body: IDENTIFIED });
  const passThrough = globalThis.fetch;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url.includes('/api/listen/identify')) {
      const form = init?.body as FormData;
      posted.push(form);
      const answer = await identify(form);
      return new Response(JSON.stringify(answer.body), {
        status: answer.status,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return passThrough(input, init);
  });
  window.showToast = vi.fn();
  window.openDownloadMissingModalForArtistAlbum = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete window.showToast;
  delete window.openDownloadMissingModalForArtistAlbum;
});

describe('note sonar', () => {
  it('stays out of the search field while the recogniser is unavailable', async () => {
    useEngine(false);
    renderSonar();
    // give the status answer time to land
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByRole('button', { name: 'Identify the song playing nearby' })).toBeNull();
  });

  it('shows the button when the recogniser is there', async () => {
    useEngine(true);
    renderSonar();
    expect(await micButton()).toBeInTheDocument();
  });

  it('names Shazam before the microphone is asked for', async () => {
    useEngine(true);
    const getUserMedia = vi.fn(() => new Promise<MediaStream>(() => {}));
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    Object.defineProperty(window, 'MediaRecorder', {
      configurable: true,
      writable: true,
      value: class {},
    });
    try {
      renderSonar();
      fireEvent.click(await micButton());
      expect(screen.getByText(DISCLOSURE)).toBeInTheDocument();
      // the status check runs before anything is recorded
      await screen.findByText('Ready when you are.');
      expect(getUserMedia).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: 'Start listening' }));
      await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
      expect(screen.getByText(DISCLOSURE)).toBeInTheDocument();
    } finally {
      delete (window as { isSecureContext?: boolean }).isSecureContext;
      delete (navigator as { mediaDevices?: unknown }).mediaDevices;
      delete (window as { MediaRecorder?: unknown }).MediaRecorder;
    }
  });

  it('says why when the recogniser went away after the button showed', async () => {
    useEngine(true);
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn() },
    });
    Object.defineProperty(window, 'MediaRecorder', {
      configurable: true,
      writable: true,
      value: class {},
    });
    try {
      renderSonar();
      fireEvent.click(await micButton());
      useEngine(false);
      await screen.findAllByText('ffmpeg is missing');
      expect(screen.getByRole('button', { name: 'Upload a clip' })).toBeInTheDocument();
    } finally {
      delete (window as { isSecureContext?: boolean }).isSecureContext;
      delete (navigator as { mediaDevices?: unknown }).mediaDevices;
      delete (window as { MediaRecorder?: unknown }).MediaRecorder;
    }
  });

  it('offers the upload, with the disclosure, when the microphone needs https', async () => {
    useEngine(true);
    renderSonar();
    fireEvent.click(await micButton());
    expect(screen.getByText('The microphone needs a secure address (https).')).toBeInTheDocument();
    expect(screen.getByText(DISCLOSURE)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload a clip' })).toBeInTheDocument();
    expect(posted).toHaveLength(0);
  });

  it('posts an uploaded clip and opens the analysis box for the song', async () => {
    useEngine(true);
    const onSearch = renderSonar();
    fireEvent.click(await micButton());
    const file = pickFile();

    await waitFor(() => expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalled());
    expect(posted).toHaveLength(1);
    const sent = posted[0].get('audio') as File;
    expect(sent.name).toBe(file.name);
    expect(sent.size).toBe(file.size);

    expect(window.showToast).toHaveBeenCalledWith(
      'Identified: Massive Attack — Teardrop (already in your library)',
      'success',
    );
    const image = 'https://img/cover.jpg';
    expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalledWith(
      'enhanced_search_track_shazam_12345',
      'Massive Attack - Teardrop',
      [
        {
          id: 'shazam_12345',
          name: 'Teardrop',
          source: null,
          artists: ['Massive Attack'],
          album: {
            name: 'Mezzanine',
            id: null,
            album_type: 'single',
            images: [{ url: image }],
            release_date: '1998',
            total_tracks: 1,
          },
          duration_ms: 330_000,
          popularity: 0,
          preview_url: null,
          external_urls: null,
          image_url: image,
        },
      ],
      {
        name: 'Mezzanine',
        id: null,
        album_type: 'single',
        images: [{ url: image }],
        release_date: '1998',
        total_tracks: 1,
        artists: [{ name: 'Massive Attack' }],
      },
      { id: null, name: 'Massive Attack' },
      false,
    );
    expect(onSearch).not.toHaveBeenCalled();
    // the sheet is gone
    expect(screen.queryByText(DISCLOSURE)).toBeNull();
  });

  it('builds the box from the catalogue row when there is one', () => {
    const args = itemBoxArgs(IDENTIFIED.identification, {
      id: 'sp1',
      name: 'Teardrop',
      artist: 'Massive Attack',
      artists: [{ name: 'Massive Attack' }],
      album: 'Mezzanine (Deluxe)',
      album_id: 'alb1',
      images: [{ url: 'https://img/row.jpg' }],
      source: 'spotify',
      duration_ms: 329_000,
      release_date: '1998-04-20',
      popularity: 70,
    });
    expect(args?.[0]).toBe('enhanced_search_track_sp1');
    expect(args?.[2][0]).toMatchObject({
      id: 'sp1',
      source: 'spotify',
      artists: [{ name: 'Massive Attack' }],
      album: { name: 'Mezzanine (Deluxe)', id: 'alb1', release_date: '1998-04-20' },
      duration_ms: 329_000,
      popularity: 70,
      image_url: 'https://img/row.jpg',
    });
  });

  it('runs the search when the analysis box cannot open', async () => {
    useEngine(true);
    delete window.openDownloadMissingModalForArtistAlbum;
    const onSearch = renderSonar();
    fireEvent.click(await micButton());
    pickFile();
    await waitFor(() => expect(onSearch).toHaveBeenCalledWith('Massive Attack Teardrop'));
  });

  it("shows the server's own words when it says no", async () => {
    useEngine(true);
    identify = async () => ({
      status: 400,
      body: {
        success: false,
        error: 'undecodable',
        message: 'That clip could not be read as audio.',
      },
    });
    renderSonar();
    fireEvent.click(await micButton());
    pickFile();
    expect(await screen.findByText('That clip could not be read as audio.')).toBeInTheDocument();
    expect(screen.getByText('No identification')).toBeInTheDocument();
    expect(window.openDownloadMissingModalForArtistAlbum).not.toHaveBeenCalled();
  });

  it('says so when nothing matched', async () => {
    useEngine(true);
    identify = async () => ({
      status: 200,
      body: { success: true, identification: null, match: null, message: 'No match.' },
    });
    renderSonar();
    fireEvent.click(await micButton());
    pickFile();
    expect(await screen.findByText('No match.')).toBeInTheDocument();
  });

  it('drops an answer that lands after the sheet was closed', async () => {
    useEngine(true);
    let answer: (value: Answer) => void = () => {};
    identify = () =>
      new Promise<Answer>((resolve) => {
        answer = resolve;
      });
    const onSearch = renderSonar();
    fireEvent.click(await micButton());
    pickFile();
    await screen.findByText('Identifying…');
    await waitFor(() => expect(posted).toHaveLength(1));

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await act(async () => {
      answer({ status: 200, body: IDENTIFIED });
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(window.showToast).not.toHaveBeenCalled();
    expect(window.openDownloadMissingModalForArtistAlbum).not.toHaveBeenCalled();
    expect(onSearch).not.toHaveBeenCalled();
    expect(screen.queryByText(DISCLOSURE)).toBeNull();
  });

  it('closes on Escape', async () => {
    useEngine(true);
    renderSonar();
    fireEvent.click(await micButton());
    expect(screen.getByText(DISCLOSURE)).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText(DISCLOSURE)).toBeNull());
  });
});
