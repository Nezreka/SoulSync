import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchVideo } from '../-search.types';
import type { VideoProgress } from './video-grid';

import { VideoGrid } from './video-grid';

const video = (over: Partial<SearchVideo> = {}): SearchVideo => ({
  video_id: 'v1',
  title: 'Windowlicker',
  channel: 'Warp',
  thumbnail: 'https://i.ytimg.com/v1.jpg',
  duration: 215,
  view_count: 1_500_000,
  ...over,
});

function renderGrid(videos: SearchVideo[], progress: Record<string, VideoProgress> = {}) {
  const onDownload = vi.fn();
  render(<VideoGrid videos={videos} progress={progress} onDownload={onDownload} />);
  return { onDownload };
}

const card = () => document.querySelector('[data-video-id]') as HTMLElement;
const ring = () => document.querySelector('.enh-video-progress-bar') as SVGCircleElement;

afterEach(cleanup);

describe('VideoGrid', () => {
  it('renders a card with its duration in SECONDS and its view count', () => {
    // The unit trap: track durations are milliseconds, video durations seconds.
    // Read as ms, 215 would render 0:00.
    renderGrid([video()]);
    expect(screen.getByText('3:35')).toBeInTheDocument();
    expect(screen.getByText('Warp · 1.5M views')).toBeInTheDocument();
  });

  it('heads its section with a count', () => {
    renderGrid([video()]);
    expect(screen.getByRole('heading', { name: /Music videos/ })).toBeInTheDocument();
    expect(document.getElementById('enh-videos-count')?.textContent).toBe('1');
  });

  it('omits the duration chip rather than printing an empty one', () => {
    renderGrid([video({ duration: undefined })]);
    expect(screen.queryByText(/^\d+:\d\d$/)).toBeNull();
  });

  it('shows an empty state when the stream returned nothing', () => {
    renderGrid([]);
    expect(screen.getByText('No music videos found.')).toBeInTheDocument();
    expect(document.getElementById('enh-videos-count')?.textContent).toBe('0');
  });

  it('downloads the whole video object from a real button', () => {
    const item = video();
    const { onDownload } = renderGrid([item]);

    // a real <button>, so Enter and Space come from the browser for free
    expect(card().tagName).toBe('BUTTON');
    expect(card()).toHaveAccessibleName('Download Windowlicker');
    fireEvent.click(card());
    // The whole object, not a serialised copy — a title with a quote in it used
    // to break the vanilla's inline onclick attribute.
    expect(onDownload).toHaveBeenCalledWith(item);
  });

  it('hides the ring, tick and cross while idle', () => {
    renderGrid([video()]);
    expect(document.querySelector('.enh-video-progress-ring')?.className).toContain('hidden');
    expect(document.querySelector('.enh-video-done')?.className).toContain('hidden');
    expect(document.querySelector('.enh-video-error')?.className).toContain('hidden');
    expect(card().dataset.state).toBe('idle');
  });

  it('fills the ring as the download progresses', () => {
    // strokeDashoffset counts DOWN: the full circumference is an empty ring.
    renderGrid([video()], { v1: { state: 'downloading', percent: 0 } });
    expect(Number(ring().getAttribute('stroke-dashoffset'))).toBeCloseTo(97.4);
    expect(document.querySelector('.enh-video-progress-ring')?.className).not.toContain('hidden');
    expect(card().dataset.state).toBe('downloading');

    cleanup();
    renderGrid([video()], { v1: { state: 'downloading', percent: 50 } });
    expect(Number(ring().getAttribute('stroke-dashoffset'))).toBeCloseTo(48.7);

    cleanup();
    renderGrid([video()], { v1: { state: 'downloading', percent: 100 } });
    expect(Number(ring().getAttribute('stroke-dashoffset'))).toBeCloseTo(0);
  });

  it('clamps an over-100 percent instead of overshooting the ring', () => {
    renderGrid([video()], { v1: { state: 'downloading', percent: 140 } });
    expect(Number(ring().getAttribute('stroke-dashoffset'))).toBeCloseTo(0);
  });

  it('shows the tick when complete and the cross when it failed', () => {
    renderGrid([video()], { v1: { state: 'completed', percent: 100 } });
    expect(document.querySelector('.enh-video-done')?.className).not.toContain('hidden');
    expect(card().dataset.state).toBe('completed');

    cleanup();
    renderGrid([video()], { v1: { state: 'errored', percent: 12 } });
    expect(document.querySelector('.enh-video-error')?.className).not.toContain('hidden');
    expect(card().dataset.state).toBe('errored');
  });

  it('tracks progress per video, not across the grid', () => {
    renderGrid([video({ video_id: 'v1' }), video({ video_id: 'v2', title: 'Second' })], {
      v2: { state: 'downloading', percent: 25 },
    });
    const cards = document.querySelectorAll<HTMLElement>('[data-video-id]');
    expect(cards[0].dataset.state).toBe('idle');
    expect(cards[1].dataset.state).toBe('downloading');
  });

  it('drops a thumbnail that fails to load', () => {
    renderGrid([video()]);
    const img = card().querySelector('img') as HTMLImageElement;
    fireEvent.error(img);
    expect(img.style.display).toBe('none');
  });
});
