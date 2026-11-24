import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { longPress } from './long-press';

function Card({ onHold, onClick }: { onHold: () => void; onClick: () => void }) {
  return (
    <button type="button" {...longPress(onHold)} onClick={onClick}>
      card
    </button>
  );
}

describe('press and hold', () => {
  it('a held finger opens the menu, and the lift does not also open the card', () => {
    vi.useFakeTimers();
    const onHold = vi.fn();
    const onClick = vi.fn();
    render(<Card onHold={onHold} onClick={onClick} />);
    const el = screen.getByRole('button');
    fireEvent.pointerDown(el, { pointerType: 'touch', clientX: 10, clientY: 10 });
    vi.advanceTimersByTime(600);
    expect(onHold).toHaveBeenCalledTimes(1);
    fireEvent.pointerUp(el, { pointerType: 'touch' });
    fireEvent.click(el);
    expect(onClick).not.toHaveBeenCalled();
    // a plain tap still opens it
    fireEvent.pointerDown(el, { pointerType: 'touch', clientX: 10, clientY: 10 });
    fireEvent.pointerUp(el, { pointerType: 'touch' });
    fireEvent.click(el);
    expect(onClick).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('a scroll (the finger moving) is not a hold, and a mouse uses right-click', () => {
    vi.useFakeTimers();
    const onHold = vi.fn();
    render(<Card onHold={onHold} onClick={vi.fn()} />);
    const el = screen.getByRole('button');
    fireEvent.pointerDown(el, { pointerType: 'touch', clientX: 10, clientY: 10 });
    fireEvent.pointerMove(el, { pointerType: 'touch', clientX: 10, clientY: 60 });
    vi.advanceTimersByTime(600);
    fireEvent.pointerDown(el, { pointerType: 'mouse', clientX: 10, clientY: 10 });
    vi.advanceTimersByTime(600);
    expect(onHold).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
