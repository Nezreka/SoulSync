import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RenderFx, StashEntry } from '../-sample-studio.types';

import { DEFAULT_FX } from '../-sample-studio.types';
import { SaveDialog } from './save-dialog';

const track = { id: 7, title: 'Midnight Groove', artist_name: 'Test Artist' };

type FetchStub = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function stubApi(handlers: {
  preview?: object;
  chop?: object;
  folders?: object;
  failWith?: number;
}) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const stub: FetchStub = async (input, init) => {
    const req = input instanceof Request ? input : null;
    const url = req ? req.url : String(input);
    const method = req ? req.method : (init?.method ?? 'GET');
    let body: unknown = undefined;
    if (init?.body) body = JSON.parse(String(init.body));
    else if (req) {
      try {
        body = await req.clone().json();
      } catch {
        body = undefined;
      }
    }
    calls.push({ url, method, body });
    if (handlers.failWith) {
      return new Response(JSON.stringify({ success: false, error: 'boom' }), {
        status: handlers.failWith,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/api/sample/folders')) {
      return new Response(
        JSON.stringify({
          success: true,
          data: handlers.folders ?? { folders: [], default: null },
          error: null,
        }),
        { headers: { 'Content-Type': 'application/json' } },
      );
    }
    if (url.includes('/api/sample/preview')) {
      return new Response(
        JSON.stringify({
          success: true,
          data: handlers.preview ?? { preview_id: 'p1.wav', engine: 'librosa', duration_s: 2 },
          error: null,
        }),
        { headers: { 'Content-Type': 'application/json' } },
      );
    }
    if (url.includes('/api/sample/chop')) {
      const entry = (handlers.chop ?? {
        id: 3,
        name: 'Midnight Groove · 0:00 chop',
        tags: [],
        track_id: 7,
        start_s: 0,
        end_s: 2,
        pitch_st: 0,
        target_bpm: null,
        format: 'wav16',
        file_path: '/x/chop.wav',
        created_at: 1,
      }) as StashEntry;
      return new Response(JSON.stringify({ success: true, data: entry, error: null }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('{}', { status: 404 });
  };
  vi.stubGlobal('fetch', vi.fn(stub));
  return calls;
}

function renderDialog(over: Partial<Parameters<typeof SaveDialog>[0]> = {}) {
  const props = {
    open: true,
    onClose: vi.fn(),
    track,
    start: 0,
    end: 2,
    pitchSt: 0,
    targetBpm: null,
    stem: null,
    fx: DEFAULT_FX,
    onSaved: vi.fn(),
    ...over,
  };
  // The dialog fetches configured sample folders via React Query.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SaveDialog {...props} />
    </QueryClientProvider>,
  );
  return props;
}

describe('SaveDialog', () => {
  beforeEach(() => {
    stubApi({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders nothing when closed', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <SaveDialog {...renderDialogBase({ open: false })} />
      </QueryClientProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  function renderDialogBase(over: Partial<Parameters<typeof SaveDialog>[0]>) {
    return {
      open: true,
      onClose: vi.fn(),
      track,
      start: 0,
      end: 2,
      pitchSt: 0,
      targetBpm: null,
      stem: null,
      fx: DEFAULT_FX,
      onSaved: vi.fn(),
      ...over,
    };
  }

  it('prefills the suggested chop name on open', () => {
    renderDialog();
    const name = screen.getByLabelText('Name') as HTMLInputElement;
    expect(name.value).toBe('Midnight Groove · 0:00 chop');
  });

  it('disables Save while the name is empty', () => {
    renderDialog();
    const name = screen.getByLabelText('Name');
    fireEvent.change(name, { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: /Save to stash/ })).toBeDisabled();
    fireEvent.change(name, { target: { value: 'my chop' } });
    expect(screen.getByRole('button', { name: /Save to stash/ })).not.toBeDisabled();
  });

  it('saves with name, tags and format, then closes', async () => {
    const calls = stubApi({});
    const props = renderDialog();

    const name = screen.getByLabelText('Name');
    fireEvent.change(name, { target: { value: 'killer break' } });

    const tagInput = screen.getByPlaceholderText('add a tag…');
    fireEvent.change(tagInput, { target: { value: 'Drums ' } });
    fireEvent.keyDown(tagInput, { key: 'Enter' });
    expect(screen.getByText('drums')).toBeInTheDocument();

    const format = screen.getByLabelText('Format') as HTMLSelectElement;
    fireEvent.change(format, { target: { value: 'flac' } });

    fireEvent.click(screen.getByRole('button', { name: /Save to stash/ }));

    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
    expect(props.onClose).toHaveBeenCalledTimes(1);
    const chopCall = calls.find((c) => c.url.includes('/api/sample/chop'));
    expect(chopCall?.body).toMatchObject({
      track_id: 7,
      start_s: 0,
      end_s: 2,
      name: 'killer break',
      tags: ['drums'],
      format: 'flac',
    });
  });

  it('shows the server error and stays open when saving fails', async () => {
    stubApi({ failWith: 500 });
    const props = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: /Save to stash/ }));
    await waitFor(() => expect(screen.getByText(/boom|failed/i)).toBeInTheDocument());
    expect(props.onSaved).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('removes tags and closes on Escape', () => {
    const props = renderDialog();
    const tagInput = screen.getByPlaceholderText('add a tag…');
    fireEvent.change(tagInput, { target: { value: 'loop' } });
    fireEvent.keyDown(tagInput, { key: 'Enter' });
    expect(screen.getByText('loop')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag loop' }));
    expect(screen.queryByText('loop')).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(props.onClose).toHaveBeenCalled();
  });

  it('shows the stem and fx summary line', () => {
    renderDialog({ stem: 'drums', pitchSt: 2, targetBpm: 128 });
    expect(screen.getByText(/Drums stem · \+2 st · → 128 BPM/)).toBeInTheDocument();
  });

  it('sends the FX recipe with the save body and shows it in the summary', async () => {
    const calls = stubApi({});
    const fx: RenderFx = {
      ...DEFAULT_FX,
      normalize: true,
      reverse: true,
      fadeMs: 10,
      space: 0.5,
      delay: { time: '1/8', feedback: 0.35, mix: 0.2 },
    };
    renderDialog({ fx });

    fireEvent.click(screen.getByRole('button', { name: /Save to stash/ }));
    await waitFor(() => {
      const chopCall = calls.find((c) => c.url.includes('/api/sample/chop'));
      expect(chopCall?.body).toMatchObject({
        normalize: 'peak',
        reverse: true,
        fade_ms: 10,
        space: 0.5,
        delay: { time: '1/8', feedback: 0.35, mix: 0.2 },
      });
    });
    expect(
      screen.getByText(/peak normalize · reversed · 10 ms fade · 0.5 s space · 1\/8 delay/),
    ).toBeInTheDocument();
  });

  it('omits normalize and sends null space/delay when FX are off', async () => {
    const calls = stubApi({});
    renderDialog({ fx: DEFAULT_FX });

    fireEvent.click(screen.getByRole('button', { name: /Save to stash/ }));
    await waitFor(() => {
      const chopCall = calls.find((c) => c.url.includes('/api/sample/chop'));
      expect(chopCall).toBeDefined();
      expect(chopCall?.body).not.toHaveProperty('normalize');
      expect(chopCall?.body).toMatchObject({ fade_ms: 5, space: null, delay: null });
    });
  });

  it('defaults the folder picker to the first configured folder', async () => {
    stubApi({
      folders: { folders: ['/samples', '/samples/drums'], default: '/samples' },
    });
    renderDialog();

    const folder = (await screen.findByLabelText('Folder')) as HTMLSelectElement;
    // Wait for the async folders query to populate the options.
    await waitFor(() => expect(folder.options.length).toBe(2));
    expect(folder.value).toBe('/samples');
  });

  it('saves to the picked folder', async () => {
    const calls = stubApi({
      folders: { folders: ['/samples', '/samples/drums'], default: '/samples' },
    });
    const props = renderDialog();

    const folder = (await screen.findByLabelText('Folder')) as HTMLSelectElement;
    await waitFor(() => expect(folder.options.length).toBe(2));
    fireEvent.change(folder, { target: { value: '/samples/drums' } });
    fireEvent.click(screen.getByRole('button', { name: /Save to stash/ }));

    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
    const chopCall = calls.find((c) => c.url.includes('/api/sample/chop'));
    expect(chopCall?.body).toMatchObject({ folder: '/samples/drums' });
  });

  it('sends no folder when none are configured', async () => {
    const calls = stubApi({});
    const props = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: /Save to stash/ }));

    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
    const chopCall = calls.find((c) => c.url.includes('/api/sample/chop'));
    expect(chopCall?.body).toMatchObject({ folder: null });
  });
});
