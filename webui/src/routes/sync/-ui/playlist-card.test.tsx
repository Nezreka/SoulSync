/**
 * The playlist card.
 *
 * The behaviour worth pinning is what a card DOESN'T show: a healthy playlist
 * gets no ring, no warning colour and no visible buttons. That restraint is the
 * design, and it is the first thing a future change would erode.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { PlaylistCard, playlistCardMeta, playlistCardPrimaryLabel } from './playlist-card';

function row(over: Partial<MirroredPlaylistRow> = {}): MirroredPlaylistRow {
  return { id: 1, name: 'Road Trip', source: 'spotify', track_count: 140, ...over };
}

function renderCard(over: Partial<MirroredPlaylistRow> = {}, name = 'Road Trip') {
  const onOpen = vi.fn();
  const onClick = vi.fn();
  const onMore = vi.fn();
  const r = row(over);
  const { container } = render(
    <PlaylistCard
      row={r}
      name={name}
      when="synced 3h ago"
      schedule="Every 6 hours"
      onOpen={onOpen}
      primary={{ label: playlistCardPrimaryLabel(r), onClick }}
      onMore={onMore}
    />,
  );
  return { container, onOpen, onClick, onMore };
}

describe('the ring — what a healthy card does NOT show', () => {
  it('a synced playlist gets no ring at all', () => {
    // A ring on every card is forty of them announcing nothing is wrong.
    const { container } = renderCard({ total_count: 140, discovered_count: 140 });
    expect(container.querySelector('.pl-ring')).toBeNull();
    expect(container.querySelector('.pl-card')?.getAttribute('data-state')).toBe('ok');
  });

  it('a never-touched playlist also gets no ring', () => {
    const { container } = renderCard({ total_count: 140, discovered_count: 0 });
    expect(container.querySelector('.pl-ring')).toBeNull();
  });

  it('a short playlist rings with its coverage', () => {
    const { container } = renderCard({ total_count: 86, discovered_count: 62 });
    const ring = container.querySelector('.pl-ring') as HTMLElement;
    expect(ring).not.toBeNull();
    expect(ring.getAttribute('data-pct')).toBe('72%');
    expect(ring.className).toContain('pl-ring--short');
    expect(ring.style.getPropertyValue('--pct')).toBe('72');
  });

  it('a failed run rings in the error tone', () => {
    const { container } = renderCard({ pipeline_state: { status: 'error' } });
    expect(container.querySelector('.pl-ring--error')).not.toBeNull();
  });

  it('a run in flight rings in the working tone', () => {
    const { container } = renderCard({ pipeline_state: { status: 'running' } });
    expect(container.querySelector('.pl-ring--working')).not.toBeNull();
  });

  it('describes itself to assistive tech, since the arc is visual only', () => {
    const { container } = renderCard({ total_count: 10, discovered_count: 5 });
    expect(container.querySelector('.pl-ring')?.getAttribute('aria-label')).toBe(
      '50% of tracks in library',
    );
  });
});

describe('the meta line', () => {
  it('states the count alone when everything is fine', () => {
    // "140 tracks · 140 in library" says the same thing twice.
    expect(playlistCardMeta(row({ total_count: 140, discovered_count: 140 }), 'synced 3h ago')).toBe(
      '140 tracks · synced 3h ago',
    );
  });

  it('says what is in the library only when it differs', () => {
    expect(playlistCardMeta(row({ total_count: 86, discovered_count: 62 }), 'x')).toBe(
      '86 tracks · 62 in library',
    );
  });

  it('reports a failure and a run in flight in the user’s words', () => {
    expect(playlistCardMeta(row({ pipeline_state: { status: 'error' } }), 'x')).toContain(
      'last run failed',
    );
    expect(
      playlistCardMeta(row({ pipeline_state: { status: 'running', phase: 'Discovering' } }), 'x'),
    ).toBe('140 tracks · discovering');
  });

  it('singularises, and survives a row with no timestamp', () => {
    expect(playlistCardMeta(row({ track_count: 1, total_count: 1, discovered_count: 1 }), '')).toBe(
      '1 track',
    );
  });
});

describe('the primary action', () => {
  it('offers the fix that matches the state', () => {
    expect(playlistCardPrimaryLabel(row({ total_count: 86, discovered_count: 62 }))).toBe(
      'Find 24 missing',
    );
    expect(playlistCardPrimaryLabel(row({ pipeline_state: { status: 'error' } }))).toBe('Retry');
    expect(playlistCardPrimaryLabel(row({ total_count: 10, discovered_count: 10 }))).toBe(
      'Sync now',
    );
  });

  it('never offers Cancel — the controller has no cancel to call', () => {
    // A button that cannot do what it says is worse than one offering less.
    expect(playlistCardPrimaryLabel(row({ pipeline_state: { status: 'running' } }))).toBe(
      'View progress',
    );
  });

  it('fires without also opening the card behind it', () => {
    const { container, onClick, onOpen } = renderCard({ total_count: 10, discovered_count: 4 });
    fireEvent.click(container.querySelector('.pl-card-fix') as HTMLElement);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('the overflow trigger does not open the card either', () => {
    const { container, onMore, onOpen } = renderCard();
    fireEvent.click(container.querySelector('.pl-card-more') as HTMLElement);
    expect(onMore).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe('the card body', () => {
  it('opens from a click and from the keyboard', () => {
    const { container, onOpen } = renderCard();
    fireEvent.click(container.querySelector('.pl-card') as HTMLElement);
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(container.querySelector('.pl-card') as HTMLElement, { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it('shows the source brand mark beside the name, and the schedule', () => {
    const { container } = renderCard();
    expect(container.querySelector('.pl-card-name .spotify-icon')).not.toBeNull();
    // With no onSchedule the pill is inert, and says so rather than claiming
    // "Not scheduled" — which would imply you could schedule it.
    expect(container.querySelector('.pl-card-pill--inert')?.textContent).toBe(
      'Can\u2019t be scheduled',
    );
  });

  it('a schedulable card shows its cadence on a CLICKABLE pill', () => {
    const onSchedule = vi.fn();
    const r = row();
    const { container } = render(
      <PlaylistCard
        row={r}
        name="Road Trip"
        when="synced 3h ago"
        schedule="Every 6 hours"
        onOpen={vi.fn()}
        primary={{ label: 'Sync now', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={onSchedule}
      />,
    );
    const pill = container.querySelector('.pl-card-pill') as HTMLElement;
    expect(pill.tagName).toBe('BUTTON');
    expect(pill.textContent).toBe('Every 6 hours');
  });

  it('the pill opens the picker without also opening the card', () => {
    // The hover veil used to cover the whole card, so the pill was unreachable
    // exactly when hovering revealed the controls.
    const onSchedule = vi.fn();
    const onOpen = vi.fn();
    const r = row();
    const { container } = render(
      <PlaylistCard
        row={r}
        name="Road Trip"
        when="x"
        schedule="Every 6 hours"
        onOpen={onOpen}
        primary={{ label: 'Sync now', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={onSchedule}
      />,
    );
    fireEvent.click(container.querySelector('.pl-card-pill') as HTMLElement);
    expect(onSchedule).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('renders the display name, which may be a rename', () => {
    renderCard({}, 'My own title');
    expect(screen.getByText('My own title')).toBeInTheDocument();
  });
});

describe('the pill only speaks up when it has something to say', () => {
  function pillOf(scheduled: boolean, schedule: string) {
    const { container } = render(
      <PlaylistCard
        row={row()}
        name="Road Trip"
        when="x"
        schedule={schedule}
        scheduled={scheduled}
        onOpen={vi.fn()}
        primary={{ label: 'Sync now', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={vi.fn()}
      />,
    );
    return container.querySelector('.pl-card-pill') as HTMLElement;
  }

  it('a scheduled playlist wears its cadence at rest', () => {
    expect(pillOf(true, 'Every 1 day').className).not.toContain('pl-card-pill--quiet');
  });

  it('an unscheduled one goes quiet, so the scheduled few stand out', () => {
    // 24 of 37 cards reading "Not scheduled" made the most-repeated element on
    // the page the one carrying no information.
    expect(pillOf(false, 'Not scheduled').className).toContain('pl-card-pill--quiet');
  });

  it('quiet is a STYLE, not a removal — it still opens the picker', () => {
    // Reserving the box is what stops the grid jogging under the cursor on
    // hover, and the control has to stay reachable either way.
    const onSchedule = vi.fn();
    const { container } = render(
      <PlaylistCard
        row={row()}
        name="Road Trip"
        when="x"
        schedule="Not scheduled"
        scheduled={false}
        onOpen={vi.fn()}
        primary={{ label: 'Sync now', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={onSchedule}
      />,
    );
    const pill = container.querySelector('.pl-card-pill') as HTMLElement;
    expect(pill.tagName).toBe('BUTTON');
    expect(pill.textContent).toBe('Not scheduled');
    fireEvent.click(pill);
    expect(onSchedule).toHaveBeenCalledTimes(1);
  });

  it('"Can’t be scheduled" is quiet too — it is rare, and it is not news', () => {
    const { container } = renderCard();
    expect((container.querySelector('.pl-card-pill--inert') as HTMLElement).className).toContain(
      'pl-card-pill--quiet',
    );
  });
});

describe('when there is nothing honest to offer', () => {
  it('renders no primary button at all', () => {
    // A Beatport or file-backed mirror cannot be refreshed by the pipeline —
    // the endpoint rejects it outright — and a button that fails on click is
    // worse than no button.
    const onMore = vi.fn();
    const { container } = render(
      <PlaylistCard
        row={row({ source: 'beatport' })}
        name="Top 100"
        when="Mirrored 1h ago"
        schedule="Not scheduled"
        onOpen={vi.fn()}
        primary={null}
        onMore={onMore}
      />,
    );
    expect(container.querySelector('.pl-card-fix')).toBeNull();
    // The overflow still works, so rename/export/delete stay reachable.
    expect(container.querySelector('.pl-card-more')).not.toBeNull();
  });
});

/**
 * These live in the vanilla stylesheet, so jsdom cannot hit-test them — it has
 * no layout engine, and every button here "works" in a rendered test whether or
 * not something is sitting on top of it in a real browser. The stacking rules
 * get asserted as TEXT instead, because both of these have now broken once.
 */
