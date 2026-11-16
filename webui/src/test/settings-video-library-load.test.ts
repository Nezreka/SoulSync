import { waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

/**
 * The shared video Library load, for the Organization card's Video panel
 * (still video-settings.js). The Folders card's Video panel is React and
 * loads itself: library-video-folders.test.tsx has its cases (SETTINGS_PORT.md
 * video pass V2a).
 */

const source = readFileSync(resolve(process.cwd(), 'static/video/video-settings.js'), 'utf8');
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
function setup() {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const fetcher = vi.fn(
    async (_url: string) =>
      new Response(
        JSON.stringify({
          movie_template: '$title ($year)/$title',
          episode_template: '$series/$episodetitle',
        }),
      ),
  );
  new Function('document', 'window', 'fetch', source)(doc, {}, fetcher);
  doc.dispatchEvent(new Event('DOMContentLoaded'));
  return {
    doc,
    fetcher,
    show: () => doc.dispatchEvent(new CustomEvent('soulsync:library-settings-shown')),
  };
}
const template = (doc: Document) => doc.getElementById('vo-movie-template') as HTMLInputElement;

describe('shared video Library initialization', () => {
  it('loads the organization once, on a fresh music-side Library visit', async () => {
    const { doc, fetcher, show } = setup();
    expect(fetcher).not.toHaveBeenCalled();
    show();
    show();
    expect(template(doc).disabled).toBe(true);
    await waitFor(() => expect(template(doc).disabled).toBe(false));
    expect(template(doc).value).toBe('$title ($year)/$title');
    // the organization, and the Whisper model status it fills in
    expect(fetcher.mock.calls.map((c) => String(c[0]))).toEqual([
      '/api/video/organization',
      '/api/video/subtitles/whisper/status',
    ]);
    template(doc).value = '/unsaved';
    show();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(template(doc).value).toBe('/unsaved');
  });

  it('keeps blank controls disabled after a failed load and allows retry', async () => {
    const { doc, fetcher, show } = setup();
    fetcher.mockResolvedValueOnce(new Response('', { status: 503 }));
    show();
    await waitFor(() => expect(doc.querySelector('.stg-library-retry')).not.toBeNull());
    expect(template(doc).disabled).toBe(true);
    (doc.querySelector('.stg-library-retry') as HTMLButtonElement).click();
    await waitFor(() => expect(template(doc).disabled).toBe(false));
    expect(template(doc).value).toBe('$title ($year)/$title');
  });
});
