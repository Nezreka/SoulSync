import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PitchTempoPanel } from './pitch-tempo-panel';

function renderPanel(over: Partial<Parameters<typeof PitchTempoPanel>[0]> = {}) {
  const props = {
    sourceBpm: 120,
    pitchSt: 0,
    targetBpm: null,
    onParamsChange: vi.fn(),
    rendering: false,
    previewEngine: null,
    mode: 'original' as const,
    onModeChange: vi.fn(),
    disabled: false,
    ...over,
  };
  render(<PitchTempoPanel {...props} />);
  return props;
}

describe('PitchTempoPanel', () => {
  it('is neutral at rest: Processed disabled, nudge hint shown', () => {
    renderPanel();
    expect(screen.getByRole('button', { name: 'Processed' })).toBeDisabled();
    expect(screen.getByText('Move pitch or tempo to hear a processed preview')).toBeInTheDocument();
  });

  it('becomes comparable once pitch or tempo moves', () => {
    renderPanel({ pitchSt: 2 });
    expect(screen.getByText('+2 st')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Processed' })).not.toBeDisabled();
    expect(
      screen.getByText('Processed preview ready — toggle above to compare'),
    ).toBeInTheDocument();
  });

  it('moves the pitch slider and resets it', () => {
    const props = renderPanel({ pitchSt: 2 });
    const slider = screen.getByLabelText('Pitch') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '4' } });
    expect(props.onParamsChange).toHaveBeenCalledWith(4, null);

    fireEvent.click(screen.getByTitle('Reset pitch'));
    expect(props.onParamsChange).toHaveBeenCalledWith(0, null);
  });

  it('reset pitch is disabled at zero', () => {
    renderPanel({ pitchSt: 0 });
    expect(screen.getByTitle('Reset pitch')).toBeDisabled();
  });

  it('sets a target BPM and resets it to the source', () => {
    const props = renderPanel({ sourceBpm: 120, targetBpm: 128 });
    const input = screen.getByLabelText('Target BPM') as HTMLInputElement;
    expect(input.value).toBe('128');
    fireEvent.change(input, { target: { value: '140' } });
    expect(props.onParamsChange).toHaveBeenCalledWith(0, 140);

    fireEvent.click(screen.getByTitle('Reset tempo'));
    expect(props.onParamsChange).toHaveBeenCalledWith(0, 120);
  });

  it('disables the BPM field when the source BPM is unknown', () => {
    renderPanel({ sourceBpm: null });
    expect(screen.getByLabelText('Target BPM')).toBeDisabled();
    expect(screen.getByText('BPM unknown — analyze first')).toBeInTheDocument();
    expect(screen.getByTitle('Reset tempo')).toBeDisabled();
  });

  it('toggles original/processed mode', () => {
    const props = renderPanel({ pitchSt: 3, mode: 'original' });
    fireEvent.click(screen.getByRole('button', { name: 'Processed' }));
    expect(props.onModeChange).toHaveBeenCalledWith('preview');
    fireEvent.click(screen.getByRole('button', { name: 'Original' }));
    expect(props.onModeChange).toHaveBeenCalledWith('original');
  });

  it('shows the rendering state and the engine note', () => {
    const { rerender } = render(<PitchTempoPanel {...baseProps({ rendering: true })} />);
    expect(screen.getByText('Rendering preview…')).toBeInTheDocument();
    rerender(<PitchTempoPanel {...baseProps({ previewEngine: 'rubberband' })} />);
    expect(screen.getByText(/Preview rendered \(Rubber Band\)/)).toBeInTheDocument();
  });

  function baseProps(over: Partial<Parameters<typeof PitchTempoPanel>[0]> = {}) {
    return {
      sourceBpm: 120 as number | null,
      pitchSt: 0,
      targetBpm: null as number | null,
      onParamsChange: vi.fn(),
      rendering: false,
      previewEngine: null as string | null,
      mode: 'original' as const,
      onModeChange: vi.fn(),
      disabled: false,
      ...over,
    };
  }
});
