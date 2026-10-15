import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RailCardActions } from './rail-card-actions';

afterEach(cleanup);

function setup(over: Partial<Parameters<typeof RailCardActions>[0]> = {}) {
  const props = {
    label: 'Kyougen',
    onPlay: vi.fn(),
    onQueue: vi.fn(),
    onAddToPlaylist: vi.fn(),
    ...over,
  };
  const cardClick = vi.fn();
  render(
    <div onClick={cardClick}>
      <RailCardActions {...props} />
    </div>,
  );
  return { props, cardClick };
}

describe('RailCardActions', () => {
  it('each button does its one thing and never also taps the card', () => {
    const { props, cardClick } = setup();
    fireEvent.click(screen.getByLabelText('Play Kyougen'));
    fireEvent.click(screen.getByLabelText('Add Kyougen to the queue'));
    fireEvent.click(screen.getByLabelText('Add Kyougen to a playlist'));
    expect(props.onPlay).toHaveBeenCalledTimes(1);
    expect(props.onQueue).toHaveBeenCalledTimes(1);
    expect(props.onAddToPlaylist).toHaveBeenCalledWith(
      screen.getByLabelText('Add Kyougen to a playlist'),
    );
    expect(cardClick).not.toHaveBeenCalled();
  });

  it('cannot start the same album twice while it is still loading', async () => {
    let finish: () => void = () => {};
    const onPlay = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    setup({ onPlay });
    const button = screen.getByLabelText('Play Kyougen');
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onPlay).toHaveBeenCalledTimes(1);
    // the other buttons stay live meanwhile
    expect(screen.getByLabelText('Add Kyougen to the queue')).not.toBeDisabled();
    await act(async () => finish());
    expect(button).not.toBeDisabled();
  });
});
