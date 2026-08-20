import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useModalA11y } from './use-modal-a11y';

/**
 * Eight Library v2 modals declared `aria-modal="true"` and implemented none of
 * what that promises (frontend-audit FE-04/FE-05). `aria-modal` without a trap
 * is worse than neither: a screen reader hides the rest of the page, and Tab
 * then walks focus into content the user can no longer perceive.
 */

function Modal({ onClose, empty = false }: { onClose: () => void; empty?: boolean }) {
  const ref = useModalA11y<HTMLDivElement>(onClose);
  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Test">
      {empty ? null : (
        <>
          <button type="button">first</button>
          <button type="button">middle</button>
          <button type="button">last</button>
        </>
      )}
    </div>
  );
}

afterEach(cleanup);

describe('useModalA11y', () => {
  it('moves focus to the first focusable element on mount', () => {
    render(<Modal onClose={vi.fn()} />);
    expect(document.activeElement?.textContent).toBe('first');
  });

  it('focuses the container itself when there is nothing else to focus', () => {
    render(<Modal onClose={vi.fn()} empty />);
    expect((document.activeElement as HTMLElement)?.getAttribute('role')).toBe('dialog');
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<Modal onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('wraps Tab from the last element back to the first', () => {
    render(<Modal onClose={vi.fn()} />);
    screen.getByText('last').focus();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Tab' });
    expect(document.activeElement?.textContent).toBe('first');
  });

  it('wraps Shift+Tab from the first element to the last', () => {
    render(<Modal onClose={vi.fn()} />);
    screen.getByText('first').focus();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Tab', shiftKey: true });
    expect(document.activeElement?.textContent).toBe('last');
  });

  it('restores focus to whatever opened it', () => {
    const opener = document.createElement('button');
    opener.textContent = 'open';
    document.body.appendChild(opener);
    opener.focus();

    const view = render(<Modal onClose={vi.fn()} />);
    expect(document.activeElement?.textContent).toBe('first');
    view.unmount();

    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});
