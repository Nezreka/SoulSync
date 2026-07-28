import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { applyMusicBrainzDeclutter, defaultFilterState } from '../-artist-detail.filters';
import { DiscographyFilters } from './discography-filters';

function renderBar(
  filters = defaultFilterState(),
  isSourceArtist = false,
  gapFill = false,
  { showViewToggle = false, enhanced = false } = {},
) {
  const onChange = vi.fn();
  const onToggleGapFill = vi.fn();
  const onToggleEnhanced = vi.fn();
  render(
    <DiscographyFilters
      filters={filters}
      onChange={onChange}
      isSourceArtist={isSourceArtist}
      gapFillEnabled={gapFill}
      onToggleGapFill={onToggleGapFill}
      showViewToggle={showViewToggle}
      enhanced={enhanced}
      onToggleEnhanced={onToggleEnhanced}
    />,
  );
  return { onChange, onToggleGapFill, onToggleEnhanced };
}

const btn = (filter: string, value: string) =>
  document.querySelector(`[data-filter="${filter}"][data-value="${value}"]`) as HTMLElement;

afterEach(() => {
  document.body.innerHTML = '';
});

describe('DiscographyFilters markup', () => {
  it('renders the id the tour anchors to, and the vanilla button classes', () => {
    renderBar();
    expect(document.getElementById('discography-filters')).not.toBeNull();
    expect(btn('category', 'albums').className).toBe('discography-filter-btn active');
  });

  it('marks inactive buttons without the active class', () => {
    const filters = defaultFilterState();
    filters.categories.eps = false;
    renderBar(filters);
    expect(btn('category', 'eps').className).toBe('discography-filter-btn');
  });

  it('defaults ownership to All being the active one', () => {
    renderBar();
    expect(btn('ownership', 'all').className).toContain('active');
    expect(btn('ownership', 'owned').className).not.toContain('active');
  });
});

describe('category and content are MULTI-toggles', () => {
  it('flips one category without touching the others', () => {
    const { onChange } = renderBar();
    fireEvent.click(btn('category', 'eps'));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ categories: { albums: true, eps: false, singles: true } }),
    );
  });

  it('toggles a category back on', () => {
    const filters = defaultFilterState();
    filters.categories.albums = false;
    const { onChange } = renderBar(filters);
    fireEvent.click(btn('category', 'albums'));
    expect(onChange.mock.calls[0][0].categories.albums).toBe(true);
  });

  it('flips one content type without touching the others', () => {
    const { onChange } = renderBar();
    fireEvent.click(btn('content', 'compilations'));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ content: { live: true, compilations: false, featured: true } }),
    );
  });

  it('preserves ALREADY-OFF siblings when toggling another', () => {
    // Starting from the default (everything true) cannot detect a sibling
    // reset, because resetting to true is a no-op there.
    const filters = defaultFilterState();
    filters.content.live = false;
    filters.content.featured = false;
    const { onChange } = renderBar(filters);
    fireEvent.click(btn('content', 'compilations'));
    expect(onChange.mock.calls[0][0].content).toEqual({
      live: false,
      compilations: false,
      featured: false,
    });
  });

  it('preserves already-off categories when toggling another', () => {
    const filters = defaultFilterState();
    filters.categories.singles = false;
    const { onChange } = renderBar(filters);
    fireEvent.click(btn('category', 'eps'));
    expect(onChange.mock.calls[0][0].categories).toEqual({
      albums: true,
      eps: false,
      singles: false,
    });
  });
});

describe('ownership is SINGLE-select', () => {
  it('replaces the selection rather than toggling it', () => {
    const { onChange } = renderBar();
    fireEvent.click(btn('ownership', 'owned'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ ownership: 'owned' }));
  });

  it('does not turn itself off when the active one is clicked again', () => {
    // A multi-toggle here would leave ownership undefined and hide everything.
    const filters = { ...defaultFilterState(), ownership: 'owned' as const };
    const { onChange } = renderBar(filters);
    fireEvent.click(btn('ownership', 'owned'));
    expect(onChange.mock.calls[0][0].ownership).toBe('owned');
  });
});

describe('MusicBrainz relabelling', () => {
  it('calls the Live toggle "Non-Studio" under the MB declutter', () => {
    // It governs the whole secondary-type set there, not just live albums.
    renderBar(applyMusicBrainzDeclutter(defaultFilterState(), 'musicbrainz'));
    expect(btn('content', 'live').textContent).toBe('Non-Studio');
  });

  it('calls it "Live" everywhere else', () => {
    renderBar();
    expect(btn('content', 'live').textContent).toBe('Live');
  });
});

