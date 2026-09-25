import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BasicAlbum, BasicResult, BasicTrack, DownloadTarget } from '../-basic.types';

import { BasicResults } from './basic-results';

afterEach(cleanup);

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'peer',
    filename: 'a.flac',
    size: 10 * 1024 * 1024,
    bitrate: 1411,
    duration: 200_000,
    quality: 'flac',
    free_upload_slots: 1,
    upload_speed: 1_000_000,
    queue_length: 0,
    sample_rate: 44_100,
    bit_depth: 16,
    artist: 'Aphex Twin',
    title: 'Xtal',
    album: 'SAW',
    track_number: 1,
    quality_score: 0.9,
    ...over,
  };
}

function album(over: Partial<BasicAlbum> = {}): BasicAlbum {
  return {
    result_type: 'album',
    username: 'peer',
    album_path: '/music/saw',
    album_title: 'Selected Ambient Works',
    artist: 'Aphex Twin',
    track_count: 2,
    total_size: 80 * 1024 * 1024,
    tracks: [track(), track({ title: 'Tha', track_number: 2, filename: 'b.flac' })],
    dominant_quality: 'flac',
    year: '1992',
    free_upload_slots: 1,
    upload_speed: 1_000_000,
    queue_length: 0,
    quality_score: 0.8,
    ...over,
  };
}

function renderResults(results: BasicResult[] = [track()]) {
  const onDownload = vi.fn<(target: DownloadTarget) => void>();
  const view = render(
    <BasicResults results={results} placeholder="Nothing here." onDownload={onDownload} />,
  );
  return { ...view, onDownload };
}

const rows = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>('[data-result-kind]'));

describe('empty', () => {
  it('shows the placeholder as the status line', () => {
    const { container } = renderResults([]);
    expect(container.querySelector('#search-status-text')?.textContent).toBe('Nothing here.');
    expect(container.querySelector('#search-results-area')).not.toBeNull();
  });
});

describe('a track row', () => {
  it('reads title, then artist and album', () => {
    const { container } = renderResults();
    const row = rows(container)[0];
    expect(row.textContent).toContain('Xtal');
    expect(row.textContent).toContain('Aphex Twin');
    expect(row.textContent).toContain('SAW');
  });

  it('badges lossless by depth and rate, not kbps', () => {
    const { container } = renderResults();
    expect(rows(container)[0].textContent).toContain('FLAC 16/44.1');
    expect(rows(container)[0].textContent).not.toContain('1411');
  });

  it('badges lossy by bitrate', () => {
    const { container } = renderResults([
      track({ quality: 'mp3', bitrate: 320, bit_depth: null, sample_rate: null }),
    ]);
    expect(rows(container)[0].textContent).toContain('MP3 320');
  });

  it('shows size and length', () => {
    const { container } = renderResults();
    expect(rows(container)[0].textContent).toContain('10.0 MB');
    expect(rows(container)[0].textContent).toContain('3:20');
  });

  it('keeps the uploader hook chat.js listens for', () => {
    const { container } = renderResults();
    const link = container.querySelector<HTMLElement>('.chat-user-link');
    expect(link?.dataset.chatMsgUser).toBe('peer');
  });

  it('has exactly one action: Download, which asks for the track', () => {
    const { container, onDownload } = renderResults();
    const buttons = within(rows(container)[0]).getAllByRole('button');
    // the uploader link and the download button, nothing else
    expect(buttons.map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual([
      'peer',
      'Download Xtal',
    ]);
    fireEvent.click(within(rows(container)[0]).getByRole('button', { name: 'Download Xtal' }));
    expect(onDownload).toHaveBeenCalledWith({
      kind: 'track',
      track: expect.objectContaining({ title: 'Xtal' }),
    });
  });

  it('no stream and no matched buttons anywhere', () => {
    const { container } = renderResults([track(), album()]);
    expect(container.textContent).not.toMatch(/Stream|Matched/);
  });
});

describe('an album row', () => {
  it('says how many tracks and the year', () => {
    const { container } = renderResults([album()]);
    expect(rows(container)[0].textContent).toContain('2 tracks');
    expect(rows(container)[0].textContent).toContain('1992');
  });

  it('downloads the whole album', () => {
    const a = album();
    const { container, onDownload } = renderResults([a]);
    fireEvent.click(
      within(rows(container)[0]).getByRole('button', { name: 'Download Selected Ambient Works' }),
    );
    expect(onDownload).toHaveBeenCalledWith({ kind: 'album', album: a });
  });

  it('hides its tracks until expanded', () => {
    const { container, getByRole } = renderResults([album()]);
    expect(container.textContent).not.toContain('Tha');
    const toggle = getByRole('button', { name: /Show tracks of/ });
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain('Tha');
  });

  it('a track inside it downloads with the album kept', () => {
    const a = album();
    const { getByRole, onDownload } = renderResults([a]);
    fireEvent.click(getByRole('button', { name: /Show tracks of/ }));
    fireEvent.click(getByRole('button', { name: 'Download Tha' }));
    expect(onDownload).toHaveBeenCalledWith({ kind: 'albumTrack', album: a, trackIndex: 1 });
  });

  it('marks discs when track numbers start over', () => {
    const { container, getByRole } = renderResults([
      album({
        tracks: [
          track({ track_number: 1 }),
          track({ track_number: 2, filename: 'b' }),
          track({ track_number: 1, filename: 'c', title: 'Second disc' }),
        ],
      }),
    ]);
    fireEvent.click(getByRole('button', { name: /Show tracks of/ }));
    expect(container.textContent).toContain('Disc 1');
    expect(container.textContent).toContain('Disc 2');
  });

  it('collapses when the results change underneath', () => {
    const onDownload = vi.fn();
    const first = [album()];
    const view = render(<BasicResults results={first} placeholder="" onDownload={onDownload} />);
    fireEvent.click(view.getByRole('button', { name: /Show tracks of/ }));
    expect(view.container.textContent).toContain('Tha');
    view.rerender(<BasicResults results={[album()]} placeholder="" onDownload={onDownload} />);
    expect(view.container.textContent).not.toContain('Tha');
  });
});
