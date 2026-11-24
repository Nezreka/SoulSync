/**
 * the lineup saves half a second after a change. closing the editor inside
 * that half second (or pressing change / remove, which close it) cleared
 * the timer and the change was gone.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const saveLineup = vi.fn(async () => ({}));
vi.mock('../-player.api', () => ({ guideEdits: { saveLineup } }));

const channel = (id: string, name: string) => ({
  id,
  name,
  ownName: name,
  kind: 'genre',
  blurb: '',
  pool: [],
});

describe('the lineup editor', () => {
  it('a change made just before closing is still saved', async () => {
    const { LineupEditor } = await import('./lineup-editor');
    const { unmount } = render(
      <LineupEditor
        channels={[channel('a', 'Action'), channel('b', 'Comedy')] as never}
        made={[]}
        canEditServer={false}
        onChanged={vi.fn()}
        onEdit={vi.fn()}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Move Comedy up' }));
    expect(saveLineup).not.toHaveBeenCalled();
    unmount();
    expect(saveLineup).toHaveBeenCalledTimes(1);
    expect((saveLineup.mock.calls[0] as unknown[])[0]).toEqual([
      { channel_id: 'b', hidden: false, name: null },
      { channel_id: 'a', hidden: false, name: null },
    ]);
  });
});