describe('source artists', () => {
  it('hides the Status group entirely — they own nothing', () => {
    renderBar(defaultFilterState(), true);
    expect(btn('ownership', 'all')).toBeNull();
    // ...but the other groups stay.
    expect(btn('category', 'albums')).not.toBeNull();
    expect(btn('content', 'live')).not.toBeNull();
  });

  it('drops the divider along with the group, not leaving a stray one', () => {
    renderBar(defaultFilterState(), true);
    // Show | Include | Sources — the Status group and its divider are gone.
    expect(document.querySelectorAll('.filter-divider')).toHaveLength(2);
    expect(document.querySelectorAll('.filter-group')).toHaveLength(3);
  });
});

describe('the gap-fill chip', () => {
  it('sits in a Sources group after Status', () => {
    renderBar();
    const groups = [...document.querySelectorAll('.filter-group .filter-label')].map(
      (n) => n.textContent,
    );
    expect(groups).toEqual(['Show', 'Include', 'Status', 'Sources']);
  });

  it('shows for a SOURCE artist too', () => {
    // Unlike Status, this group keeps a button, so the vanilla's
    // `:has(.filter-label:only-child)` rule never hides it.
    renderBar(defaultFilterState(), true);
    expect(document.getElementById('gapfill-toggle-btn')).not.toBeNull();
  });

  it('reflects the persisted state in its active class', () => {
    renderBar(defaultFilterState(), false, true);
    expect(document.getElementById('gapfill-toggle-btn')?.className).toContain('active');
  });

  it('is inactive when gap-fill is off', () => {
    renderBar();
    expect(document.getElementById('gapfill-toggle-btn')?.className).not.toContain('active');
  });

  it('toggles on click', () => {
    const { onToggleGapFill, onChange } = renderBar();
    fireEvent.click(document.getElementById('gapfill-toggle-btn') as HTMLElement);
    expect(onToggleGapFill).toHaveBeenCalled();
    // It is a view option, not a discography filter — filter state is untouched.
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('the View toggle', () => {
  it('is absent unless the page says the artist can be enhanced', () => {
    renderBar();
    expect(document.querySelector('.enhanced-view-toggle-btn')).toBeNull();
  });

  it('marks Standard active by default', () => {
    renderBar(defaultFilterState(), false, false, { showViewToggle: true });
    const [standard, enhanced] = [...document.querySelectorAll('.enhanced-view-toggle-btn')];
    expect(standard.className).toContain('active');
    expect(enhanced.className).not.toContain('active');
  });

  it('marks Enhanced active when it is on', () => {
    renderBar(defaultFilterState(), false, false, { showViewToggle: true, enhanced: true });
    const [standard, enhanced] = [...document.querySelectorAll('.enhanced-view-toggle-btn')];
    expect(standard.className).not.toContain('active');
    expect(enhanced.className).toContain('active');
  });

  it('reports which view was picked', () => {
    const { onToggleEnhanced } = renderBar(defaultFilterState(), false, false, {
      showViewToggle: true,
    });
    fireEvent.click(document.querySelector('[data-view="enhanced"]') as HTMLElement);
    expect(onToggleEnhanced).toHaveBeenCalledWith(true);

    fireEvent.click(document.querySelector('[data-view="standard"]') as HTMLElement);
    expect(onToggleEnhanced).toHaveBeenCalledWith(false);
  });

  it('HIDES the discography filters while Enhanced is on, keeping View', () => {
    // Enhanced replaces the discography, so filters that only describe it are
    // meaningless there.
    renderBar(defaultFilterState(), false, false, { showViewToggle: true, enhanced: true });
    const groups = [...document.querySelectorAll('.filter-group')] as HTMLElement[];
    const visible = groups.filter((g) => g.style.display !== 'none');
    expect(visible).toHaveLength(1);
    expect(visible[0].querySelector('.filter-label')?.textContent).toBe('View');
  });

  it('shows them all again in Standard', () => {
    renderBar(defaultFilterState(), false, false, { showViewToggle: true });
    const groups = [...document.querySelectorAll('.filter-group')] as HTMLElement[];
    expect(groups.every((g) => g.style.display !== 'none')).toBe(true);
  });
});
