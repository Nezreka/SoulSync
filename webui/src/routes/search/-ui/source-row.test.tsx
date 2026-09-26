import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchControllerState } from '../-search.use-controller';

import { catalogSources, ModeTabs, modeOf, SourcePicker } from './source-row';

function stateOf(over: Partial<SearchControllerState> = {}): SearchControllerState {
  return {
    query: 'aphex',
    activeSource: 'spotify',
    sources: {},
    fallbacks: {},
    loadingSources: new Set(),
    configuredSources: {},
    enabledExperimental: new Set(),
    userPickedSource: false,
    ...over,
  };
}

function renderTabs(over: Partial<SearchControllerState> = {}, catalogSource = 'spotify') {
  const onSelect = vi.fn();
  render(<ModeTabs state={stateOf(over)} catalogSource={catalogSource} onSelect={onSelect} />);
  return { onSelect };
}

function renderPicker(over: Partial<SearchControllerState> = {}) {
  const onSelect = vi.fn();
  const onOpenSettings = vi.fn();
  render(
    <SourcePicker state={stateOf(over)} onSelect={onSelect} onOpenSettings={onOpenSettings} />,
  );
  return { onSelect, onOpenSettings };
}

const tab = (source: string) =>
  document.querySelector(`#enh-source-row [data-source="${source}"]`) as HTMLButtonElement;
const item = (source: string) =>
  document.querySelector(`[role="menuitemradio"][data-source="${source}"]`) as HTMLButtonElement;
const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /Search with/ }));

afterEach(cleanup);

describe('modeOf and catalogSources', () => {
  it('splits videos and files off from the metadata sources', () => {
    expect(modeOf('youtube_videos')).toBe('videos');
    expect(modeOf('soulseek')).toBe('files');
    expect(modeOf('deezer')).toBe('catalog');
    const catalog = catalogSources(stateOf());
    expect(catalog).toContain('spotify');
    expect(catalog).not.toContain('soulseek');
    expect(catalog).not.toContain('youtube_videos');
  });

  it('hides experimental sources until they are enabled', () => {
    expect(catalogSources(stateOf())).not.toContain('bandcamp');
    const enabled = catalogSources(stateOf({ enabledExperimental: new Set(['bandcamp']) }));
    expect(enabled).toContain('bandcamp');
    // Enabling one does not reveal the other.
    expect(enabled).not.toContain('jiosaavn');
  });
});

describe('ModeTabs', () => {
  it('keeps the hooks the global widget clicks', () => {
    // downloads.js and api-monitor.js click #enh-source-row [data-source="soulseek"]
    // to hand a query to basic search. that selector is the contract.
    renderTabs();
    expect(document.getElementById('enh-source-row')?.getAttribute('role')).toBe('tablist');
    expect(tab('soulseek')).not.toBeNull();
    expect(tab('youtube_videos')).not.toBeNull();
    expect(tab('soulseek').getAttribute('role')).toBe('tab');
  });

  it('marks the mode of the active source', () => {
    renderTabs({ activeSource: 'deezer' }, 'deezer');
    expect(screen.getByRole('tab', { name: 'Catalog' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Files' })).toHaveAttribute('aria-selected', 'false');

    cleanup();
    renderTabs({ activeSource: 'soulseek' });
    expect(screen.getByRole('tab', { name: 'Files' })).toHaveAttribute('aria-selected', 'true');
  });

  it('sends Catalog back to the source the user was on', () => {
    const { onSelect } = renderTabs({ activeSource: 'soulseek' }, 'deezer');
    fireEvent.click(screen.getByRole('tab', { name: 'Catalog' }));
    expect(onSelect).toHaveBeenCalledWith('deezer');
    fireEvent.click(screen.getByRole('tab', { name: 'Videos' }));
    expect(onSelect).toHaveBeenCalledWith('youtube_videos');
  });

  it('stops the click from reaching the document', () => {
    // the page re-renders on select and detaches the node; a document listener
    // would then see a click from nowhere
    const onDocumentClick = vi.fn();
    document.addEventListener('click', onDocumentClick);
    try {
      renderTabs();
      fireEvent.click(tab('soulseek'));
      expect(onDocumentClick).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('click', onDocumentClick);
    }
  });
});

describe('SourcePicker', () => {
  it('names the active source and opens a menu of catalog sources', () => {
    renderPicker({ activeSource: 'deezer' });
    const pill = screen.getByRole('button', { name: 'Search with Deezer' });
    expect(pill).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).toBeNull();

    openMenu();
    expect(pill).toHaveAttribute('aria-expanded', 'true');
    expect(item('deezer')).toHaveAttribute('aria-checked', 'true');
    expect(item('spotify')).toHaveAttribute('aria-checked', 'false');
    // not metadata sources: they are tabs
    expect(item('soulseek')).toBeNull();
    expect(item('youtube_videos')).toBeNull();
  });

  it('selects a configured source and closes', () => {
    const { onSelect, onOpenSettings } = renderPicker();
    openMenu();
    fireEvent.click(item('deezer'));
    expect(onSelect).toHaveBeenCalledWith('deezer');
    expect(onOpenSettings).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('sends an unconfigured source to Settings instead of making it active', () => {
    // activating it would show an empty result set and leave the user blaming
    // the provider
    const { onSelect, onOpenSettings } = renderPicker({ configuredSources: { deezer: false } });
    openMenu();
    expect(item('deezer')).toHaveAttribute('data-unconfigured');
    expect(item('deezer').textContent).toContain('Not set up');
    fireEvent.click(item('deezer'));
    expect(onOpenSettings).toHaveBeenCalledWith('deezer');
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('treats an unknown source as configured rather than dimming it', () => {
    // configuredSources lands asynchronously; a missing key means "not answered yet"
    renderPicker({ configuredSources: {} });
    openMenu();
    expect(item('deezer')).not.toHaveAttribute('data-unconfigured');
  });

  it('says when a source was served by another, and which one is searching', () => {
    renderPicker({ fallbacks: { discogs: 'deezer' }, loadingSources: new Set(['itunes']) });
    openMenu();
    expect(item('discogs').textContent).toContain('Showing Deezer');
    expect(item('itunes').textContent).toContain('Searching…');
  });

  it('closes on Escape and on a click outside', () => {
    renderPicker();
    openMenu();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();

    openMenu();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('is a plain YouTube label in videos mode, with no menu', () => {
    renderPicker({ activeSource: 'youtube_videos' });
    expect(screen.queryByRole('button', { name: /Search with/ })).toBeNull();
    expect(screen.getByText('YouTube')).toBeInTheDocument();
  });

  it('renders a brand logo where there is one and a glyph where there is not', () => {
    renderPicker();
    openMenu();
    expect(item('spotify').querySelector('img')?.getAttribute('src')).toBe(
      '/static/img/brands/spotify.png',
    );
    // Amazon has no logo file; the emoji is the fallback, not an empty span.
    expect(item('amazon').querySelector('img')).toBeNull();
    expect(item('amazon').textContent).toContain('🛒');
  });
});
