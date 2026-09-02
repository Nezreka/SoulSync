import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { MirroredMatch } from '../-sync.server';

import {
  distinctSources,
  hasIndistinguishable,
  ServerDisambigModal,
  shortRef,
} from './server-playlist-list';

/**
 * #1219 — "Server Playlists shows multiple versions of the same playlist".
 *
 * The reporter picked a server playlist and got "was found on 4 sources", where
 * all four were TIDAL. They were four DIFFERENT TIDAL playlists that happen to
 * share a name — TIDAL allows that, the mirror table's UNIQUE(source,
 * source_playlist_id, profile_id) means repeated syncs cannot duplicate a row,
 * and the ids are TIDAL's own stable UUIDs.
 *
 * So nothing needed cleaning out; the modal was wrong twice over. It said
 * "sources" when it meant playlists, and every card showed the same name, badge
 * and track count — nothing to choose on. Renaming a mirror (Mirrored tab →
 * card menu → Rename) already existed and could not help here, because this
 * modal rendered the raw upstream `name` and ignored the alias.
 */

const base: MirroredMatch = {
  id: 1,
  name: 'Road Trip',
  source: 'tidal',
  track_count: 40,
  mirrored_at: new Date().toISOString(),
};

const four: MirroredMatch[] = [
  { ...base, id: 1, source_ref: 'tidal:playlist:aaaaaaaa-1111' },
  { ...base, id: 2, source_ref: 'tidal:playlist:bbbbbbbb-2222' },
  { ...base, id: 3, source_ref: 'tidal:playlist:cccccccc-3333' },
  { ...base, id: 4, source_ref: 'tidal:playlist:dddddddd-4444' },
];

function show(candidates: MirroredMatch[], onPick = vi.fn()) {
  render(
    <ServerDisambigModal
      playlistName="Road Trip"
      candidates={candidates}
      onPick={onPick}
      onClose={vi.fn()}
    />,
  );
  return onPick;
}

// ── the pure helpers ─────────────────────────────────────────────────────────

describe('counting what is actually there', () => {
  it('counts one source when every candidate is TIDAL', () => {
    expect(distinctSources(four)).toBe(1);
  });

  it('counts them separately when the sources really do differ', () => {
    expect(distinctSources([{ source: 'tidal' }, { source: 'spotify' }])).toBe(2);
  });

  it('spots candidates a person cannot tell apart', () => {
    expect(hasIndistinguishable(four)).toBe(true);
  });

  it('does not flag candidates that differ by name or source', () => {
    expect(
      hasIndistinguishable([
        { name: 'A', source: 'tidal' },
        { name: 'B', source: 'tidal' },
      ]),
    ).toBe(false);
    expect(
      hasIndistinguishable([
        { name: 'A', source: 'tidal' },
        { name: 'A', source: 'spotify' },
      ]),
    ).toBe(false);
  });

  it('treats a renamed mirror as distinct — that is the point of renaming', () => {
    expect(
      hasIndistinguishable([
        { name: 'Road Trip', display_name: 'Road Trip (car)', source: 'tidal' },
        { name: 'Road Trip', source: 'tidal' },
      ]),
    ).toBe(false);
  });

  it('shortens a source reference to its tail', () => {
    expect(shortRef({ source_ref: 'tidal:playlist:aaaaaaaa-1111' })).toContain('1111');
  });

  it('falls back to the row id when there is no reference to show', () => {
    expect(shortRef({ id: 7 })).toBe('#7');
  });
});

// ── what the user sees ───────────────────────────────────────────────────────

describe('the modal', () => {
  it('says "playlists", not "sources", when they are all one source', () => {
    show(four);
    expect(screen.getByText(/matches 4 mirrored playlists/)).toBeTruthy();
    expect(screen.queryByText(/4 sources/)).toBeNull();
  });

  it('still says "sources" when they genuinely differ', () => {
    show([
      { ...base, id: 1 },
      { ...base, id: 2, source: 'spotify' },
    ]);
    expect(screen.getByText(/was found on 2 sources/)).toBeTruthy();
  });

  it('shows the alias so a rename actually helps here', () => {
    show([
      { ...base, id: 1, display_name: 'Road Trip (car)' },
      { ...base, id: 2 },
    ]);
    expect(screen.getByText('Road Trip (car)')).toBeTruthy();
  });

  it('shows something identifying when the cards are otherwise identical', () => {
    const { container } = render(
      <ServerDisambigModal
        playlistName="Road Trip"
        candidates={four}
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const refs = [...container.querySelectorAll('.server-disambig-ref')].map((n) => n.textContent);
    expect(refs).toHaveLength(4);
    expect(new Set(refs).size).toBe(4); // four cards, four different labels
  });

  it('points at where renaming lives, since that is the real fix', () => {
    show(four);
    expect(screen.getByText(/rename a mirror from the Mirrored tab/i)).toBeTruthy();
  });

  it('stays quiet when the candidates are already distinguishable', () => {
    const { container } = render(
      <ServerDisambigModal
        playlistName="Road Trip"
        candidates={[
          { ...base, id: 1 },
          { ...base, id: 2, source: 'spotify' },
        ]}
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(container.querySelector('.server-disambig-hint')).toBeNull();
    expect(container.querySelector('.server-disambig-ref')).toBeNull();
  });

  it('still picks the candidate that was clicked', () => {
    const onPick = show(four);
    fireEvent.click(screen.getAllByText('Road Trip')[0]);
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });
});