describe('the hover veil, as the stylesheet actually declares it', () => {
  // Comments stripped first: they discuss the very properties being asserted,
  // and a prose mention of z-index is not a declaration of one.
  const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8').replace(
    /\/\*[\s\S]*?\*\//g,
    '',
  );
  const block = (selector: string) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const found = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(css);
    expect(found, `${selector} is not in static/style.css`).not.toBeNull();
    return found?.[1] ?? '';
  };

  it('the veil takes NO pointer events, so the pill under it stays clickable', () => {
    // The original bug: an inset:0 layer that swallowed every click meant for
    // the schedule pill, exactly when hovering revealed the controls.
    expect(block('.pl-card-hover')).toMatch(/pointer-events:\s*none/);
  });

  it('its children DO, or Sync now and the overflow menu are inert', () => {
    expect(block('.pl-card-hover > *')).toMatch(/pointer-events:\s*auto/);
  });

  it('reveals the veil on TOUCH, where :hover never fires', () => {
    // Without this, Sync now and the overflow menu sit at opacity 0 on every
    // phone and tablet while staying perfectly clickable — controls you can
    // only hit by guessing where they are.
    const touch = /@media\s*\(hover:\s*none\)\s*\{([\s\S]*?)\n\}/g;
    const blocks = [...css.matchAll(touch)].map((m) => m[1]);
    const veil = blocks.find((b) => b.includes('.pl-card-hover'));
    expect(veil, 'no (hover: none) rule reveals .pl-card-hover').toBeTruthy();
    expect(veil).toMatch(/opacity:\s*1/);
  });

  it('reveals the quiet schedule pill on touch too', () => {
    const touch = /@media\s*\(hover:\s*none\)\s*\{([\s\S]*?)\n\}/g;
    const blocks = [...css.matchAll(touch)].map((m) => m[1]);
    expect(blocks.some((b) => b.includes('.pl-card-pill--quiet'))).toBe(true);
  });

  it('the card body is NOT lifted above the veil', () => {
    // The second bug, caused by fixing the first: z-index on the body puts its
    // box over Sync now and the overflow menu, which stretch to the same right
    // edge, so their clicks landed on the card and opened the detail modal.
    // pointer-events is what protects the pill; a z-index here only breaks the
    // buttons.
    expect(block('.pl-card-body')).not.toMatch(/z-index/);
  });
});
