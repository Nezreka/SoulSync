import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchControllerState } from '../-search.use-controller';

import { emptySourceResults } from '../-search.helpers';
import { SourceRow } from './source-row';

function stateOf(over: Partial<SearchControllerState> = {}): SearchControllerState {
  return {
    query: 'aphex',
    activeSource: 'spotify',
    sources: {},
    fallbacks: {},
    loadingSources: new Set(),
    configuredSources: {},
    enabledExperimental: new Set(),
    ...over,
  };
}

function renderRow(
  over: Partial<SearchControllerState> = {},
  handlers: { onSelect?: () => void; onOpenSettings?: () => void } = {},
) {
  const onSelect = handlers.onSelect ?? vi.fn();
  const onOpenSettings = handlers.onOpenSettings ?? vi.fn();
  render(<SourceRow state={stateOf(over)} onSelect={onSelect} onOpenSettings={onOpenSettings} />);
  return { onSelect, onOpenSettings };
}

const icon = (source: string) =>
  document.querySelector(`[data-source="${source}"]`) as HTMLButtonElement;

afterEach(cleanup);

describe('SourceRow', () => {
  it('hides experimental sources until they are enabled', () => {
    renderRow();
    expect(icon('jiosaavn')).toBeNull();
    expect(icon('bandcamp')).toBeNull();
    // A non-experimental source is always in the row.
    expect(icon('deezer')).not.toBeNull();

    cleanup();
    renderRow({ enabledExperimental: new Set(['bandcamp']) });
    expect(icon('bandcamp')).not.toBeNull();
    // Enabling one does not reveal the other.
    expect(icon('jiosaavn')).toBeNull();
  });

  it('marks the active source, and only it', () => {
    renderRow({ activeSource: 'deezer' });
    expect(icon('deezer').className).toContain('active');
    expect(icon('deezer').getAttribute('aria-selected')).toBe('true');
    expect(icon('spotify').className).not.toContain('active');
    expect(icon('spotify').getAttribute('aria-selected')).toBe('false');
  });

  it('keeps the tablist semantics the vanilla had', () => {
    // The vanilla row was role=tablist with role=tab children; losing that turns
    // a keyboard-navigable picker into a pile of unlabelled buttons.
    renderRow();
    expect(document.getElementById('enh-source-row')?.getAttribute('role')).toBe('tablist');
    expect(icon('spotify').getAttribute('role')).toBe('tab');
  });

  it('marks a cached source, a loading one, and a fallen-back one', () => {
    renderRow({
      sources: { deezer: emptySourceResults() },
      loadingSources: new Set(['itunes']),
      fallbacks: { discogs: 'deezer' },
    });
    expect(icon('deezer').className).toContain('cached');
    expect(icon('itunes').className).toContain('loading');
    expect(icon('discogs').className).toContain('fallback-warning');
    // Untouched sources wear none of the three.
    expect(icon('musicbrainz').className).toBe('enh-source-icon');
  });

  it('names both sources in a fallback tooltip', () => {
    renderRow({ fallbacks: { discogs: 'deezer' } });
    expect(icon('discogs').getAttribute('title')).toBe('Discogs unavailable — showing Deezer');
  });

  it('selects a configured source', () => {
    const { onSelect, onOpenSettings } = renderRow();
    fireEvent.click(icon('deezer'));
    expect(onSelect).toHaveBeenCalledWith('deezer');
    expect(onOpenSettings).not.toHaveBeenCalled();
  });

  it('sends an unconfigured source to Settings instead of making it active', () => {
    // The important state: activating it would show an empty result set and
    // leave the user blaming the provider.
    const { onSelect, onOpenSettings } = renderRow({ configuredSources: { deezer: false } });
    const button = icon('deezer');
    expect(button.className).toContain('unconfigured');
    expect(button.getAttribute('title')).toContain('not configured');

    fireEvent.click(button);
    expect(onOpenSettings).toHaveBeenCalledWith('deezer');
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('treats an unknown source as configured rather than dimming it', () => {
    // configuredSources is filled in asynchronously; a missing key means "not
    // answered yet", and dimming on that flashes the whole row on every load.
    renderRow({ configuredSources: {} });
    expect(icon('deezer').className).not.toContain('unconfigured');
  });

  it('stops the click from reaching the document', () => {
    // The page closes its dropdown on any document click; without this the row
    // would dismiss the very results it just asked for.
    const onDocumentClick = vi.fn();
    document.addEventListener('click', onDocumentClick);
    try {
      renderRow();
      fireEvent.click(icon('deezer'));
      expect(onDocumentClick).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('click', onDocumentClick);
    }
  });

  it('renders a brand logo where there is one and a glyph where there is not', () => {
    renderRow();
    expect(icon('spotify').querySelector('img')?.getAttribute('src')).toBe(
      '/static/img/brands/spotify.png',
    );
    // Amazon has no logo file; the emoji is the fallback, not an empty span.
    expect(icon('amazon').querySelector('img')).toBeNull();
    expect(icon('amazon').querySelector('.enh-source-icon-glyph')?.textContent).toBe('🛒');
  });

  it('calls Soulseek "Basic Search", as the UI always has', () => {
    renderRow();
    expect(icon('soulseek').querySelector('.enh-source-icon-label')?.textContent).toBe(
      'Basic Search',
    );
  });
});
