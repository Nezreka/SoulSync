import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { SourceVertical } from '../-sync.use-vertical';

import { SYNC_SOURCES } from '../-sync.sources';
import { SourceModals } from './source-modals';

vi.mock('./discovery-modal', () => ({
  DiscoveryModal: ({ onRediscover }: { onRediscover: () => void }) => (
    <button onClick={onRediscover}>Rediscover</button>
  ),
}));
vi.mock('./fix-modal', () => ({ FixModal: () => null }));
afterEach(cleanup);

it.each([true, false])('restarts discovery only after a successful reset (%s)', async (success) => {
  const startDiscovery = vi.fn();
  const resetDiscovery = vi.fn().mockResolvedValue(success);
  const vertical = {
    states: { p: { phase: 'discovered', playlist: { name: 'Test', tracks: [] } } },
    resetDiscovery,
    startDiscovery,
  } as unknown as SourceVertical;
  render(
    <SourceModals
      config={SYNC_SOURCES.mirrored}
      vertical={vertical}
      openId="p"
      onClose={vi.fn()}
      standalone={false}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Rediscover' }));
  await waitFor(() => expect(resetDiscovery).toHaveBeenCalledWith('p'));
  // The render still captures the old discovered state when the request resolves.
  await waitFor(() => expect(startDiscovery).toHaveBeenCalledTimes(success ? 1 : 0));
  if (success) expect(startDiscovery).toHaveBeenCalledWith('p', undefined);
});
