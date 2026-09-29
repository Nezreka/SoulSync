import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChopTray } from './chop-tray';

const onsets = [0.5, 1.0, 1.5, 2.0];
// 120 BPM -> 2s bars; 60s -> 30 bars of suggestions to pick from.
const peaks = {
  buckets: 1500,
  duration_s: 60,
  min: Array.from({ length: 1500 }, () => -0.5),
  max: Array.from({ length: 1500 }, () => 0.5),
};

function renderTray(over: Partial<Parameters<typeof ChopTray>[0]> = {}) {
  const props = {
    onsets,
    inPoint: 0,
    outPoint: 2.5,
    bpm: 120 as number | null,
    durationS: 60,
    peaks,
    analysisReady: true,
    onAuditionSlice: vi.fn(),
    onMergeSlices: vi.fn(),
    onUseSuggestion: vi.fn(),
    ...over,
  };
  const utils = render(<ChopTray {...props} />);
  return { ...utils, props };
}

/** The slice firehose lives behind the toggle now. */
function openAllSlices() {
  fireEvent.click(screen.getByRole('button', { name: /All transient slices/ }));
}

describe('ChopTray', () => {
  it('shows suggested chops first when analysis is ready', () => {
    renderTray();
    expect(screen.getByText('Suggested chops')).toBeInTheDocument();
    const chips = screen.getAllByTitle(/click to make it the loop region/);
    expect(chips.length).toBeGreaterThan(0);
    expect(chips.length).toBeLessThanOrEqual(8);
    // suggestion chips are NOT the 1480-style firehose
    expect(screen.queryByText('0:00.0–0:00.5')).not.toBeInTheDocument();
  });

  it('clicking a suggestion makes it the loop region', () => {
    const { props } = renderTray();
    const chip = screen.getAllByTitle(/click to make it the loop region/)[0];
    fireEvent.click(chip);
    expect(props.onUseSuggestion).toHaveBeenCalledTimes(1);
    const [start, end] = vi.mocked(props.onUseSuggestion).mock.calls[0];
    expect(end).toBeGreaterThan(start);
  });

  it('auditions a suggestion without setting the region', () => {
    const { props } = renderTray();
    fireEvent.click(screen.getAllByTitle(/^Audition Bars/)[0]);
    expect(props.onAuditionSlice).toHaveBeenCalledTimes(1);
    expect(props.onUseSuggestion).not.toHaveBeenCalled();
  });

  it('shows a teaching empty state before analysis lands', () => {
    renderTray({ analysisReady: false });
    expect(screen.getByText(/chop by ear/)).toBeInTheDocument();
    expect(screen.queryByTitle(/click to make it the loop region/)).not.toBeInTheDocument();
  });

  it('hides the transient firehose behind a toggle', () => {
    renderTray();
    // onsets 0.5/1.0/1.5/2.0 inside 0..2.5 -> 5 slices, but hidden at first
    expect(screen.queryByText('0:00.0–0:00.5')).not.toBeInTheDocument();
    openAllSlices();
    expect(screen.getByText('5', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('0:00.0–0:00.5')).toBeInTheDocument();
    expect(screen.getByText('0:02.0–0:02.5')).toBeInTheDocument();
  });

  it('selects slices on click and merges the selection into the loop', () => {
    const { props } = renderTray();
    openAllSlices();
    const chips = screen.getAllByTitle(/click to select/);
    expect(chips).toHaveLength(5);

    const mergeBtn = screen.getByRole('button', { name: /Merge/ });
    expect(mergeBtn).toBeDisabled();

    fireEvent.click(chips[1]);
    fireEvent.click(chips[3]);
    expect(chips[1]).toHaveAttribute('data-selected', 'true');
    expect(chips[3]).toHaveAttribute('data-selected', 'true');
    expect(chips[0]).toHaveAttribute('data-selected', 'false');

    expect(mergeBtn).toHaveTextContent('Merge (2) → loop');
    fireEvent.click(mergeBtn);
    // merge spans from the first selected slice's start to the last's end
    expect(props.onMergeSlices).toHaveBeenCalledTimes(1);
    expect(props.onMergeSlices).toHaveBeenCalledWith(0.5, 2.0);
    // selection clears after a merge
    expect(screen.getByRole('button', { name: /Merge/ })).toBeDisabled();
  });

  it('auditions a slice without selecting it', () => {
    const { props } = renderTray();
    openAllSlices();
    const chips = screen.getAllByTitle(/click to select/);
    fireEvent.click(screen.getAllByTitle(/Audition 0:00.5/)[0]);
    expect(props.onAuditionSlice).toHaveBeenCalledTimes(1);
    expect(props.onAuditionSlice).toHaveBeenCalledWith(0.5, 1.0);
    expect(chips[1]).toHaveAttribute('data-selected', 'false');
  });

  it('Clear resets the selection', () => {
    renderTray();
    openAllSlices();
    const chips = screen.getAllByTitle(/click to select/);
    fireEvent.click(chips[0]);
    fireEvent.click(chips[2]);
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(chips[0]).toHaveAttribute('data-selected', 'false');
    expect(chips[2]).toHaveAttribute('data-selected', 'false');
    expect(screen.getByRole('button', { name: /Merge/ })).toBeDisabled();
  });

  it('keyboard Enter toggles selection', () => {
    renderTray();
    openAllSlices();
    const chip = screen.getAllByTitle(/click to select/)[0];
    fireEvent.keyDown(chip, { key: 'Enter' });
    expect(chip).toHaveAttribute('data-selected', 'true');
  });

  it('shows an empty note when the region has no slices', () => {
    renderTray({ inPoint: 1, outPoint: 1 });
    openAllSlices();
    expect(screen.getByText(/No slices in the current region/)).toBeInTheDocument();
  });
});
