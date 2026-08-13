import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SourcesModalProps } from './sources-modals';

import { YourAlbumsSourcesModal, YourArtistsSourcesModal } from './sources-modals';

/**
 * The two sources modals share one view, so most assertions run against both —
 * what CANNOT be shared is each variant's vanilla identity (overlay id, row
 * data attribute, toggle id prefix, source list), which is exactly where a
 * copy-paste port would drift.
 */

afterEach(cleanup);

function props(over: Partial<SourcesModalProps> = {}): SourcesModalProps {
  return {
    state: { spotify: true, tidal: false, deezer: true, discogs: false, lastfm: true },
    connected: ['spotify', 'deezer', 'lastfm'],
    onToggle: vi.fn(),
    onSave: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
}

const VARIANTS = [
  {
    name: 'Your Albums',
    Modal: YourAlbumsSourcesModal,
    overlayId: 'ya-albums-sources-modal-overlay',
    dataAttr: 'data-yaa-source',
    togglePrefix: 'yaa-toggle-',
    ids: ['spotify', 'tidal', 'deezer', 'discogs'],
    labels: ['Spotify', 'Tidal', 'Deezer', 'Discogs'],
    title: 'Your Albums Sources',
    noun: 'albums',
  },
  {
    name: 'Your Artists',
    Modal: YourArtistsSourcesModal,
    overlayId: 'ya-sources-modal-overlay',
    dataAttr: 'data-source',
    togglePrefix: 'ya-toggle-',
    ids: ['spotify', 'tidal', 'lastfm', 'deezer'],
    labels: ['Spotify', 'Tidal', 'Last.fm', 'Deezer'],
    title: 'Your Artists Sources',
    noun: 'artists',
  },
] as const;

describe.each(VARIANTS)(
  '$name sources modal',
  ({ Modal, overlayId, dataAttr, togglePrefix, ids, labels, title, noun }) => {
    it('renders its OWN overlay id, title and copy', () => {
      const { container } = render(<Modal {...props()} />);
      const overlay = container.querySelector(`#${overlayId}`)!;
      expect(overlay).toHaveClass('modal-overlay');
      expect(overlay.querySelector('.ya-sources-modal h2')!.textContent).toBe(title);
      expect(overlay.querySelector('.ya-sources-desc')!.textContent).toBe(
        `Choose which connected services contribute ${noun} to this section.`,
      );
    });

    it('renders its four sources in vanilla order, under its OWN data attribute', () => {
      const { container } = render(<Modal {...props()} />);
      const rows = [...container.querySelectorAll('.ya-sources-list .ya-source-row')];
      expect(rows.map((r) => r.getAttribute(dataAttr))).toEqual([...ids]);
      expect(rows.map((r) => r.querySelector('.ya-source-name')!.textContent)).toEqual([...labels]);
    });

    it('marks disconnected rows and words both statuses', () => {
      const { container } = render(<Modal {...props()} />);
      const row = (id: string) => container.querySelector(`[${dataAttr}="${id}"]`)!;
      expect(row('spotify')).not.toHaveClass('disconnected');
      expect(row('tidal')).toHaveClass('disconnected');
      expect(row('spotify').querySelector('.ya-source-status')!.textContent).toBe('Connected');
      expect(row('tidal').querySelector('.ya-source-status')!.textContent).toBe('Not connected');
    });

    it('reflects enablement on prefix-idd toggle buttons', () => {
      const { container } = render(<Modal {...props()} />);
      const spotify = container.querySelector(`#${togglePrefix}spotify`)!;
      const tidal = container.querySelector(`#${togglePrefix}tidal`)!;
      expect(spotify).toHaveClass('ya-source-toggle', 'on');
      expect(tidal).toHaveClass('ya-source-toggle');
      expect(tidal).not.toHaveClass('on');
    });

    it('forwards a row click ONCE — including on a disconnected row', () => {
      // The vanilla wires onclick on disconnected rows too; refusing (and the
      // albums/artists hint difference) is the module reducer's decision, not
      // markup's.
      const p = props();
      const { container } = render(<Modal {...p} />);
      fireEvent.click(container.querySelector(`[${dataAttr}="tidal"]`)!);
      expect(p.onToggle).toHaveBeenCalledExactlyOnceWith('tidal');
    });

    it('fires ONE toggle per button click, not a second via the row', () => {
      // event.stopPropagation() (1646/5651): without it every button click
      // would toggle twice and the switch would appear dead.
      const p = props();
      const { container } = render(<Modal {...p} />);
      fireEvent.click(container.querySelector(`#${togglePrefix}spotify`)!);
      expect(p.onToggle).toHaveBeenCalledExactlyOnceWith('spotify');
    });

    it('closes on the backdrop and Cancel, but NOT on a click inside', () => {
      const p = props();
      const { container } = render(<Modal {...p} />);
      fireEvent.click(container.querySelector('.ya-sources-modal')!);
      expect(p.onClose).not.toHaveBeenCalled();
      fireEvent.click(container.querySelector(`#${overlayId}`)!);
      expect(p.onClose).toHaveBeenCalledTimes(1);
      fireEvent.click(container.querySelector('.ya-sources-cancel-btn')!);
      expect(p.onClose).toHaveBeenCalledTimes(2);
    });

    it('saves from the footer', () => {
      const p = props();
      const { container } = render(<Modal {...p} />);
      fireEvent.click(container.querySelector('.ya-sources-save-btn')!);
      expect(p.onSave).toHaveBeenCalledOnce();
      expect(p.onClose).not.toHaveBeenCalled();
    });
  },
);
