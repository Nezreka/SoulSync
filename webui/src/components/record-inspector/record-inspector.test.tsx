import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { RecordInspector } from './record-inspector';
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it('labels review snapshots separately when a track has no database row', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  render(
    <RecordInspector
      name="Unindexed file"
      recordKind="Track"
      extraRecord={{ 'Embedded tags (review snapshot)': { title: 'File title' } }}
      onClose={() => {}}
    />,
  );
  expect(await screen.findByText('Embedded tags (review snapshot)')).toBeTruthy();
  expect(screen.getByText(/no current database record/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});
it('restores keyboard focus after closing the generic inspector', async () => {
  const trigger = document.createElement('button');
  document.body.append(trigger);
  trigger.focus();
  const { unmount } = render(<RecordInspector name="File" recordKind="Track" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Close record' })).toHaveFocus());
  unmount();
  expect(trigger).toHaveFocus();
  trigger.remove();
});
it('does not reuse a previously loaded record after changing endpoints', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify({
            success: true,
            record: { title: url.endsWith('1') ? 'First' : 'Second' },
          }),
        ),
    ),
  );
  const { rerender } = render(
    <RecordInspector name="File" endpoint="/record/1" onClose={() => {}} />,
  );
  await screen.findByText('First');
  rerender(<RecordInspector name="File" endpoint="/record/2" onClose={() => {}} />);
  await screen.findByText('Second');
  expect(screen.queryByText('First')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'JSON' }));
  expect(screen.getByText('"Second"')).toBeTruthy();
});
