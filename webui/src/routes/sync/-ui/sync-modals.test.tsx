/**
 * The modal mount: the mirrored vertical's windows, opened only when the
 * mirrored source is the one that's open.
 */

import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SyncModals as SyncModalsState } from '../-sync.use-modals';
import type { SourceVertical } from '../-sync.use-vertical';

import { SYNC_SOURCES } from '../-sync.sources';

/** Captures what the SourceModals instance was handed. */
const seen: { config: unknown; openId: string | null; mirroredSource?: string }[] = [];

vi.mock('./source-modals', () => ({
  SourceModals: (props: { config: unknown; openId: string | null; mirroredSource?: string }) => {
    seen.push({ config: props.config, openId: props.openId, mirroredSource: props.mirroredSource });
    return null;
  },
}));

const { SyncModals } = await import('./sync-modals');

const vertical = { states: {} } as unknown as SourceVertical;

function store(open: { source: string; sourceId: string } | null): SyncModalsState {
  return {
    open: open as SyncModalsState['open'],
    openModal: vi.fn(),
    close: vi.fn(),
    openIdFor: (source) => (open?.source === source ? open.sourceId : null),
  };
}

afterEach(() => {
  seen.length = 0;
});

describe('the mount', () => {
  it('renders the one mirrored host, with its source label', () => {
    render(
      <SyncModals
        vertical={vertical}
        modals={store(null)}
        standalone={false}
        mirroredSource="Tidal"
      />,
    );
    expect(seen).toHaveLength(1);
    expect(seen[0].config).toBe(SYNC_SOURCES.mirrored);
    expect(seen[0].openId).toBeNull();
    expect(seen[0].mirroredSource).toBe('Tidal');
  });

  it('opens it with the mirrored id', () => {
    render(
      <SyncModals
        vertical={vertical}
        modals={store({ source: 'mirrored', sourceId: 'mirrored_4' })}
        standalone={false}
      />,
    );
    expect(seen[0].openId).toBe('mirrored_4');
  });

  it('stays closed when some other source is open', () => {
    render(
      <SyncModals
        vertical={vertical}
        modals={store({ source: 'qobuz', sourceId: 'q1' })}
        standalone={false}
      />,
    );
    expect(seen[0].openId).toBeNull();
  });
});
