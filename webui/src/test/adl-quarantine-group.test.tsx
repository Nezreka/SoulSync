/**
 * #1208: clearing a whole group of quarantined candidates.
 *
 * A track that failed verification against ninety-nine sources folds into ONE
 * row with "98 more" behind it, and the row's Delete only removes the candidate
 * it sits on - the count ticks down one confirm at a time. The group needs its
 * own verb, and it must not quietly widen the per-row one.
 */

import { fireEvent, render } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AdlQuarantineEntry } from '@/routes/active-downloads/-adl.types';
import type { QuarantineGroup } from '@/routes/active-downloads/-adl.use-verification';

import {
  quarantineDeleteEntry,
  quarantineDeleteGroup,
} from '@/routes/active-downloads/-adl.verif-actions';
import { AdlQuarantineList } from '@/routes/active-downloads/-ui/adl-review';
import { server } from '@/test/msw';

let toasts: string[] = [];
let confirms: Record<string, unknown>[] = [];
let confirmAnswer = true;

beforeEach(() => {
  toasts = [];
  confirms = [];
  confirmAnswer = true;
  window.showToast = vi.fn((message: string) => {
    toasts.push(message);
  });
  window.showConfirmDialog = vi.fn((options?: Record<string, unknown>) => {
    confirms.push(options ?? {});
    return Promise.resolve(confirmAnswer);
  });
});

const entry = (id: string): AdlQuarantineEntry =>
  ({
    id,
    expected_track: 'Wild Thing',
    expected_artist: 'The Troggs',
    original_filename: '04 - Wild Thing.flac',
    group_key: 'the troggs|wild thing',
    has_full_context: true,
  }) as AdlQuarantineEntry;

const NO_HANDLERS = {
  onPlay: vi.fn(),
  onCompare: vi.fn(),
  onAudit: vi.fn(),
  onApprove: vi.fn(),
  onDelete: vi.fn(),
};

function renderList(
  groups: QuarantineGroup[],
  onDeleteGroup?: (entry: AdlQuarantineEntry, count: number) => void,
) {
  return render(
    <AdlQuarantineList
      groups={groups}
      openDetails={new Set()}
      openGroups={new Set()}
      onToggleDetails={vi.fn()}
      onToggleGroup={vi.fn()}
      handlersFor={() => NO_HANDLERS}
      onDeleteGroup={onDeleteGroup}
    />,
  );
}

/** Captures the URL of every request, so the query string can be asserted. */
function captureDelete(body: Record<string, unknown> = { success: true, deleted: 3 }) {
  const urls: string[] = [];
  server.use(
    http.delete('/api/quarantine/:id', ({ request }) => {
      urls.push(request.url);
      return HttpResponse.json(body);
    }),
  );
  return urls;
}

describe('the quarantine group row', () => {
  it('offers one delete for the whole group, counting every candidate', () => {
    const onDeleteGroup = vi.fn();
    const { container } = renderList(
      [{ key: 'g', members: [entry('q1'), entry('q2'), entry('q3')] }],
      onDeleteGroup,
    );
    const button = container.querySelector('.verif-quar-alt-del') as HTMLElement;
    // 3 members = "2 more" folded, but deleting takes all THREE.
    expect(container.querySelector('.verif-quar-alt-btn')?.textContent).toContain('2 more');
    expect(button.textContent).toContain('Delete all 3');

    fireEvent.click(button);
    expect(onDeleteGroup).toHaveBeenCalledWith(expect.objectContaining({ id: 'q1' }), 3);
  });

  it('leaves a lone entry alone', () => {
    // One candidate is not a group; it already has a Delete of its own.
    const { container } = renderList([{ key: 'g', members: [entry('q1')] }], vi.fn());
    expect(container.querySelector('.verif-quar-alt-del')).toBeNull();
    expect(container.querySelector('.verif-quar-alt-btn')).toBeNull();
  });

  it('shows no group delete when the page does not wire one', () => {
    const { container } = renderList([{ key: 'g', members: [entry('q1'), entry('q2')] }]);
    expect(container.querySelector('.verif-quar-alt-del')).toBeNull();
    expect(container.querySelector('.verif-quar-alt-btn')).not.toBeNull();
  });
});

describe('deleting a group', () => {
  it('confirms with the count, then asks the server for the siblings', async () => {
    const urls = captureDelete({ success: true, deleted: 3 });
    const onDone = vi.fn();
    await quarantineDeleteGroup(entry('q1'), 3, onDone);

    expect(confirms[0]).toMatchObject({ destructive: true, confirmText: 'Delete all 3' });
    expect(String(confirms[0].message)).toContain('all 3 quarantined candidates');
    expect(urls).toHaveLength(1);
    expect(new URL(urls[0]).search).toBe('?siblings=1');
    expect(toasts[0]).toBe('Deleted 3 quarantined files');
    expect(onDone).toHaveBeenCalled();
  });

  it('reports the SERVER tally, not the count the list happened to show', async () => {
    // The list can be seconds stale; another candidate may have landed since.
    captureDelete({ success: true, deleted: 5 });
    await quarantineDeleteGroup(entry('q1'), 3, vi.fn());
    expect(toasts[0]).toBe('Deleted 5 quarantined files');
  });

  it('deletes nothing when the confirm is declined', async () => {
    confirmAnswer = false;
    const urls = captureDelete();
    const onDone = vi.fn();
    await quarantineDeleteGroup(entry('q1'), 3, onDone);
    expect(urls).toHaveLength(0);
    expect(onDone).not.toHaveBeenCalled();
  });

  it('does not widen the per-row delete', async () => {
    // The row button still takes exactly one candidate - no siblings flag.
    const urls = captureDelete({ success: true, deleted: 1 });
    await quarantineDeleteEntry(entry('q1'), vi.fn());
    expect(new URL(urls[0]).search).toBe('');
  });
});
