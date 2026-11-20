/**
 * The video organization settings, wherever DESIGN 3.3 draws them now
 * (settings-bic phase 4: Library > Naming, Import and files, Tags and
 * artwork, Subtitles; Quality > YouTube; Downloads > Behaviour): the load (once, the first time the Library tab or the
 * video settings page is shown, with a retry), the previews, what saves and
 * when, the languages check, the subtitle chain, Whisper, Reset and the
 * Missing Subtitles job. The load cases settings-video-library-load.test.ts
 * ran against video-settings.js.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SubtitlesHero,
  VideoImport,
  VideoImportHero,
  VideoNaming,
  VideoNamingHero,
  VideoSubtitles,
  VideoTags,
  VideoTagsHero,
  YoutubeFileOptions,
  YoutubeFollowCount,
} from './library-video-organization';
import { jobLine, providerOrder, renderTemplate, videoOrganization } from './video-organization';

const ORG = {
  movie_template: '$title ($year)/$title',
  episode_template: '$series/$episodetitle',
  youtube_template: '',
  transfer_mode: 'move',
  colon_replacement: 'delete',
  verify_with_ffprobe: true,
  subtitle_langs: 'en,es',
  subtitle_provider_order: '["subdl", "opensubtitles"]',
  subtitle_use_embedded: false,
  whisper_model: 'base',
  whisper_enabled: true,
  whisper_model_downloaded: true,
  whisper_model_path: '/models/base',
  recycle_keep_days: 0,
};

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoOrganization.resetState();
  calls = [];
  answers = {
    'GET /api/video/organization': ORG,
    'GET /api/video/subtitles/whisper/status': {
      enabled: true,
      downloaded: true,
      engine: true,
      model: 'base',
      size_bytes: 145_000_000,
      downloaded_at: '2026-11-01T10:00:00',
      models: {
        tiny: { label: 'tiny', blurb: 'fastest' },
        base: { label: 'base', blurb: 'balanced' },
        small: { label: 'small', blurb: 'best' },
      },
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined;
      calls.push({ url, method, body });
      const key = `${method} ${url}`;
      if (answers[key] === 503)
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve(null),
        } as Response);
      const data = key in answers ? answers[key] : method === 'POST' ? body : { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
});

const el = (id: string) => document.getElementById(id) as HTMLInputElement;
const posts = () =>
  calls
    .filter((c) => c.method === 'POST' && c.url === '/api/video/organization')
    .map((c) => c.body!);
/** the browser's change event: a field left after typing in it */
function typeAndLeave(input: HTMLInputElement, value: string) {
  fireEvent.input(input, { target: { value } });
  input.dispatchEvent(new Event('change', { bubbles: true }));
}
const panel = () =>
  render(
    <div>
      <VideoNamingHero />
      <VideoNaming />
      <VideoImportHero />
      <VideoImport />
      <VideoTagsHero />
      <VideoTags />
      <SubtitlesHero />
      <VideoSubtitles />
    </div>,
  );
const seg = (id: string) => document.getElementById(id)!.getAttribute('data-value');
async function shown() {
  act(() => {
    document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
  });
  await waitFor(() => expect(el('vo-movie-template').value).toBe('$title ($year)/$title'));
  await waitFor(() => expect(el('vo-movie-template').disabled).toBe(false));
  await waitFor(() => expect(el('vo-whisper-status').textContent).not.toBe(''));
}

describe('the load', () => {
  it('loads once, on the Library tab, and keeps what was typed', async () => {
    panel();
    expect(calls).toEqual([]);
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    expect(el('vo-movie-template').disabled).toBe(true);
    await shown();
    // the organization, the Whisper model status it fills in, and the
    // Missing Subtitles job's line (blank from the music side before, AUDIT 36)
    expect([...new Set(calls.map((c) => c.url))].sort()).toEqual([
      '/api/video/organization',
      '/api/video/repair/jobs',
      '/api/video/subtitles/whisper/status',
    ]);
    fireEvent.input(el('vo-movie-template'), { target: { value: '/unsaved' } });
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    expect(calls.filter((c) => c.url === '/api/video/organization')).toHaveLength(1);
    expect(el('vo-movie-template').value).toBe('/unsaved');
  });

  it('a failed load keeps the controls off and offers a retry', async () => {
    answers['GET /api/video/organization'] = 503;
    panel();
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    await waitFor(() => expect(document.querySelector('.stg-library-retry')).not.toBeNull());
    expect(el('vo-movie-template').disabled).toBe(true);
    answers['GET /api/video/organization'] = ORG;
    fireEvent.click(document.querySelector('.stg-library-retry')!);
    await waitFor(() => expect(el('vo-movie-template').disabled).toBe(false));
    expect(el('vo-movie-template').value).toBe('$title ($year)/$title');
  });

  it('the video settings page loads it too, and the job status each time it is shown', async () => {
    answers['GET /api/video/repair/jobs'] = {
      jobs: [
        {
          job_id: 'missing_subtitles',
          enabled: true,
          interval_hours: 168,
          last_run: { finished_at: '2026-11-10T03:00:00', auto_fixed: 4 },
          pending_findings_count: 2,
        },
      ],
    };
    panel();
    const show = () =>
      act(() => {
        document.dispatchEvent(
          new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
        );
      });
    show();
    await waitFor(() =>
      expect(el('vo-sub-job-status').textContent).toBe(
        'On · every 7 days · queues what’s missing · last run 2026-11-10, queued 4 · 2 to review',
      ),
    );
    expect(el('vo-movie-template').value).toBe('$title ($year)/$title');
    show();
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/video/repair/jobs')).toHaveLength(2),
    );
    expect(calls.filter((c) => c.url === '/api/video/organization')).toHaveLength(1);
  });
});

