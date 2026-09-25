import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ArtistFace, CoverCard, TrackRow } from './compact-item';

afterEach(cleanup);

describe('ArtistFace', () => {
  it('is a link, with initials while it has no photo', () => {
    render(<ArtistFace name="Boards of Canada" href="/a/1" inLibrary={false} artistId="x1" />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/a/1');
    expect(link.textContent).toContain('BO');
    expect(link.textContent).toContain('Artist');
  });

  it('carries no lazy-loader attributes when there is no artist id', () => {
    render(<ArtistFace name="Nobody" href="/a/2" inLibrary={false} />);
    expect(document.querySelector('[data-needs-image]')).toBeNull();
    expect(document.querySelector('[data-artist-id]')).toBeNull();
  });

  it('swaps a broken photo for initials the lazy loader can fill', () => {
    render(
      <ArtistFace name="Aphex Twin" image="https://x/a.jpg" href="/a/3" inLibrary artistId={7} />,
    );
    fireEvent.error(document.querySelector('img') as HTMLImageElement);
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('[data-needs-image="true"]')?.textContent).toBe('AT');
    expect(screen.getByRole('link').textContent).toContain('In your library');
  });
});

describe('CoverCard', () => {
  it('opens from click and from Enter or Space', () => {
    const onOpen = vi.fn();
    render(<CoverCard name="Drukqs" sub="Aphex Twin • 2001" onOpen={onOpen} />);
    const card = screen.getByRole('button', { name: 'Drukqs' });
    fireEvent.click(card);
    fireEvent.keyDown(card, { key: 'Enter' });
    fireEvent.keyDown(card, { key: ' ' });
    fireEvent.keyDown(card, { key: 'Tab' });
    expect(onOpen).toHaveBeenCalledTimes(3);
  });

  it('runs its cover button without also opening the card', () => {
    const onOpen = vi.fn();
    const onAction = vi.fn();
    render(
      <CoverCard
        name="Drukqs"
        sub="x"
        onOpen={onOpen}
        actionLabel="Download Drukqs"
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download Drukqs' }));
    expect(onAction).toHaveBeenCalledOnce();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('Enter on its cover button does not open the card underneath', () => {
    const onOpen = vi.fn();
    render(
      <CoverCard name="Drukqs" sub="x" onOpen={onOpen} actionLabel="Get" onAction={vi.fn()} />,
    );
    fireEvent.keyDown(screen.getByRole('button', { name: 'Get' }), { key: 'Enter' });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('is a link when it has an href', () => {
    render(<CoverCard name="Warp" sub="Label" href="/label-detail/l1" round />);
    expect(screen.getByRole('link')).toHaveAttribute('href', '/label-detail/l1');
  });

  it('shows its badge', () => {
    render(<CoverCard name="Drukqs" sub="x" badge="In library" />);
    expect(screen.getByText('In library')).toBeInTheDocument();
  });
});

describe('TrackRow', () => {
  const renderRow = (over: Partial<Parameters<typeof TrackRow>[0]> = {}) => {
    const onOpen = vi.fn();
    const onPlay = vi.fn();
    render(
      <TrackRow
        index={2}
        name="Xtal"
        sub="Aphex Twin • SAW 85-92"
        duration="4:54"
        playTitle="Stream this track"
        onOpen={onOpen}
        onPlay={onPlay}
        {...over}
      />,
    );
    return { onOpen, onPlay };
  };

  it('numbers itself from one and shows its duration', () => {
    renderRow();
    const row = screen.getByRole('button', { name: 'Xtal, Aphex Twin • SAW 85-92' });
    expect(row.textContent).toContain('3');
    expect(row.textContent).toContain('4:54');
  });

  it('opens on Enter from the row, not from its buttons', () => {
    const { onOpen, onPlay } = renderRow();
    fireEvent.keyDown(screen.getByRole('button', { name: /^Xtal,/ }), { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledOnce();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Stream this track: Xtal' }), {
      key: 'Enter',
    });
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onPlay).not.toHaveBeenCalled();
  });
});
