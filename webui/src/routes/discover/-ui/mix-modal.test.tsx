import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoverMix } from '../-discover.mixes';

import { CompactPlaylist, MixModal, MixSelectionBarView } from './mix-modal';

/**
 * The compact track list and the mix modal.
 *
 * `selectable` is the axis everything here turns on: it is opt-in per call, and
 * it brings the checkbox, the preview button AND the class the grid reflows on.
 * The plain playlist renderers pass nothing and must get none of it.
 */

afterEach(cleanup);

const track = (over: Record<string, unknown> = {}) => ({
  name: 'Xtal',
  artists: [{ name: 'Aphex Twin' }],
  album: { name: 'Selected Ambient Works', images: [{ url: '/img/saw.jpg' }] },
  duration_ms: 293_000,
  ...over,
});

describe('the compact playlist', () => {
  it('numbers rows from one and shows name, artist, album and duration', () => {
    const { container } = render(<CompactPlaylist tracks={[track()]} />);
    expect(container.querySelector('.track-compact-number')!.textContent).toBe('1');
    expect(container.querySelector('.track-compact-name')!.textContent).toBe('Xtal');
    expect(container.querySelector('.track-compact-artist')!.textContent).toBe('Aphex Twin');
    expect(container.querySelector('.track-compact-album')!.textContent).toBe(
      'Selected Ambient Works',
    );
    expect(container.querySelector('.track-compact-duration')!.textContent).toBe('4:53');
  });

  it('pads the seconds', () => {
    const { container } = render(<CompactPlaylist tracks={[track({ duration_ms: 63_000 })]} />);
    expect(container.querySelector('.track-compact-duration')!.textContent).toBe('1:03');
  });

  it('leaves the duration EMPTY when it is unknown', () => {
    // "0:00" claims a fact we do not have.
    const { container } = render(<CompactPlaylist tracks={[track({ duration_ms: 0 })]} />);
    expect(container.querySelector('.track-compact-duration')!.textContent).toBe('');
  });

  it('falls back to the placeholder cover', () => {
    const { container } = render(
      <CompactPlaylist tracks={[track({ album: { name: 'x', images: [] } })]} />,
    );
    expect(container.querySelector('.track-compact-image img')).toHaveAttribute(
      'src',
      '/static/placeholder-album.png',
    );
  });

  it('adds NO selection affordances when it is not selectable', () => {
    const { container } = render(<CompactPlaylist tracks={[track()]} />);
    expect(container.querySelector('.track-compact-check')).toBeNull();
    expect(container.querySelector('.track-compact-play')).toBeNull();
    expect(container.querySelector('.has-select')).toBeNull();
  });

  it('adds all three together when it is', () => {
    // The checkbox, the preview button and the reflow class arrive as one
    // feature; any one of them alone is a half-built row.
    const { container } = render(<CompactPlaylist tracks={[track()]} selectable />);
    expect(container.querySelector('.track-compact-check')).not.toBeNull();
    expect(container.querySelector('.track-compact-play')).not.toBeNull();
    expect(container.querySelector('.discover-playlist-track-compact.has-select')).not.toBeNull();
  });

  it('reflects the selected set', () => {
    const { container } = render(
      <CompactPlaylist tracks={[track(), track({ name: 'Tha' })]} selectable selected={[1]} />,
    );
    const boxes = [...container.querySelectorAll('.track-compact-check')] as HTMLInputElement[];
    expect(boxes[0].checked).toBe(false);
    expect(boxes[1].checked).toBe(true);
  });

  it('reports a toggle and a preview by index', () => {
    const onToggle = vi.fn();
    const onPreview = vi.fn();
    const { container } = render(
      <CompactPlaylist
        tracks={[track(), track({ name: 'Tha' })]}
        selectable
        onToggle={onToggle}
        onPreview={onPreview}
      />,
    );
    fireEvent.click([...container.querySelectorAll('.track-compact-check')][1]);
    fireEvent.click([...container.querySelectorAll('.track-compact-play')][1]);
    expect(onToggle).toHaveBeenCalledWith(1);
    expect(onPreview).toHaveBeenCalledWith(1);
  });

  it('keeps the row index as a data hook', () => {
    const { container } = render(<CompactPlaylist tracks={[track(), track()]} />);
    const rows = [...container.querySelectorAll('.discover-playlist-track-compact')];
    expect(rows[1]).toHaveAttribute('data-track-index', '1');
  });
});

