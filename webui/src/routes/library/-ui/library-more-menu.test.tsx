/**
 * #1909: the library header was four equal buttons (Radio, Export,
 * Automatic Search, Watch All). Radio stays out front, the three
 * library-wide jobs moved into More with a line each.
 */

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

import { LibraryMoreMenu, LibraryV2CanWriteContext } from './library-v2-page';

function renderMenu(canWrite = true) {
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <LibraryV2CanWriteContext.Provider value={canWrite}>
        <LibraryMoreMenu />
      </LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'More library actions' }));
  return [...document.querySelectorAll('.sync-more-menu [role="menuitem"]')] as HTMLButtonElement[];
}

describe('the library More menu', () => {
  it('lists the three library-wide jobs, each saying what it does', () => {
    const items = renderMenu();
    expect(items.map((i) => i.querySelector('.sync-more-name')?.textContent)).toEqual([
      'Automatic Search',
      'Watch All',
      'Export artists',
    ]);
    for (const i of items) expect(i.querySelector('.sync-more-desc')?.textContent).toBeTruthy();
  });

  it('a profile that cannot change the library cannot start the library-wide writes', () => {
    const items = renderMenu(false);
    expect(items[0]).toBeDisabled();
    expect(items[1]).toBeDisabled();
    // export only reads
    expect(items[2]).toBeEnabled();
  });

  it('Export opens its window', () => {
    const items = renderMenu();
    fireEvent.click(items[2]);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
