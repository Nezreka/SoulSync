import { describe, expect, it } from 'vitest';

import {
  SECTION_CONFIG,
  SECTION_DEFAULTS,
  YOUR_ALBUMS_POLL_MAX_ATTEMPTS,
  YOUR_ALBUMS_POLL_MS,
  resolveSection,
} from './-discover.section-state';

const items = (d: unknown) => ((d as { albums?: unknown[] })?.albums ?? []) as unknown[];
const ok = <T>(data: T) => ({ kind: 'ok' as const, data });
const err = () => ({ kind: 'error' as const, error: new Error('boom') });

describe('the five phases, in the controller’s order', () => {
  it('is loading while the query is pending', () => {
    const r = resolveSection('recent-releases', undefined, items, true);
    expect(r.phase).toBe('loading');
    expect(r.message).toBe('Loading recent releases...');
  });

  it('is error when the request THREW', () => {
    // Distinct from `success: false`. This is HTTP not-ok / network / bad JSON.
    const r = resolveSection('recent-releases', err(), items, false);
    expect(r.phase).toBe('error');
    expect(r.message).toBe('Failed to load recent releases');
  });

  it('is EMPTY — not error — when the payload says success:false', () => {
    // The controller calls _showEmpty() on a failed success gate. Getting this
    // wrong shows "Failed to load" where the vanilla shows the empty copy.
    const r = resolveSection('recent-releases', ok({ success: false, albums: [] }), items, false);
    expect(r.phase).toBe('empty');
    expect(r.message).toBe('No recent releases found');
  });

  it('is rendered when there are rows', () => {
    const r = resolveSection(
      'recent-releases',
      ok({ success: true, albums: [{ n: 1 }] }),
      items,
      false,
    );
    expect(r.phase).toBe('rendered');
    expect(r.items).toHaveLength(1);
  });

  it('is empty when there are none', () => {
    const r = resolveSection('recent-releases', ok({ success: true, albums: [] }), items, false);
    expect(r.phase).toBe('empty');
    expect(r.hidden).toBe(false); //  recent-releases stays and explains itself
  });

  it('treats an absent success key as success', () => {
    const r = resolveSection('recent-releases', ok({ albums: [{ n: 1 }] }), items, false);
    expect(r.phase).toBe('rendered');
  });
});

describe('error toasts follow the vanilla, section by section', () => {
  it('toasts for sections that asked for it', () => {
    for (const id of ['recent-releases', 'your-albums-section', 'your-artists-section'] as const) {
      expect(resolveSection(id, err(), items, false).shouldToast).toBe(true);
    }
  });

  it('stays SILENT for sections that deliberately did not', () => {
    // The vanilla's reasoning: a section with no recovery action should not
    // shout at the user.
    for (const id of ['recommended-artists-section', 'listening-recs-section'] as const) {
      const r = resolveSection(id, err(), items, false);
      expect(r.phase).toBe('error');
      expect(r.shouldToast).toBe(false);
    }
  });
});

describe('hideWhenEmpty per section', () => {
  it('hides the four that opted in', () => {
    for (const id of [
      'recommended-artists-section',
      'listening-recs-section',
      'your-albums-section',
      'your-artists-section',
    ] as const) {
      const r = resolveSection(id, ok({ success: true, albums: [] }), items, false);
      expect(r.phase).toBe('empty');
      expect(r.hidden).toBe(true);
    }
  });

  it('keeps the others visible with their message', () => {
    const r = resolveSection(
      'seasonal-albums-section',
      ok({ success: true, albums: [] }),
      items,
      false,
    );
    expect(r.hidden).toBe(false);
    expect(r.message).toBe('No seasonal albums found');
  });
});

describe('the your-albums stale path', () => {
  const withStats = (total: number, stale: boolean) => ({
    success: true,
    albums: [],
    stale,
    stats: { total, owned: 0, missing: total },
  });

  it('is STALE when the cache is rebuilding and nothing has landed yet', () => {
    const r = resolveSection('your-albums-section', ok(withStats(0, true)), items, false);
    expect(r.phase).toBe('stale');
    expect(r.message).toBe('Fetching your albums from connected services...');
    expect(r.shouldPoll).toBe(true);
  });

  it('stale WINS over empty when both apply', () => {
    // Both predicates see total === 0; the controller checks isStale first.
    const data = withStats(0, true);
    expect(SECTION_CONFIG['your-albums-section']?.isEmpty?.([], data)).toBe(false);
    expect(SECTION_CONFIG['your-albums-section']?.isStale?.([], data)).toBe(true);
    expect(resolveSection('your-albums-section', ok(data), items, false).phase).toBe('stale');
  });

  it('is EMPTY when there is genuinely nothing and no rebuild running', () => {
    const r = resolveSection('your-albums-section', ok(withStats(0, false)), items, false);
    expect(r.phase).toBe('empty');
    expect(r.hidden).toBe(true);
    expect(r.shouldPoll).toBe(false);
  });

  it('is NOT empty when the library has albums, even with none on this page', () => {
    // The filter can legitimately match nothing while stats.total is large —
    // hiding the section then would be wrong.
    const r = resolveSection('your-albums-section', ok(withStats(120, false)), items, false);
    expect(r.phase).toBe('rendered');
  });

  it('stops being stale once albums land', () => {
    const r = resolveSection('your-albums-section', ok(withStats(5, true)), items, false);
    expect(r.phase).not.toBe('stale');
    expect(r.shouldPoll).toBe(false);
  });

  it('pins the poller shape', () => {
    // 5s, at most 12 attempts — it gives up after a minute rather than
    // hammering forever.
    expect(YOUR_ALBUMS_POLL_MS).toBe(5000);
    expect(YOUR_ALBUMS_POLL_MAX_ATTEMPTS).toBe(12);
  });
});

describe('defaults', () => {
  it('falls back to the controller’s own copy for unconfigured sections', () => {
    const r = resolveSection('cache-deep-cuts', ok({ success: true, albums: [] }), items, false);
    expect(r.message).toBe(SECTION_DEFAULTS.emptyMessage);
  });

  it('keeps the vanilla default strings verbatim', () => {
    expect(SECTION_DEFAULTS).toEqual({
      loadingMessage: 'Loading...',
      emptyMessage: 'Nothing to show',
      errorMessage: 'Failed to load',
      staleMessage: 'Updating...',
    });
  });

  it('does not resurrect the sections Discover 2.0 orphaned', () => {
    expect(SECTION_CONFIG).not.toHaveProperty('decade-browser');
    expect(SECTION_CONFIG).not.toHaveProperty('genre-browser');
  });
});
