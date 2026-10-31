import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import type { LibraryV2EnhanceTrack } from '../-library-v2.types';

import { EnhanceQualityModal } from './enhance-quality-modal';

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

  expect(screen.getByText('Enhance Quality — Muse')).toBeInTheDocument();
  expect(screen.getByText('2 below profile')).toBeInTheDocument();
  expect(screen.getByText('On Wishlist')).toBeInTheDocument();
  expect(screen.getAllByText('Not on Wishlist')).toHaveLength(2);

  fireEvent.click(screen.getByLabelText('Enhance Song 2'));
  fireEvent.click(screen.getByRole('button', { name: 'Enhance (2)' }));

  expect(onEnhance).toHaveBeenCalledWith([1, 3]);
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

  fireEvent.click(screen.getByRole('button', { name: 'Deselect all' }));

  expect(screen.getByRole('button', { name: 'Enhance (0)' })).toBeDisabled();
});
