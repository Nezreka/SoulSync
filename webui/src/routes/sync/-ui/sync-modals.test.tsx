/**
 * The modal mount. The risk this covers is wiring: nine SourceModals fed from
 * one store, and handing the wrong source's id to one of them opens the wrong
 * playlist's modal — or nine at once.
 */

import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SyncModals as SyncModalsState } from '../-sync.use-modals';
import type { SyncVerticals } from '../-sync.verticals';

import { SYNC_SOURCES } from '../-sync.sources';
import { SYNC_VERTICAL_IDS } from '../-sync.verticals';

/** Captures what each SourceModals instance was handed. */
const seen: { config: unknown; openId: string | null; mirroredSource?: string }[] = [];

vi.mock('./source-modals', () => ({
  SourceModals: (props: { config: unknown; openId: string | null; mirroredSource?: string }) => {
    seen.push({ config: props.config, openId: props.openId, mirroredSource: props.mirroredSource });
    return null;
  },
}));

const { SyncModals } = await import('./sync-modals');

function verticals(): SyncVerticals {
  return Object.fromEntries(SYNC_VERTICAL_IDS.map((id) => [id, { states: {} }])) as SyncVerticals;
}

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
  it('renders one modal host per vertical, in the registry order', () => {
    // Driven by SYNC_VERTICAL_IDS so a source added to the table cannot end up
    // with no modal — the failure mode of nine hand-written blocks.
    render(<SyncModals verticals={verticals()} modals={store(null)} standalone={false} />);
    expect(seen).toHaveLength(SYNC_VERTICAL_IDS.length);
    expect(seen.map((s) => s.config)).toEqual(SYNC_VERTICAL_IDS.map((id) => SYNC_SOURCES[id]));
  });

  it('opens ONLY the source that is open', () => {
    render(
      <SyncModals
        verticals={verticals()}
        modals={store({ source: 'qobuz', sourceId: 'q1' })}
        standalone={false}
      />,
    );
    const open = seen.filter((s) => s.openId !== null);
    expect(open).toHaveLength(1);
    expect(open[0].config).toBe(SYNC_SOURCES.qobuz);
    expect(open[0].openId).toBe('q1');
  });

  it('leaves every modal closed when nothing is open', () => {
    render(<SyncModals verticals={verticals()} modals={store(null)} standalone={false} />);
    expect(seen.every((s) => s.openId === null)).toBe(true);
  });

  it('gives mirroredSource to MIRRORED alone', () => {
    // Every other vertical knows its own name from its config; handing them a
    // mirrored row's source would mislabel them.
    render(
      <SyncModals
        verticals={verticals()}
        modals={store(null)}
        standalone={false}
        mirroredSource="Tidal"
      />,
    );
    const withSource = seen.filter((s) => s.mirroredSource !== undefined);
    expect(withSource).toHaveLength(1);
    expect(withSource[0].config).toBe(SYNC_SOURCES.mirrored);
    expect(withSource[0].mirroredSource).toBe('Tidal');
  });
});
