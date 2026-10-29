import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

export const fetchMock = vi.fn();

export function reply(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

export const finding = {
  id: 7,
  job_id: 'library_reconciliation',
  finding_type: 'reconciliation_unresolved',
  severity: 'warning',
  status: 'pending',
  title: 'Choose release: Ashes of the Wake',
  description: 'Multiple releases fit equally well.',
  file_path: null,
  details: {
    album_title: 'Ashes of the Wake',
    artist_name: 'Lamb of God',
    release_disambiguation: 'US deluxe edition',
    album_folder: '/music/Lamb of God/Ashes of the Wake',
    candidates: [
      {
        source: 'musicbrainz',
        release_id: 'release-one',
        image_url: '/api/image-cache/release-one',
        matched: 1,
        clear: 1,
        total_slots: 1,
        missing: [],
        extra: [],
        pairs: [
          {
            file: '01 - Laid to Rest.flac',
            tag_title: 'Laid to Rest',
            file_duration_ms: 240000,
            audio_title: 'Laid to Rest',
            audio_artist: 'Lamb of God',
            audio_score: 0.98,
            audio_alternatives: [
              {
                title: 'Hourglass',
                artist: 'Lamb of God',
                score: 0.98,
                mbid: 'hourglass-id',
              },
            ],
            slot_title: 'Laid to Rest',
            local_recording_disambiguation: 'acoustic',
            slot_recording_disambiguation: 'acoustic',
            slot_number: 1,
            disc_number: 1,
            slot_duration_ms: 240010,
            duration_delta_ms: 10,
            confidence: 'clear',
            reasons: ['AcoustID recording ID matches release slot'],
            ambiguity_reasons: [],
          },
        ],
      },
      {
        source: 'musicbrainz',
        release_id: 'release-two',
        matched: 1,
        clear: 1,
      },
    ],
  },
  evidence_token: 'current-evidence',
};

export function auxiliary(url: string) {
  if (url.includes('/playlist-references')) return reply({ playlists: [] });
  if (url.startsWith('/api/repair/review-queue/summary?'))
    return reply({ all: 1, review: 1, ready: 0, files: 0, ready_total: 0 });
  if (url === '/api/repair/finding-types') return reply({ types: [] });
  if (url === '/api/repair/bulk-fix/status') return reply({ running: false });
  if (url === '/api/repair/status') return reply({ current_job: null });
  if (url === '/api/repair/history?limit=50') return reply({ runs: [] });
  return null;
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/review');
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});
