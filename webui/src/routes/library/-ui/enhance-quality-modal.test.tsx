import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import type { LibraryV2EnhanceTrack } from '../-library-v2.types';

import { EnhanceQualityModal, qualityTier } from './enhance-quality-modal';

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

  expect(onEnhance).toHaveBeenCalledWith([3]);
});

it('colours a quality by what it is', () => {
  expect(qualityTier('MP3 320kbps')).toBe('lossy');
  expect(qualityTier('FLAC 16-bit')).toBe('lossless');
  expect(qualityTier('FLAC 24-bit')).toBe('hires');
  expect(qualityTier(null)).toBe('unknown');
});
