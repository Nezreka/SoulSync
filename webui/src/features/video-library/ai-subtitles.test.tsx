import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AiJob } from './ai-subtitles';

import { jobWords } from './ai-subtitles';
import { AiSubtitlesDialog } from './ai-subtitles-dialog';

const job = (extra: Partial<AiJob>): AiJob => ({
  id: 1,
  video_kind: 'movie',
  video_id: 7,
  language: 'en',
  source: 'on_demand',
  state: 'queued',
  progress: 0,
  ...extra,
});

describe('a job in plain words', () => {
  it('says where it is', () => {
    expect(jobWords(job({ state: 'queued', position: 3 }))).toEqual({
      label: 'AI subtitles are in line',
      detail: '3rd in line',
    });
    expect(jobWords(job({ state: 'running', progress: 0.42, eta_seconds: 360 }))).toEqual({
      label: 'Making subtitles with AI… 42%',
      detail: 'about 6 min left',
    });
    expect(jobWords(job({ state: 'paused' })).detail).toBe('Someone else is watching right now');
    expect(jobWords(job({ state: 'done', verdict: 'low' })).detail).toContain('Low confidence');
    expect(jobWords(job({ state: 'no_match', error: 'the audio is ja' })).detail).toBe(
      'the audio is ja',
    );
    expect(jobWords(job({ state: 'queued', source: 'library' }), true).detail).toBe(
      'Waiting while someone watches',
    );
  });
});

type FetchStub = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
let calls: { url: string; method: string; body: unknown }[];
let jobs: AiJob[];
let enqueueStatus: number;

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  calls = [];
  jobs = [];
  enqueueStatus = 202;
  localStorage.setItem('ss-player:subLang', 'en');
  const stub: FetchStub = async (input, init) => {
    const req = input instanceof Request ? input : null;
    const url = req ? req.url : input instanceof URL ? input.href : (input as string);
    const method = req ? req.method : (init?.method ?? 'GET');
    let body: unknown;
    try {
      body = req
        ? await req.clone().json()
        : init?.body
          ? JSON.parse(init.body as string)
          : undefined;
    } catch {
      body = undefined;
    }
    calls.push({ url, method, body });
    if (url.includes('/subtitles/ai/jobs') && method === 'POST' && url.endsWith('/cancel')) {
      jobs = jobs.map((j) => ({ ...j, state: 'cancelled' }));
      return json({ ok: true });
    }
    if (url.includes('/subtitles/ai/jobs') && method === 'POST') {
      if (enqueueStatus !== 202)
        return json(
          { error: 'unavailable', message: 'No AI subtitle model is installed yet.' },
          enqueueStatus,
        );
      jobs = [job({ state: 'queued', position: 2 })];
      return json({ job: jobs[0] }, 202);
    }
    if (url.includes('/subtitles/ai/jobs'))
      return json({ jobs, available: true, why: null, watching: false });
    return json({});
  };
  vi.stubGlobal('fetch', vi.fn(stub));
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function dialog(onReady = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AiSubtitlesDialog kind="movie" id={7} title="Heat" onClose={vi.fn()} onReady={onReady} />
    </QueryClientProvider>,
  );
}

describe('make subtitles with ai from a title page', () => {
  it('asks for the picked language and shows its place in line, then stops it', async () => {
    dialog();
    fireEvent.change(screen.getByLabelText('Subtitle language'), { target: { value: 'en' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Generate' }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
        kind: 'movie',
        item_id: 7,
        lang: 'en',
        origin: 'detail',
      }),
    );
    expect(await screen.findByText('2nd in line')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/ai/jobs/1/cancel'))).toBe(true));
  });

  it('says why when the server cannot make them', async () => {
    enqueueStatus = 503;
    dialog();
    fireEvent.click(await screen.findByRole('button', { name: 'Generate' }));
    expect(await screen.findByText('No AI subtitle model is installed yet.')).toBeTruthy();
  });
});
