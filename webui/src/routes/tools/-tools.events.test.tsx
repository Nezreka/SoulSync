/**
 * The socket → React seam.
 *
 * The two pure helpers here encode the media-scan bugs P6 fixed, so they get
 * literal assertions rather than anything derived from the module.
 */

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  MEDIA_SCAN_EVENT,
  REPAIR_PROGRESS_EVENT,
  REPAIR_STATUS_EVENT,
  isMediaScanCompletion,
  mediaScanStatusKey,
  useMediaScanEvent,
  useRepairProgressEvent,
  useRepairStatusEvent,
} from './-tools.events';

afterEach(cleanup);

describe('event names', () => {
  it('match the names core.js dispatches', () => {
    expect(REPAIR_STATUS_EVENT).toBe('ss:repair-status');
    expect(REPAIR_PROGRESS_EVENT).toBe('ss:repair-progress');
    expect(MEDIA_SCAN_EVENT).toBe('ss:media-scan');
  });
});

describe('mediaScanStatusKey', () => {
  it('keys off `status`, never the phantom `is_scanning`', () => {
    expect(mediaScanStatusKey({ status: 'scanning' })).toBe('scanning');
    expect(mediaScanStatusKey({ status: 'idle' })).toBe('idle');
    expect(mediaScanStatusKey({ status: 'scheduled' })).toBe('scheduled');
    // A payload carrying ONLY the phantom field is not a scan in progress —
    // the real emitter never sends it, and reading it is what broke the vanilla.
    expect(mediaScanStatusKey({ is_scanning: true })).toBe('unknown');
    expect(mediaScanStatusKey(null)).toBe('unknown');
  });
});

describe('isMediaScanCompletion', () => {
  it('only a scanning → idle transition is a completion', () => {
    expect(isMediaScanCompletion('scanning', 'idle')).toBe(true);
  });

  it('a bare idle frame is NOT a completion', () => {
    // The server pushes scan:media every 2s regardless of activity, so this is
    // the frame every page load receives. Treating it as a finished scan is what
    // popped a success toast on load.
    expect(isMediaScanCompletion(null, 'idle')).toBe(false);
    expect(isMediaScanCompletion('idle', 'idle')).toBe(false);
    expect(isMediaScanCompletion('scheduled', 'idle')).toBe(false);
  });

  it('is not a completion while still scanning or merely scheduled', () => {
    expect(isMediaScanCompletion('scanning', 'scanning')).toBe(false);
    expect(isMediaScanCompletion('idle', 'scheduled')).toBe(false);
  });
});

/** Mount a hook, fire the matching window event, assert delivery + teardown. */
function subscriptionHarness(use: (onFrame: (frame: never) => void) => void, name: string) {
  const seen = vi.fn();
  function Probe() {
    use(seen as (frame: never) => void);
    return null;
  }
  const view = render(<Probe />);
  window.dispatchEvent(new CustomEvent(name, { detail: { hello: 'world' } }));
  expect(seen).toHaveBeenCalledWith({ hello: 'world' });

  // A detail-less event must not call through — the handler guards on it.
  window.dispatchEvent(new CustomEvent(name));
  expect(seen).toHaveBeenCalledTimes(1);

  view.unmount();
  window.dispatchEvent(new CustomEvent(name, { detail: { hello: 'again' } }));
  expect(seen).toHaveBeenCalledTimes(1);
}

describe('the subscription hooks', () => {
  it('deliver repair status frames and detach on unmount', () => {
    subscriptionHarness(useRepairStatusEvent as never, REPAIR_STATUS_EVENT);
  });

  it('deliver repair progress frames and detach on unmount', () => {
    subscriptionHarness(useRepairProgressEvent as never, REPAIR_PROGRESS_EVENT);
  });

  it('deliver media scan frames and detach on unmount', () => {
    subscriptionHarness(useMediaScanEvent as never, MEDIA_SCAN_EVENT);
  });
});
