import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscographyRelease } from '../-artist-detail.types';

import { applyMusicBrainzDeclutter, defaultFilterState } from '../-artist-detail.filters';
import { DiscographySection } from './discography-section';

function renderSection(
  releases: DiscographyRelease[],
  filters = defaultFilterState(),
  opts: Partial<{ mb: boolean; bucket: 'albums' | 'eps' | 'singles' }> = {},
) {
  render(
    <DiscographySection
      bucket={opts.bucket ?? 'albums'}
      releases={releases}
      filters={filters}
      isMusicBrainz={opts.mb ?? false}
      isSourceArtist={false}
      onOpen={vi.fn()}
    />,
  );
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('DiscographySection', () => {
  it('renders the vanilla ids the tour and CSS anchor to', () => {
    renderSection([{ id: 1, title: 'A', owned: true }]);
    expect(document.getElementById('albums-section')).not.toBeNull();
    expect(document.getElementById('albums-grid')).not.toBeNull();
    expect(document.getElementById('albums-owned-count')).not.toBeNull();
    expect(document.getElementById('albums-missing-count')).not.toBeNull();
  });

  it('uses the vanilla headings, including the EPs capitalisation', () => {
    renderSection([{ id: 1, title: 'A' }], defaultFilterState(), { bucket: 'eps' });
    expect(document.querySelector('h3')?.textContent).toBe('EPs');
  });

  it('labels with the FILTERED counts', () => {
    renderSection([
      { id: 1, owned: true },
      { id: 2, owned: false },
      { id: 3, owned: true },
    ]);
    expect(document.getElementById('albums-owned-count')?.textContent).toBe('2 owned');
    expect(document.getElementById('albums-missing-count')?.textContent).toBe('1 missing');
  });

  it('shows 0 owned / 0 missing while ownership is still checking', () => {
    // Not "Checking..." — see sectionStatsLabels.
    renderSection([
      { id: 1, owned: null },
      { id: 2, owned: null },
    ]);
    expect(document.getElementById('albums-owned-count')?.textContent).toBe('0 owned');
    expect(document.querySelectorAll('.release-card')).toHaveLength(2);
  });

  it('disappears entirely when its category toggle is off', () => {
    const filters = defaultFilterState();
    filters.categories.albums = false;
    renderSection([{ id: 1, owned: true }], filters);
    expect(document.getElementById('albums-section')).toBeNull();
  });

  it('disappears when every card is filtered out', () => {
    const filters = { ...defaultFilterState(), ownership: 'owned' as const };
    renderSection([{ id: 1, owned: false }], filters);
    expect(document.getElementById('albums-section')).toBeNull();
  });

  it('does not RENDER hidden cards, so their artwork is never requested', () => {
    // The vanilla rendered them and set display:none. Same visible result,
    // smaller DOM, and no image fetch for a 200-release MB discography.
    const filters = { ...defaultFilterState(), ownership: 'owned' as const };
    renderSection(
      [
        { id: 1, owned: true },
        { id: 2, owned: false },
      ],
      filters,
    );
    expect(document.querySelectorAll('.release-card')).toHaveLength(1);
  });

  it('exempts owned releases from the MusicBrainz auto-declutter', () => {
    const filters = applyMusicBrainzDeclutter(defaultFilterState(), 'musicbrainz');
    renderSection(
      [
        { id: 1, title: 'Live at X', secondary_types: ['Live'], owned: true },
        { id: 2, title: 'Live at Y', secondary_types: ['Live'], owned: false },
      ],
      filters,
      { mb: true },
    );
    // The owned live album survives; the missing one is decluttered away.
    expect(document.querySelectorAll('.release-card')).toHaveLength(1);
    expect(document.querySelector('.album-card-name')?.textContent).toBe('Live at X');
  });

  it('keeps keys unique when ids repeat, so React does not mis-reconcile', () => {
    // Some sources reuse ids across buckets, and MusicBrainz can list the same
    // release-group id twice. React warns and may reuse the wrong DOM node.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderSection([
      { id: 1, title: 'A' },
      { id: 1, title: 'B' },
    ]);
    const calls = spy.mock.calls.map((c) => c.map(String).join(' '));
    const warned = calls.some((c) => /same key|unique "key"/i.test(c));
    spy.mockRestore();
    expect(warned).toBe(false);
    expect(document.querySelectorAll('.release-card')).toHaveLength(2);
  });
});
