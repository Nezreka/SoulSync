import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoverMix } from '../-discover.mixes';

import { CompactPlaylist, MixModal, MixSelectionBarView } from './mix-modal';

/**
 * The compact track list and the mix modal.
 *
 * `selectable` is the axis the rows turn on; the modal's own risk is different:
 * its first version was invented wholesale and passed a full mutation pass,
 * because its tests asserted the invention. Everything below the rows now pins
 * the VANILLA shell — `.mix-modal` in `#mix-modal-overlay`, the eyebrow
 * subtitle, `btn--sm` actions with the sync id, and the selbar's real ids.
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

  it('reports a toggle and a play by index', () => {
    const onToggle = vi.fn();
    const onPlay = vi.fn();
    const { container } = render(
      <CompactPlaylist
        tracks={[track(), track({ name: 'Tha' })]}
        selectable
        onToggle={onToggle}
        onPlay={onPlay}
      />,
    );
    fireEvent.click([...container.querySelectorAll('.track-compact-check')][1]);
    fireEvent.click([...container.querySelectorAll('.track-compact-play')][1]);
    expect(onToggle).toHaveBeenCalledWith(1);
    expect(onPlay).toHaveBeenCalledWith(1);
  });

  // M02. The row button said "Preview" and the page passed it () => {}, so
  // every one of them was dead. It plays the whole track, so it says Play, and
  // it says WHICH track.
  it('the row button is a named Play, not an anonymous Preview', () => {
    const { container } = render(
      <CompactPlaylist tracks={[track()]} selectable onPlay={vi.fn()} />,
    );
    const btn = container.querySelector('.track-compact-play')!;
    expect(btn.getAttribute('aria-label')).toBe('Play Xtal');
    expect(btn.getAttribute('title')).toBe('Play Xtal');
  });

  it('a resolving row cannot be fired twice', () => {
    const onPlay = vi.fn();
    const { container } = render(
      <CompactPlaylist
        tracks={[track(), track({ name: 'Tha' })]}
        selectable
        onPlay={onPlay}
        playingIndex={1}
      />,
    );
    const rows = [...container.querySelectorAll('.track-compact-play')] as HTMLButtonElement[];
    expect(rows[1].disabled).toBe(true);
    expect(rows[0].disabled).toBe(false);
    fireEvent.click(rows[1]);
    expect(onPlay).not.toHaveBeenCalled();
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
    onClearSelection: vi.fn(),
    onDownloadSelected: vi.fn(),
    ...over,
  });

  it('keeps the ids the vanilla updater writes into', () => {
    const { container } = render(<MixSelectionBarView {...bar()} />);
    for (const sel of [
      '#mix-modal-selbar.mix-modal-selbar',
      '.mix-selbar-all #mix-select-all',
      '#mix-sel-count.mix-sel-count',
      '.mix-selbar-spacer',
      '#mix-dl-selected',
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
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

  it('ticks select-all only when everything is selected, never on empty', () => {
    const { container, rerender } = render(<MixSelectionBarView {...bar({ selected: [0, 1] })} />);
    const box = () => container.querySelector('#mix-select-all') as HTMLInputElement;
    expect(box().checked).toBe(false);
    rerender(<MixSelectionBarView {...bar({ selected: [0, 1, 2] })} />);
    expect(box().checked).toBe(true);
    // 0 === 0 would otherwise show select-all ticked on a list with no rows.
    rerender(<MixSelectionBarView {...bar({ total: 0, selected: [] })} />);
    expect(box().checked).toBe(false);
  });

  it('selects everything, clears everything — as DIFFERENT actions', () => {
    // Clear exists even when nothing is "all": it unticks whatever subset is
    // ticked, which select-all(false) only does from the fully-ticked state.
    const p = bar({ selected: [1] });
    const { container } = render(<MixSelectionBarView {...p} />);
    fireEvent.click(container.querySelector('#mix-select-all')!);
    expect(p.onSelectAll).toHaveBeenCalledWith([0, 1, 2]);
    fireEvent.click(screen.getByText('Clear'));
    expect(p.onClearSelection).toHaveBeenCalled();
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
    onClearSelection: vi.fn(),
    onToggleTrack: vi.fn(),
    onPlayTrack: vi.fn(),
    onDownloadSelected: vi.fn(),
    ...over,
  });

  it('renders the vanilla shell: overlay id, .mix-modal, header, body id', () => {
    const { container } = render(<MixModal {...props()} />);
    for (const sel of [
      '#mix-modal-overlay.modal-overlay',
      '.mix-modal',
      '.mix-modal-header',
      '.mix-modal-actions',
      '.mix-modal-close',
      '#mix-modal-tracks.mix-modal-body',
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
  });

  it('puts the subtitle ABOVE the title as an eyebrow, falling back to Mix', () => {
    const { container, rerender } = render(
      <MixModal {...props({ mix: mix({ subtitle: 'by ListenBrainz' }) })} />,
    );
    const header = container.querySelector('.mix-modal-header > div')!;
    expect(header.children[0]).toHaveClass('mix-modal-subtitle');
    expect(header.children[0].textContent).toBe('by ListenBrainz');
    expect(header.children[1]).toHaveClass('mix-modal-title');
    expect(header.children[1].textContent).toBe('Your Mix');
    rerender(<MixModal {...props()} />);
    expect(container.querySelector('.mix-modal-subtitle')!.textContent).toBe('Mix');
  });

  it('leaves the meta EMPTY until a lazy mix has tracks', () => {
    // '' rather than '0 tracks' (4981); the count fills in after the fetch.
    const { container, rerender } = render(<MixModal {...props({ tracks: undefined })} />);
    expect(container.querySelector('.mix-modal-meta')!.textContent).toBe('');
    rerender(<MixModal {...props()} />);
    expect(container.querySelector('.mix-modal-meta')!.textContent).toBe('1 tracks');
  });

  it('builds Download and Sync from a syncKey, in btn--sm classes', () => {
    render(<MixModal {...props()} />);
    expect(screen.getByText('Download')).toHaveClass('btn', 'btn--sm', 'btn--secondary');
    expect(screen.getByText('Sync')).toHaveClass('btn', 'btn--sm', 'btn--primary');
  });

  it('gives the SYNC button the id the live poller re-enables it by', () => {
    // your_mix → your-mix (underscores to hyphens, 4943).
    render(<MixModal {...props()} />);
    expect(screen.getByText('Sync')).toHaveAttribute('id', 'your-mix-sync-btn');
    expect(screen.getByText('Download')).not.toHaveAttribute('id');
  });

  it("keeps a mix's own actions, always led by Play", () => {
    const { container, rerender } = render(
      <MixModal {...props({ mix: mix({ actions: [{ label: 'Rebuild', onclick: 'x' }] }) })} />,
    );
    expect(screen.getByText('Rebuild')).toBeInTheDocument();
    expect(screen.getByText('▶ Play')).toBeInTheDocument();
    expect(screen.queryByText('Sync')).toBeNull();
    rerender(<MixModal {...props({ mix: mix({ syncKey: undefined }) })} />);
    // play + close: even an action-less mix is listenable now
    expect(container.querySelectorAll('.mix-modal-actions button')).toHaveLength(2);
  });

  it('reports the whole action, so the caller can honour closeFirst', () => {
    const p = props();
    render(<MixModal {...p} />);
    fireEvent.click(screen.getByText('Download'));
    expect(p.onAction).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'Download', closeFirst: true }),
    );
  });

  it('renders the generic sync block from the statusBase when syncing', () => {
    const { container } = render(
      <MixModal
        {...props({
          syncing: true,
          syncProgress: { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 },
        })}
      />,
    );
    expect(container.querySelector('#your-mix-sync-status')).not.toBeNull();
    expect(container.querySelector('#your-mix-sync-percentage')!.textContent).toBe('50');
  });

  it('lets a section REPLACE the sync block with its own markup', () => {
    // ListenBrainz syncs write into -sync-total/-sync-matched spans; handing it
    // the generic block would leave the poller writing into nothing.
    const { container } = render(
      <MixModal
        {...props({
          syncing: true,
          syncStatusOverride: <div className="lb-status">lb</div>,
        })}
      />,
    );
    expect(container.querySelector('.lb-status')).not.toBeNull();
    expect(container.querySelector('#your-mix-sync-status')).toBeNull();
  });

  it('hides the selection bar until there are tracks to select', () => {
    const { container, rerender } = render(<MixModal {...props({ tracks: undefined })} />);
    expect(container.querySelector('#mix-modal-selbar')).toBeNull();
    rerender(<MixModal {...props({ tracks: [] })} />);
    expect(container.querySelector('#mix-modal-selbar')).toBeNull();
    rerender(<MixModal {...props()} />);
    expect(container.querySelector('#mix-modal-selbar')).not.toBeNull();
  });

  it('always renders its rows SELECTABLE', () => {
    const { container } = render(<MixModal {...props()} />);
    expect(container.querySelector('.track-compact-check')).not.toBeNull();
  });

  it('shows loading and failure as the shared empty blocks', () => {
    const { container, rerender } = render(
      <MixModal {...props({ tracks: undefined, loading: true })} />,
    );
    expect(container.querySelector('.discover-empty p')!.textContent).toBe('Loading tracks…');
    rerender(<MixModal {...props({ tracks: undefined, error: true })} />);
    expect(container.querySelector('.discover-empty p')!.textContent).toBe('Failed to load tracks');
  });

  it('closes on the backdrop and the ✕ but not on the card', () => {
    const p = props();
    const { container } = render(<MixModal {...p} />);
    fireEvent.click(container.querySelector('.mix-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Close'));
    fireEvent.click(container.querySelector('#mix-modal-overlay')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });

  // ── M03: the modal contract ────────────────────────────────────────────────
  // Escape did nothing, the thing had no dialog role, focus stayed on the card
  // behind it and the page underneath kept scrolling.

  it('is a labelled modal dialog', () => {
    const { container } = render(<MixModal {...props()} />);
    const dialog = container.querySelector('.mix-modal')!;
    expect(dialog).toHaveAttribute('role', 'dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const labelledBy = dialog.getAttribute('aria-labelledby')!;
    expect(document.getElementById(labelledBy)!.textContent).toBe('Your Mix');
  });

  it('Escape closes it', () => {
    const p = props();
    render(<MixModal {...p} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(p.onClose).toHaveBeenCalled();
  });

  it('takes focus on open and gives it back to the opener on close', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    const { container, unmount } = render(<MixModal {...props()} />);
    expect(container.querySelector('.mix-modal')!.contains(document.activeElement)).toBe(true);

    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('keeps Tab inside the dialog', () => {
    const { container } = render(<MixModal {...props()} />);
    const dialog = container.querySelector('.mix-modal') as HTMLElement;
    const items = [...dialog.querySelectorAll<HTMLElement>('button, input')];
    const first = items[0];
    const last = items[items.length - 1];

    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  // The one control that said nothing at all while a 50-track mix resolved.
  it('the Play action says it is starting, and cannot be fired twice', () => {
    const p = props({ playing: true });
    render(<MixModal {...p} />);
    const play = screen.getByText('Starting…') as HTMLButtonElement;
    expect(play.disabled).toBe(true);
    fireEvent.click(play);
    expect(p.onAction).not.toHaveBeenCalled();
    // Only the play action is busy; Download and Sync stay live.
    expect((screen.getByText('Download') as HTMLButtonElement).disabled).toBe(false);
  });

  it('locks the page behind it while open', () => {
    const { unmount } = render(<MixModal {...props()} />);
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).not.toBe('hidden');
  });
});