describe('the fields', () => {
  it('fills from the settings, with the page defaults', async () => {
    panel();
    await shown();
    expect(seg('vo-transfer-mode')).toBe('move');
    expect(el('vo-verify').checked).toBe(true);
    expect(el('vo-sub-langs').value).toBe('en,es');
    expect(el('vo-recycle-days').value).toBe('7');
    expect(el('vo-sub-min-score').value).toBe('24');
    // Quality > YouTube draws these two; it loads the settings itself
    render(<YoutubeFileOptions />);
    expect(el('vo-sponsorblock').getAttribute('data-value')).toBe('off');
    expect(el('vo-yt-subs').checked).toBe(false);
    expect(el('vo-whisper-model').value).toBe('base');
  });

  it('the previews follow the templates and the colon setting', async () => {
    panel();
    expect(el('vo-movie-preview').textContent).toBe('…');
    await shown();
    expect(el('vo-movie-preview').textContent).toBe('The Matrix (1999)/The Matrix.mkv');
    // a blank template previews its placeholder
    expect(el('vo-youtube-preview').textContent).toBe(
      'Veritasium/Season 2024/Veritasium - s2024e0315 - How Electricity Actually Works.mp4',
    );
    expect(el('vo-episode-preview').textContent).toBe('Dexter Resurrection/A Beating Heart.mkv');
    fireEvent.change(el('vo-colon-replacement'), { target: { value: 'smart' } });
    expect(el('vo-episode-preview').textContent).toBe('Dexter - Resurrection/A Beating Heart.mkv');
    fireEvent.input(el('vo-movie-template'), { target: { value: '$title [$audiolangs]' } });
    expect(el('vo-movie-preview').textContent).toBe('The Matrix [EN].mkv');
  });

  it('a typed field saves when it is left, the whole of it; a box saves at once', async () => {
    panel();
    await shown();
    fireEvent.input(el('vo-movie-template'), { target: { value: '$title' } });
    expect(posts()).toHaveLength(0);
    el('vo-movie-template').dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toMatchObject({
      movie_template: '$title',
      episode_template: '$series/$episodetitle',
      transfer_mode: 'move',
      subtitle_provider_order: ['subdl', 'opensubtitles'],
      subtitle_min_score: 24,
      recycle_keep_days: '7',
      whisper_model_downloaded: true,
      whisper_model_path: '/models/base',
      whisper_model_size_bytes: 145_000_000,
    });
    expect(shell.showToast).toHaveBeenCalledWith('Library organization saved', 'success');
    fireEvent.click(el('vo-nfo'));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].write_nfo).toBe(true);
    // the knobs are clamped the way the backend would
    typeAndLeave(el('vo-sub-daily-quota'), '5000');
    await waitFor(() => expect(posts()).toHaveLength(3));
    expect(posts()[2].subtitle_daily_quota).toBe(1000);
  });

  it('the ignore switches are off (and dimmed) while embedded subs do not count', async () => {
    panel();
    await shown();
    expect(el('vo-sub-use-embedded').checked).toBe(false);
    expect(el('vo-sub-ignore-pgs').disabled).toBe(true);
    fireEvent.click(el('vo-sub-use-embedded'));
    expect(el('vo-sub-ignore-pgs').disabled).toBe(false);
  });

  it('a language that is not a code is put back, with a note, and not saved', async () => {
    panel();
    await shown();
    typeAndLeave(el('vo-sub-langs'), 'en, english');
    await waitFor(() => expect(el('vo-sub-langs').value).toBe('en,es'));
    expect(el('vo-sub-langs-error').hidden).toBe(false);
    expect(el('vo-sub-langs-error').textContent).toBe(
      'Not a language code: "english". Use 2-3 letters per code (e.g. en, es).',
    );
    expect(posts()).toHaveLength(0);
    typeAndLeave(el('vo-sub-langs'), 'en;fr');
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].subtitle_langs).toBe('en;fr');
    expect(el('vo-sub-langs-error').hidden).toBe(true);
  });

  it('Reset asks first, posts the standard layout and shows what came back', async () => {
    panel();
    await shown();
    const confirm = vi.fn(() => Promise.resolve(false));
    shell.showConfirmDialog = confirm;
    await act(async () => {
      fireEvent.click(el('vo-reset'));
    });
    expect(confirm).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Put the standard layout back?' }),
    );
    expect(posts()).toHaveLength(0);
    delete shell.showConfirmDialog;
    answers['POST /api/video/organization'] = { ...ORG, movie_template: '$title ($year)' };
    fireEvent.click(el('vo-reset'));
    await waitFor(() => expect(el('vo-movie-template').value).toBe('$title ($year)'));
    expect(posts()[0]).toEqual(
      expect.objectContaining({
        movie_template: '',
        transfer_mode: 'copy',
        youtube_follow_count: 5,
      }),
    );
    expect(shell.showToast).toHaveBeenCalledWith('Reset to the standard layout', 'success');
  });

  it('the heroes read the naming, the import and the sidecars back', async () => {
    panel();
    await shown();
    const text = document.body.textContent!;
    expect(text).toContain('A movie is saved as The Matrix (1999) / The Matrix.mkv');
    expect(text).toContain('A finished file is moved in and checked with ffprobe.');
    expect(text).toContain('Finished files are filed alone');
  });

  it('the YouTube follow count, drawn on Downloads > Behaviour, saves when it is left', async () => {
    render(<YoutubeFollowCount />);
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    await waitFor(() => expect(el('vo-yt-follow-count').value).toBe('5'));
    await waitFor(() => expect(el('vo-yt-follow-count').disabled).toBe(false));
    typeAndLeave(el('vo-yt-follow-count'), '12');
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].youtube_follow_count).toBe('12');
  });
});

