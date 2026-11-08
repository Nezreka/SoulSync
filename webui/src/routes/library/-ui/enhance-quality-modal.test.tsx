import type { ReactElement } from 'react';

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2EnhanceTrack } from '../-library-v2.types';

import { EnhanceQualityModal, qualityTier } from './enhance-quality-modal';

function render(ui: ReactElement) {
  return rtlRender(
    <QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function track(id: number, album: string, monitored = false): LibraryV2EnhanceTrack {
  return {
    track_id: id,
    title: `Song ${id}`,
    album_id: album === 'Origin' ? 1 : 2,
    album_title: album,
    disc_number: 1,
    track_number: id,
    current: 'MP3 320kbps',
    profile: 'Lossless',
    target: 'FLAC 16-bit',
    monitored,
  };
}

it('groups the upgrades by album and enhances only the picked tracks', () => {
  const onEnhance = vi.fn();
  render(
    <EnhanceQualityModal
      title="Muse"
      tracks={[track(1, 'Origin', true), track(2, 'Origin'), track(3, 'Absolution')]}
      onEnhance={onEnhance}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByRole('dialog', { name: 'Enhance Quality — Muse' })).toBeInTheDocument();
  expect(screen.getByText('3 tracks on 2 releases can sound better.')).toBeInTheDocument();
  // one card per release, each with its cover
  expect(screen.getByText('Origin')).toBeInTheDocument();
  expect(screen.getByText('Absolution')).toBeInTheDocument();
  expect(document.querySelector('img[src="/api/library/v2/artwork/album/1"]')).not.toBeNull();
  // only the track already listed says so
  expect(screen.getAllByText('On Wishlist')).toHaveLength(1);

  fireEvent.click(screen.getByLabelText('Enhance Song 2'));
  fireEvent.click(screen.getByRole('button', { name: 'Enhance (2)' }));

  expect(onEnhance).toHaveBeenCalledWith([1, 3], null);
});

it('cannot enhance nothing', () => {
  render(
    <EnhanceQualityModal
      title="Muse"
      tracks={[track(1, 'Origin')]}
      onEnhance={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByLabelText('Deselect all'));

  expect(screen.getByRole('button', { name: 'Enhance (0)' })).toBeDisabled();
});

it('a whole release is picked or dropped at once', () => {
  const onEnhance = vi.fn();
  render(
    <EnhanceQualityModal
      title="Muse"
      tracks={[track(1, 'Origin'), track(2, 'Origin'), track(3, 'Absolution')]}
      onEnhance={onEnhance}
      onClose={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByLabelText('Enhance all of Origin'));
  expect(screen.getByText('1 of 3 picked')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Enhance (1)' }));

  expect(onEnhance).toHaveBeenCalledWith([3], null);
});

it('colours a quality by what it is', () => {
  expect(qualityTier('MP3 320kbps')).toBe('lossy');
  expect(qualityTier('FLAC 16-bit')).toBe('lossless');
  expect(qualityTier('FLAC 24-bit')).toBe('hires');
  expect(qualityTier(null)).toBe('unknown');
});

it("the admin can aim a run at another profile, like 3.6.1's tier picker", async () => {
  // 3.6.1 let you upgrade "tracks below lossless" whatever the profile said.
  // the picker lists the profiles that upgrade; picking one shows what IT
  // would upgrade and the run carries it
  const hiRes = { ...track(4, 'Absolution'), profile: 'Hi-Res', target: 'FLAC 24-bit' };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      const body = url.includes('quality-profiles')
        ? {
            success: true,
            profiles: [
              { id: 7, name: 'Hi-Res', upgrade_policy: 'acceptable' },
              { id: 8, name: 'Keep', upgrade_policy: 'none' },
            ],
          }
        : url.includes('quality_profile_id=7')
          ? { success: true, tracks: [hiRes] }
          : { success: true, tracks: [] };
      return new Response(JSON.stringify(body), {
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
  const onEnhance = vi.fn();
  render(
    <EnhanceQualityModal
      title="Muse"
      tracks={[track(1, 'Origin')]}
      scope={{ entity: 'artists', id: 3 }}
      canChooseTarget
      onEnhance={onEnhance}
      onClose={vi.fn()}
    />,
  );

  const picker = (await screen.findByLabelText('Upgrade to')) as HTMLSelectElement;
  // a profile that never upgrades is no target
  expect([...picker.options].map((o) => o.textContent)).toEqual([
    "Each track's own profile",
    'Hi-Res',
  ]);
  fireEvent.change(picker, { target: { value: '7' } });
  await waitFor(() => expect(screen.getByText('Song 4')).toBeInTheDocument());
  expect(screen.queryByText('Song 1')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Enhance (1)' }));
  expect(onEnhance).toHaveBeenCalledWith([4], 7);
});

it('a profile without write rights gets no picker', () => {
  render(
    <EnhanceQualityModal
      title="Muse"
      tracks={[track(1, 'Origin')]}
      scope={{ entity: 'artists', id: 3 }}
      onEnhance={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.queryByLabelText('Upgrade to')).toBeNull();
});

it('a failed profile check says so instead of "everything meets it"', async () => {
  // third #1062 review G15-6
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes('quality-profiles'))
        return new Response(
          JSON.stringify({
            success: true,
            profiles: [{ id: 7, name: 'Hi-Res', upgrade_policy: 'acceptable' }],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        );
      return new Response(JSON.stringify({ success: false, error: 'boom' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
  render(
    <EnhanceQualityModal
      title="Muse"
      tracks={[track(1, 'Origin')]}
      scope={{ entity: 'artists', id: 3 }}
      canChooseTarget
      onEnhance={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  fireEvent.change(await screen.findByLabelText('Upgrade to'), { target: { value: '7' } });
  expect(await screen.findByText('Couldn’t check that profile.')).toBeInTheDocument();
  expect(screen.queryByText(/already meets/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^Enhance \(/ })).toBeDisabled();
});

it('a failed background refetch keeps the checked profile usable (review 4 R4-E-3)', async () => {
  let fail = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes('quality-profiles'))
        return new Response(
          JSON.stringify({
            success: true,
            profiles: [{ id: 7, name: 'Hi-Res', upgrade_policy: 'acceptable' }],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        );
      if (fail)
        return new Response(JSON.stringify({ success: false, error: 'boom' }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        });
      return new Response(JSON.stringify({ success: true, tracks: [track(1, 'Origin')] }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
  const client = createTestQueryClient();
  rtlRender(
    <QueryClientProvider client={client}>
      <EnhanceQualityModal
        title="Muse"
        tracks={[track(1, 'Origin')]}
        scope={{ entity: 'artists', id: 3 }}
        canChooseTarget
        onEnhance={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
  fireEvent.change(await screen.findByLabelText('Upgrade to'), { target: { value: '7' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /^Enhance \(/ })).toBeEnabled());
  fail = true;
  await client.refetchQueries();
  await waitFor(() => expect(client.getQueryCache().getAll().some((q) => q.state.status === 'error')).toBe(true));
  expect(screen.queryByText('Couldn’t check that profile.')).toBeNull();
  expect(screen.getByRole('button', { name: /^Enhance \(/ })).toBeEnabled();
});
