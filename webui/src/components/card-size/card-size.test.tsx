import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { CardSizeSlider, cardGridStyle, useCardSize, type CardSizeRange } from './card-size';

const RANGE: CardSizeRange = { min: 120, max: 320, base: 200 };

function Harness({ storageKey }: { storageKey: string }) {
  const [size, setSize] = useCardSize(storageKey, RANGE);
  const [, rerender] = useState(0);
  return (
    <div>
      <CardSizeSlider label="Artist card size" size={size} range={RANGE} onChange={setSize} />
      <div data-testid="grid" style={cardGridStyle(size)} />
      <button type="button" onClick={() => rerender((n) => n + 1)}>
        again
      </button>
    </div>
  );
}

describe('#2108 card size', () => {
  it('leaves a grid its own columns until a size is picked', () => {
    expect(cardGridStyle(null)).toBeUndefined();
    // never wider than the row: no sideways scroll on a phone
    expect(cardGridStyle(260)?.gridTemplateColumns).toBe(
      'repeat(auto-fill, minmax(min(260px, 100%), 1fr))',
    );
  });

  it('keeps each grid its own size across a reload, and resets', () => {
    const first = render(<Harness storageKey="t:artists" />);
    fireEvent.change(screen.getByLabelText('Artist card size'), { target: { value: '280' } });
    expect(screen.getByTestId('grid').style.gridTemplateColumns).toContain('280px');
    first.unmount();

    // a fresh mount (a reload, or back from the table view) reads it back
    const second = render(<Harness storageKey="t:artists" />);
    expect(screen.getByTestId('grid').style.gridTemplateColumns).toContain('280px');
    second.unmount();

    // another grid is untouched
    const albums = render(<Harness storageKey="t:albums" />);
    expect(screen.getByTestId('grid').style.gridTemplateColumns).toBe('');
    albums.unmount();

    render(<Harness storageKey="t:artists" />);
    fireEvent.click(screen.getByRole('button', { name: 'Reset artist card size' }));
    expect(screen.getByTestId('grid').style.gridTemplateColumns).toBe('');
    expect(window.localStorage.getItem('t:artists')).toBeNull();
  });

  it('keeps a stored size inside the range', () => {
    window.localStorage.setItem('t:big', '9000');
    render(<Harness storageKey="t:big" />);
    expect(screen.getByTestId('grid').style.gridTemplateColumns).toContain('320px');
  });
});