describe('the subtitle chain', () => {
  const steps = () =>
    [
      ...document.querySelectorAll<HTMLElement>('#subchain-widget ul[aria-label] > li[data-id]'),
    ].map((s) => s.dataset.id);
  const menu = (id: string, entry: string) => {
    fireEvent.click(document.querySelector(`#subchain-widget [data-more="${id}"]`)!);
    return [
      ...document.querySelectorAll<HTMLButtonElement>('#subchain-widget [role="menu"] button'),
    ].find((b) => b.textContent!.startsWith(entry))!;
  };

  it('the stored order on the order list, Whisper pinned last while it can run', async () => {
    answers['GET /api/video/organization'] = { ...ORG, download_subtitles: true };
    panel();
    await shown();
    expect(steps()).toEqual(['subdl', 'opensubtitles']);
    expect(document.querySelector('#subchain-widget li[data-id="subdl"]')!.textContent).toContain(
      'Tried first',
    );
    const whisper = document.querySelector('#subchain-widget [data-pinned="whisper"]')!;
    expect(whisper.textContent).toContain('Last resort, always tried last');
    expect(
      [...document.querySelectorAll<HTMLElement>('#subchain-widget [data-pool] > li[data-id]')].map(
        (t) => t.dataset.id,
      ),
    ).toEqual(['subsource', 'embedded']);
    // and the hero reads it back
    expect(document.body.textContent).toContain(
      'Subtitles in en, es come from SubDL, then OpenSubtitles. When none has them, Whisper transcribes the audio.',
    );
  });

  it('add, move and remove save at once; the last one stays', async () => {
    panel();
    await shown();
    fireEvent.click(document.querySelector('#subchain-widget [data-use="embedded"]')!);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].subtitle_provider_order).toEqual(['subdl', 'opensubtitles', 'embedded']);
    fireEvent.click(menu('embedded', 'Move up'));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].subtitle_provider_order).toEqual(['subdl', 'embedded', 'opensubtitles']);
    fireEvent.click(menu('subdl', 'Stop using'));
    await waitFor(() => expect(posts()).toHaveLength(3));
    fireEvent.click(menu('embedded', 'Stop using'));
    await waitFor(() => expect(posts()).toHaveLength(4));
    // the last one cannot go, and says why
    const stop = menu('opensubtitles', 'Stop using');
    expect(stop.disabled).toBe(true);
    expect(document.querySelector('#subchain-widget [role="menu"]')!.textContent).toContain(
      'Keep at least one provider: subtitles need somewhere to come from',
    );
    expect(steps()).toEqual(['opensubtitles']);
  });

  it('the stored order: a list, JSON, or nothing', () => {
    expect(providerOrder(['subdl', 'whisper', 'subdl'])).toEqual(['subdl']);
    expect(providerOrder('not json')).toEqual(['opensubtitles']);
    expect(providerOrder(null)).toEqual(['opensubtitles']);
  });
});

