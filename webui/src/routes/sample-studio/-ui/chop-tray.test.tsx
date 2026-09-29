import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChopTray } from './chop-tray';

const onsets = [0.5, 1.0, 1.5, 2.0];

function renderTray(over: Partial<Parameters<typeof ChopTray>[0]> = {}) {
  const props = {
    onsets,
    inPoint: 0,
    outPoint: 2.5,
    onAuditionSlice: vi.fn(),
    onMergeSlices: vi.fn(),
    ...over,
  };
  const utils = render(<ChopTray {...props} />);
  return { ...utils, props };
}

describe('ChopTray', () => {
  it('splits the region at onsets and shows the slice count', () => {
    renderTray();
    // onsets 0.5/1.0/1.5/2.0 inside 0..2.5 -> 5 slices
    expect(screen.getByText('5', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('0:00.0–0:00.5')).toBeInTheDocument();
    expect(screen.getByText('0:02.0–0:02.5')).toBeInTheDocument();
  });

  it('renders nothing when the region is empty', () => {
    const { container } = renderTray({ inPoint: 1, outPoint: 1 });
    expect(container.firstChild).toBeNull();
  });

  it('selects slices on click and merges the selection into the loop', () => {
    const { props } = renderTray();
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
    const chips = screen.getAllByTitle(/click to select/);
    fireEvent.click(screen.getAllByTitle(/Audition 0:00.5/)[0]);
    expect(props.onAuditionSlice).toHaveBeenCalledTimes(1);
    expect(props.onAuditionSlice).toHaveBeenCalledWith(0.5, 1.0);
    expect(chips[1]).toHaveAttribute('data-selected', 'false');
  });

  it('Clear resets the selection', () => {
    renderTray();
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
    const chip = screen.getAllByTitle(/click to select/)[0];
    fireEvent.keyDown(chip, { key: 'Enter' });
    expect(chip).toHaveAttribute('data-selected', 'true');
  });
});
