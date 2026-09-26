import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExpandedAlbumHeader } from './expanded-album-header';

/**
 * a finished reassign used to call the header's onDelete, which popped the
 * "Delete Album" confirmation over a move that had already happened. the
 * modal is stubbed to apply the moment it mounts so the wiring is what's
 * under test, not the three-step picker.
 */
vi.mock('./reassign-modal', () => ({
  ReassignModal: ({ onApplied }: { onApplied?: () => void }) => {
    onApplied?.();
    return <div data-testid="reassign-stub" />;
  },
}));

afterEach(() => cleanup());

describe('reassign apply', () => {
  it('refetches through onReassigned and never opens the delete dialog', () => {
    const onReassigned = vi.fn();
    const onAlbumDeleted = vi.fn();
    render(
      <ExpandedAlbumHeader
        album={{ id: 7, title: 'SAW 85-92', tracks: [] }}
        rows={[]}
        artistId={42}
        artistName="Aphex Twin"
        isAdmin
        onArtApplied={vi.fn()}
        onAlbumDeleted={onAlbumDeleted}
        onAlbumPatched={vi.fn()}
        onReassigned={onReassigned}
      />,
    );
    fireEvent.click(document.querySelector('.lib-more') as HTMLElement);
    fireEvent.click(document.querySelector('.enhanced-reassign-album-btn') as HTMLElement);
    expect(screen.getByTestId('reassign-stub')).toBeTruthy();
    expect(onReassigned).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Delete Album', { selector: 'h3' })).toBeNull();
    expect(onAlbumDeleted).not.toHaveBeenCalled();
  });

  it('falls back to dropping the album when no reload is wired', () => {
    const onAlbumDeleted = vi.fn();
    render(
      <ExpandedAlbumHeader
        album={{ id: 7, title: 'SAW 85-92', tracks: [] }}
        rows={[]}
        artistId={42}
        artistName="Aphex Twin"
        isAdmin
        onArtApplied={vi.fn()}
        onAlbumDeleted={onAlbumDeleted}
        onAlbumPatched={vi.fn()}
      />,
    );
    fireEvent.click(document.querySelector('.lib-more') as HTMLElement);
    fireEvent.click(document.querySelector('.enhanced-reassign-album-btn') as HTMLElement);
    expect(onAlbumDeleted).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Delete Album', { selector: 'h3' })).toBeNull();
  });
});
