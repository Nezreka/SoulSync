/**
 * #1934: a click anywhere on a source card imported it, so the Import button
 * meant nothing. Only the button imports now; the rest of the card shows the
 * songs first.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ImportCard } from './import-card';
import { previewTrack } from './import-preview-sheet';

const songs = [
  previewTrack({ track_name: 'Nightcall', artist_name: 'Kavinsky', duration_ms: 258000 }),
  previewTrack({ name: 'Roads', artists: [{ name: 'Portishead' }], album: { name: 'Dummy' } }),
];

function card(overrides: Partial<Parameters<typeof ImportCard>[0]> = {}) {
  const onAction = vi.fn();
  const loadPreview = vi.fn(async () => songs);
  render(
    <ImportCard
      name="Road Trip"
      meta="2 tracks"
      status="new"
      onAction={onAction}
      loadPreview={loadPreview}
      {...overrides}
    />,
  );
  return { onAction, loadPreview };
}

describe('ImportCard (#1934)', () => {
  it('a click on the card shows the songs and imports nothing', async () => {
    const { onAction, loadPreview } = card();

    fireEvent.click(screen.getByLabelText("See what's in Road Trip"));

    expect(await screen.findByText('Nightcall')).toBeInTheDocument();
    expect(screen.getByText('Kavinsky')).toBeInTheDocument();
    expect(screen.getByText('4:18')).toBeInTheDocument();
    expect(screen.getByText('Portishead · Dummy')).toBeInTheDocument();
    expect(screen.getByText('2 songs')).toBeInTheDocument();
    expect(onAction).not.toHaveBeenCalled();
    expect(loadPreview).toHaveBeenCalledTimes(1);
  });

  it('the Import button imports straight away', () => {
    const { onAction, loadPreview } = card();

    fireEvent.click(screen.getByLabelText('Import Road Trip into SoulSync'));

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(loadPreview).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Import in the sheet imports once and closes it', async () => {
    const { onAction } = card();
    fireEvent.click(screen.getByLabelText("See what's in Road Trip"));
    await screen.findByText('Nightcall');

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));

    expect(onAction).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('the sheet offers your own copy when the card does', async () => {
    const onCopy = vi.fn();
    card({ onCopy });
    fireEvent.click(screen.getByLabelText("See what's in Road Trip"));

    fireEvent.click(await screen.findByRole('button', { name: 'Keep my own copy' }));

    expect(onCopy).toHaveBeenCalledTimes(1);
  });

  it('closing the sheet imports nothing', async () => {
    const { onAction } = card();
    fireEvent.click(screen.getByLabelText("See what's in Road Trip"));
    await screen.findByText('Nightcall');

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onAction).not.toHaveBeenCalled();
  });

  it('a card already in SoulSync still opens it on click', () => {
    const { onAction, loadPreview } = card({ status: 'imported' });

    fireEvent.click(screen.getByLabelText('Road Trip'));

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(loadPreview).not.toHaveBeenCalled();
  });

  it("a card that can't load its songs imports on click like before", () => {
    const { onAction } = card({ loadPreview: undefined });

    fireEvent.click(screen.getByLabelText('Road Trip'));

    expect(onAction).toHaveBeenCalledTimes(1);
  });
});