describe('the selection bar', () => {
  const bar = (over: Partial<Parameters<typeof MixSelectionBarView>[0]> = {}) => ({
    total: 3,
    selected: [],
    onSelectAll: vi.fn(),
    onDownloadSelected: vi.fn(),
    ...over,
  });

  it('counts the selection and disables download at zero', () => {
    render(<MixSelectionBarView {...bar()} />);
    expect(screen.getByText('0 selected')).toBeInTheDocument();
    expect(screen.getByText('Download selected')).toBeDisabled();
  });

  it('puts the count in the download label once there is one', () => {
    render(<MixSelectionBarView {...bar({ selected: [0, 2] })} />);
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.getByText('Download selected (2)')).not.toBeDisabled();
  });

  it('ticks select-all only when everything is selected', () => {
    const { container, rerender } = render(<MixSelectionBarView {...bar({ selected: [0, 1] })} />);
    const box = () => container.querySelector('.mix-select-all input') as HTMLInputElement;
    expect(box().checked).toBe(false);
    rerender(<MixSelectionBarView {...bar({ selected: [0, 1, 2] })} />);
    expect(box().checked).toBe(true);
  });

  it('does NOT tick select-all for an empty list', () => {
    // 0 === 0 would otherwise show every row selected on a list with no rows.
    const { container } = render(<MixSelectionBarView {...bar({ total: 0 })} />);
    expect((container.querySelector('.mix-select-all input') as HTMLInputElement).checked).toBe(
      false,
    );
  });

  it('selects and clears everything', () => {
    const p = bar();
    const { container, rerender } = render(<MixSelectionBarView {...p} />);
    fireEvent.click(container.querySelector('.mix-select-all input')!);
    expect(p.onSelectAll).toHaveBeenCalledWith([0, 1, 2]);

    const p2 = bar({ selected: [0, 1, 2] });
    rerender(<MixSelectionBarView {...p2} />);
    fireEvent.click(container.querySelector('.mix-select-all input')!);
    expect(p2.onSelectAll).toHaveBeenCalledWith([]);
  });

  it('downloads the selection', () => {
    const p = bar({ selected: [1] });
    render(<MixSelectionBarView {...p} />);
    fireEvent.click(screen.getByText('Download selected (1)'));
    expect(p.onDownloadSelected).toHaveBeenCalled();
  });
});

describe('the mix modal', () => {
  const mix = (over: Partial<DiscoverMix> = {}): DiscoverMix => ({
    key: 'mix_a',
    title: 'Your Mix',
    syncKey: 'your_mix',
    ...over,
  });

  const props = (over: Partial<Parameters<typeof MixModal>[0]> = {}) => ({
    mix: mix(),
    tracks: [track()],
    selected: [],
    onClose: vi.fn(),
    onAction: vi.fn(),
    onSelectAll: vi.fn(),
    onToggleTrack: vi.fn(),
    onPreviewTrack: vi.fn(),
    onDownloadSelected: vi.fn(),
    ...over,
  });

  it('shows the title, subtitle and the track list', () => {
    const { container } = render(
      <MixModal {...props({ mix: mix({ subtitle: 'Fresh picks' }) })} />,
    );
    expect(screen.getByText('Your Mix')).toBeInTheDocument();
    expect(screen.getByText('Fresh picks')).toBeInTheDocument();
    expect(container.querySelectorAll('.discover-playlist-track-compact')).toHaveLength(1);
  });

  it('always renders its rows SELECTABLE', () => {
    // The whole point of #1079 — the modal is where selection lives.
    const { container } = render(<MixModal {...props()} />);
    expect(container.querySelector('.track-compact-check')).not.toBeNull();
  });

  it('builds Download and Sync from a syncKey', () => {
    render(<MixModal {...props()} />);
    expect(screen.getByText('Download')).toBeInTheDocument();
    expect(screen.getByText('Sync')).toBeInTheDocument();
  });

  it("prefers a mix's own actions", () => {
    render(
      <MixModal
        {...props({
          mix: mix({ actions: [{ label: 'Rebuild', onclick: 'rebuild' }] }),
        })}
      />,
    );
    expect(screen.getByText('Rebuild')).toBeInTheDocument();
    expect(screen.queryByText('Sync')).toBeNull();
  });

  it('shows NO actions for a mix with neither', () => {
    // That is the vanilla's behaviour, not an oversight: there is nothing for
    // Download or Sync to act on.
    const { container } = render(<MixModal {...props({ mix: mix({ syncKey: undefined }) })} />);
    expect(container.querySelector('.mix-modal-actions')).toBeNull();
  });

  it('marks the primary action', () => {
    render(<MixModal {...props()} />);
    expect(screen.getByText('Sync')).toHaveClass('primary');
    expect(screen.getByText('Download')).not.toHaveClass('primary');
  });

  it('reports the whole action, so the caller can honour closeFirst', () => {
    // Download opens a second modal beneath this one, which would otherwise be
    // uninteractable — the flag has to survive the click.
    const p = props();
    render(<MixModal {...p} />);
    fireEvent.click(screen.getByText('Download'));
    expect(p.onAction).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'Download', closeFirst: true }),
    );
  });

  it('renders a sync panel when it is given one', () => {
    const { container } = render(
      <MixModal {...props({ syncStatus: <div className="sync-panel">syncing</div> })} />,
    );
    expect(container.querySelector('.sync-panel')).not.toBeNull();
  });

  it('closes on the backdrop and the × but not on the card', () => {
    const p = props();
    const { container } = render(<MixModal {...p} />);
    fireEvent.click(container.querySelector('.discover-mix-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Close'));
    fireEvent.click(container.querySelector('.modal-overlay')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });
});
