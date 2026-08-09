/**
 * The 🔗 source-ref editor (auto-sync.js 2410-2421) — the prompt's question,
 * its pre-filled value, and the empty-value rejection.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { SourceRefModal } from './source-ref-modal';

const ROW = { id: 3, name: 'Road Trip', source: 'tidal' } as MirroredPlaylistRow;

afterEach(() => {
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
});

function open(row: MirroredPlaylistRow = ROW, currentRef = 'tp1') {
  const onClose = vi.fn();
  const onSubmit = vi.fn();
  render(
    <SourceRefModal row={row} currentRef={currentRef} onClose={onClose} onSubmit={onSubmit} />,
  );
  return {
    onClose,
    onSubmit,
    input: document.querySelector('.mirrored-source-ref-input') as HTMLInputElement,
  };
}

describe('SourceRefModal', () => {
  it('asks the vanilla question and pre-fills the current ref', () => {
    const { input } = open();
    expect(
      screen.getByText('Update original playlist ID or URL for "Road Trip"'),
    ).toBeInTheDocument();
    expect(input.value).toBe('tp1');
  });

  it('link-sourced mirrors are asked for a URL instead', () => {
    open({ id: 4, name: 'Mix', source: 'youtube' } as MirroredPlaylistRow, '');
    expect(screen.getByText('Update original playlist URL for "Mix"')).toBeInTheDocument();
  });

  it('Save submits the TRIMMED value', () => {
    const { onSubmit, input } = open();
    fireEvent.change(input, { target: { value: '  https://tidal/p/9  ' } });
    fireEvent.click(screen.getByText('Save'));
    expect(onSubmit).toHaveBeenCalledWith('https://tidal/p/9');
  });

  it('Enter submits too', () => {
    const { onSubmit, input } = open();
    fireEvent.change(input, { target: { value: ' abc ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledWith('abc');
  });

  it('an empty or whitespace value is rejected, not submitted (2417-2421)', () => {
    window.showToast = vi.fn() as typeof window.showToast;
    const { onSubmit, input } = open();
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.click(screen.getByText('Save'));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('Source link or ID is required', 'error');
  });

  it('Cancel closes without submitting', () => {
    const { onClose, onSubmit } = open();
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('Escape closes — the vanilla prompt’s null return (2415)', () => {
    const { onClose, onSubmit, input } = open();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('the backdrop closes, a click inside does not', () => {
    const { onClose } = open();
    const overlay = document.querySelector('#mirrored-source-ref-modal')!;
    fireEvent.click(overlay.firstChild as Element);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
