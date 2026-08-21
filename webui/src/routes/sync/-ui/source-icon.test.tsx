/**
 * Real brand marks instead of emoji.
 *
 * All twelve sprites already existed in style.css and only the tab strip used
 * them, so every card and pill fell back to an emoji that said nothing about
 * the service. These tests pin the mapping — including the ids that differ
 * between the vertical registry and the tab table, which is where a wrong
 * mark would come from.
 */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SourceIcon, sourceIconClass } from './source-icon';

describe('sourceIconClass', () => {
  it('maps every source that has a mark', () => {
    expect(sourceIconClass('spotify')).toBe('spotify-icon');
    expect(sourceIconClass('tidal')).toBe('tidal-icon');
    expect(sourceIconClass('qobuz')).toBe('qobuz-icon');
    expect(sourceIconClass('deezer')).toBe('deezer-icon');
    expect(sourceIconClass('youtube')).toBe('youtube-icon');
    expect(sourceIconClass('beatport')).toBe('beatport-icon');
  });

  it('accepts BOTH id spellings a source goes by', () => {
    // The vertical registry says `spotify_public` and `itunes_link`; the tab
    // table says `spotify-public` and `itunes-link`. A card gets handed
    // whichever its caller happens to hold.
    expect(sourceIconClass('spotify_public')).toBe('spotify-icon');
    expect(sourceIconClass('spotify-public')).toBe('spotify-icon');
    expect(sourceIconClass('itunes_link')).toBe('itunes-icon');
    expect(sourceIconClass('itunes-link')).toBe('itunes-icon');
    expect(sourceIconClass('deezer-link')).toBe('deezer-icon');
    expect(sourceIconClass('lastfm-sync')).toBe('lastfm-icon');
  });

  it('is null for an unknown or missing source rather than guessing', () => {
    for (const s of [undefined, null, '', 'nonsense']) {
      expect(sourceIconClass(s)).toBeNull();
    }
  });
});

describe('SourceIcon', () => {
  it('renders the same sprite pair the tab strip uses', () => {
    const { container } = render(<SourceIcon source="tidal" />);
    const span = container.querySelector('span') as HTMLElement;
    expect(span.className).toBe('tab-icon tidal-icon');
  });

  it('falls back to a glyph when the source has no mark', () => {
    const { container } = render(<SourceIcon source="nonsense" />);
    expect(container.textContent).toBe('📋');
    expect(container.querySelector('.tab-icon')).toBeNull();
  });

  it('takes a caller-supplied fallback', () => {
    const { container } = render(<SourceIcon source={null} fallback="✨" />);
    expect(container.textContent).toBe('✨');
  });

  it('is decorative — hidden from assistive tech, since the name is beside it', () => {
    const { container } = render(<SourceIcon source="spotify" />);
    expect(container.querySelector('span')?.getAttribute('aria-hidden')).toBe('true');
  });
});
