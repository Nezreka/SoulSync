/**
 * The server-order modal, against pages-extra.js 385-446. Every class here is
 * the CSS contract for this dialog, so they are asserted as literals.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ServerOrderModal } from './server-order-modal';

const ORDER = [
  { title: 'Bonus', artist: 'Someone', thumb: 'http://art/1.jpg' },
  { title: 'Alright', artist: 'Kendrick' },
];

function renderModal(props: Record<string, unknown> = {}) {
  const onClose = vi.fn();
  const onAlign = vi.fn();
  render(
    <ServerOrderModal
      order={ORDER}
      serverType="plex"
      onClose={onClose}
      onAlign={onAlign}
      {...props}
    />,
  );
  return { onClose, onAlign };
}

describe('ServerOrderModal', () => {
  it('numbers the rows by the SERVER order and names the server (391, 400)', () => {
    renderModal();
    expect(document.querySelector('.server-order-h1')?.textContent).toBe('Plex playlist order');
    const rows = document.querySelectorAll('.server-order-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('.server-order-num')?.textContent).toBe('1');
    expect(rows[0].querySelector('.server-order-title')?.textContent).toBe('Bonus');
    expect(rows[0].querySelector('.server-order-artist')?.textContent).toBe('Someone');
  });

  it("falls back to 'Server' when the type is unknown (390)", () => {
    renderModal({ serverType: undefined });
    expect(document.querySelector('.server-order-h1')?.textContent).toBe('Server playlist order');
  });

  it('shows a ♫ placeholder without artwork, and when the artwork fails (395-397)', () => {
    renderModal();
    // Row 2 has no thumb at all.
    const rows = document.querySelectorAll('.server-order-row');
    expect(rows[1].querySelector('.server-order-art-ph')?.textContent).toBe('♫');
    expect(rows[1].querySelector('img')).toBeNull();

    // Row 1 has one — until it fails to load, when it becomes the same placeholder.
    const img = rows[0].querySelector('img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('http://art/1.jpg');
    fireEvent.error(img);
    expect(rows[0].querySelector('img')).toBeNull();
    expect(rows[0].querySelector('.server-order-art-ph')?.textContent).toBe('♫');
  });

  it("names an untitled track 'Unknown' but leaves a missing artist blank (403-404)", () => {
    renderModal({ order: [{}] });
    expect(document.querySelector('.server-order-title')?.textContent).toBe('Unknown');
    expect(document.querySelector('.server-order-artist')?.textContent).toBe('');
  });

  it('says so when the server has no tracks (441)', () => {
    renderModal({ order: [] });
    expect(screen.getByText('No server tracks.')).toBeInTheDocument();
    expect(document.querySelectorAll('.server-order-row')).toHaveLength(0);
  });

  it('offers the two align actions on the three supported servers (412)', () => {
    for (const serverType of ['plex', 'jellyfin', 'navidrome']) {
      const { unmount } = render(
        <ServerOrderModal
          order={ORDER}
          serverType={serverType}
          onClose={vi.fn()}
          onAlign={vi.fn()}
        />,
      );
      expect(document.querySelectorAll('.server-align-btn')).toHaveLength(2);
      unmount();
    }
  });

  it('hides them on a server with no reorder primitive (412)', () => {
    renderModal({ serverType: 'subsonic' });
    expect(document.querySelector('.server-order-foot')).toBeNull();
    expect(document.querySelectorAll('.server-align-btn')).toHaveLength(0);
    // …but the read-only list is still the point of the view.
    expect(document.querySelectorAll('.server-order-row')).toHaveLength(2);
  });

  it('the two buttons differ only in the extras choice (417-424)', () => {
    const { onAlign } = renderModal();
    const buttons = document.querySelectorAll('.server-align-btn');
    expect(buttons[0].querySelector('.server-align-btn-t')?.textContent).toBe('Mirror source');
    expect(buttons[0].querySelector('.server-align-btn-d')?.textContent).toBe(
      'reorder to match the source · remove server-only tracks',
    );
    expect(buttons[1].querySelector('.server-align-btn-t')?.textContent).toBe('Keep extras');
    expect(buttons[1].querySelector('.server-align-btn-d')?.textContent).toBe(
      'reorder to match the source · keep server-only tracks at the end',
    );

    fireEvent.click(buttons[0]);
    expect(onAlign).toHaveBeenCalledWith(false);
    fireEvent.click(buttons[1]);
    expect(onAlign).toHaveBeenCalledWith(true);
  });

  it('says plainly that aligning never adds the missing tracks (426)', () => {
    renderModal();
    expect(document.querySelector('.server-order-foot-note')?.textContent).toBe(
      "Missing tracks aren't added here — run a normal sync for those.",
    );
  });

  it('closes on the backdrop and the ×, but never from inside the dialog (433, 439, 444)', () => {
    const { onClose } = renderModal();
    fireEvent.click(document.querySelector('.server-order-dialog') as Element);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector('.server-order-list') as Element);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(document.querySelector('.server-order-close') as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(document.querySelector('#server-order-modal') as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
