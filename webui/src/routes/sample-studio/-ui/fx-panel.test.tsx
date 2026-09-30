import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { RenderFx } from '../-sample-studio.types';

import { DEFAULT_FX } from '../-sample-studio.types';
import { FxPanel } from './fx-panel';

function renderPanel(fx: RenderFx = DEFAULT_FX, bpmKnown = true) {
  const onChange = vi.fn();
  render(<FxPanel fx={fx} onChange={onChange} bpmKnown={bpmKnown} disabled={false} />);
  return onChange;
}

describe('FxPanel', () => {
  it('renders the five FX controls with defaults', () => {
    renderPanel();
    expect(screen.getByRole('button', { name: /Peak normalize/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByRole('button', { name: /Reverse/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByLabelText('Fade length in milliseconds')).toHaveValue('5');
    expect(screen.getByRole('button', { name: /Space/ })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /Delay/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('toggles peak normalize and reverse', () => {
    const onChange = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Peak normalize/ }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_FX, normalize: true });

    fireEvent.click(screen.getByRole('button', { name: /Reverse/ }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_FX, reverse: true });
  });

  it('changes the fade amount', () => {
    const onChange = renderPanel();
    fireEvent.change(screen.getByLabelText('Fade length in milliseconds'), {
      target: { value: '10' },
    });
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_FX, fadeMs: 10 });
  });

  it('enables space and sets the tail length', () => {
    const onChange = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Space/ }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_FX, space: 0.5 });

    const withSpace = renderPanel({ ...DEFAULT_FX, space: 0.5 });
    fireEvent.change(screen.getByLabelText('Reverb length in seconds'), {
      target: { value: '1.2' },
    });
    expect(withSpace).toHaveBeenCalledWith({ ...DEFAULT_FX, space: 1.2 });
  });

  it('enables delay and edits time, feedback and mix', () => {
    const onChange = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Delay/ }));
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_FX,
      delay: { time: '1/4', feedback: 0.35, mix: 0.25 },
    });

    const withDelay = renderPanel({
      ...DEFAULT_FX,
      delay: { time: '1/4', feedback: 0.35, mix: 0.25 },
    });
    fireEvent.change(screen.getByLabelText('Delay time'), { target: { value: '1/8' } });
    expect(withDelay).toHaveBeenCalledWith({
      ...DEFAULT_FX,
      delay: { time: '1/8', feedback: 0.35, mix: 0.25 },
    });
    fireEvent.change(screen.getByLabelText('Delay feedback'), { target: { value: '0.5' } });
    expect(withDelay).toHaveBeenCalledWith({
      ...DEFAULT_FX,
      delay: { time: '1/4', feedback: 0.5, mix: 0.25 },
    });
  });

  it('disables delay without a known tempo and says so', () => {
    renderPanel(DEFAULT_FX, false);
    expect(screen.getByRole('button', { name: /Delay/ })).toBeDisabled();
    expect(screen.getByText(/needs the track's tempo/)).toBeInTheDocument();
  });
});