describe('Whisper', () => {
  it('a downloaded model: its line, the button done, the switch on', async () => {
    panel();
    await shown();
    expect(el('vo-whisper-status').textContent).toBe('Downloaded: base (145 MB) on 2026-11-01.');
    expect(el('vo-whisper-dl').textContent).toBe('base downloaded');
    expect(el('vo-whisper-dl').disabled).toBe(true);
    expect(el('vo-whisper-enabled').disabled).toBe(false);
    expect(document.querySelector('#vo-whisper-model option[value="base"]')!.textContent).toBe(
      'base: balanced',
    );
    // another model picked: it has to be downloaded first, and the switch waits
    fireEvent.change(el('vo-whisper-model'), { target: { value: 'small' } });
    expect(el('vo-whisper-dl').textContent).toBe('Download small model');
    expect(el('vo-whisper-dl').disabled).toBe(false);
    expect(el('vo-whisper-enabled').disabled).toBe(true);
    expect(posts()).toHaveLength(0);
  });

  it('a download saves the pick first, then starts, and a failure says why', async () => {
    panel();
    await shown();
    fireEvent.change(el('vo-whisper-model'), { target: { value: 'small' } });
    answers['POST /api/video/subtitles/whisper/download'] = { error: 'busy' };
    const fetcher = fetch as unknown as ReturnType<typeof vi.fn>;
    fetcher.mockImplementationOnce((url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method || 'GET', body: JSON.parse(init?.body as string) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(ORG) } as Response);
    });
    fetcher.mockImplementationOnce((url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method || 'GET', body: JSON.parse(init?.body as string) });
      return Promise.resolve({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ error: 'busy' }),
      } as Response);
    });
    fireEvent.click(el('vo-whisper-dl'));
    await waitFor(() => expect(el('vo-whisper-error').hidden).toBe(false));
    expect(el('vo-whisper-error').textContent).toBe('Could not start download: busy');
    expect(calls.slice(-2).map((c) => [c.url, c.body?.whisper_model ?? c.body?.model])).toEqual([
      ['/api/video/organization', 'small'],
      ['/api/video/subtitles/whisper/download', 'small'],
    ]);
    // the save before it was a quiet one
    expect(shell.showToast).not.toHaveBeenCalled();
  });

  it('while a download runs: the progress, the button, and the line', async () => {
    answers['GET /api/video/subtitles/whisper/status'] = {
      downloaded: false,
      engine: false,
      engine_hint: 'pip install faster-whisper',
      download: { state: 'downloading', model: 'base', total_bytes: 200, downloaded_bytes: 50 },
    };
    panel();
    await shown();
    expect(el('vo-whisper-progress').hidden).toBe(false);
    expect(el('vo-whisper-progress-bar').style.width).toBe('25%');
    expect(el('vo-whisper-dl').textContent).toBe('Downloading base… 25%');
    expect(el('vo-whisper-status').textContent).toBe(
      'No model downloaded yet. Transcription engine missing on the server: pip install faster-whisper.',
    );
    expect(el('vo-whisper-enabled').closest('[data-row]')!.textContent).toContain(
      'Download a model first (and install faster-whisper on the server).',
    );
    videoOrganization.resetState();
  });
});

describe('the rules', () => {
  it('the template engine and the job line', () => {
    expect(renderTemplate('$title ({imdb-$imdbid})', { title: 'A: B', imdbid: '' }, 'dash')).toBe(
      'A- B',
    );
    expect(renderTemplate('$title ($year) ($year)', { title: 'X', year: '2000' }, 'delete')).toBe(
      'X (2000)',
    );
    expect(jobLine(null)).toBe('');
    expect(
      jobLine({ job_id: 'missing_subtitles', enabled: false, settings: { dry_run: true } }),
    ).toBe('Off · dry run: findings only · never run');
    expect(jobLine({ job_id: 'missing_subtitles', enabled: true, interval_hours: 36 })).toBe(
      'On · every 36h · queues what’s missing · never run',
    );
  });
});
