/**
 * The standalone flag — derived from the same ss:service-status frames the
 * vanilla's two writers read, false until the first frame (the vanilla's own
 * initial value).
 */

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SERVICE_STATUS_EVENT, isStandalonePayload, useStandalone } from './-sync.use-standalone';

function status(detail: unknown): void {
  window.dispatchEvent(new CustomEvent(SERVICE_STATUS_EVENT, { detail }));
}

describe('isStandalonePayload', () => {
  it("true only for media_server.type === 'soulsync'", () => {
    expect(isStandalonePayload({ media_server: { type: 'soulsync' } })).toBe(true);
    expect(isStandalonePayload({ media_server: { type: 'plex' } })).toBe(false);
    expect(isStandalonePayload({})).toBe(false);
    expect(isStandalonePayload(null)).toBe(false);
  });
});

describe('useStandalone', () => {
  it('false before any frame, then tracks each frame', () => {
    const { result } = renderHook(() => useStandalone());
    expect(result.current).toBe(false);
    act(() => status({ media_server: { type: 'soulsync' } }));
    expect(result.current).toBe(true);
    act(() => status({ media_server: { type: 'jellyfin' } }));
    expect(result.current).toBe(false);
  });
});
