/**
 * The wish tier (#1199, E-13): a profile in a library of its own may monitor
 * and search there; everything that changes files stays the admin's.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ActionButton, LibraryV2CanWishContext, LibraryV2CanWriteContext } from './library-v2-page';

function renderAs(canWrite: boolean, canWish: boolean) {
  return render(
    <LibraryV2CanWriteContext.Provider value={canWrite}>
      <LibraryV2CanWishContext.Provider value={canWish}>
        <ActionButton icon="automatic" label="Automatic Search" requiresWish onClick={() => {}} />
        <ActionButton icon="edit" label="Retag" onClick={() => {}} />
      </LibraryV2CanWishContext.Provider>
    </LibraryV2CanWriteContext.Provider>,
  );
}

describe('the wish tier', () => {
  it('lets a profile in its own library search but not change files', () => {
    renderAs(false, true);
    const search = screen.getByRole('button', { name: /Automatic Search/ });
    expect(search).toBeEnabled();
    expect(search).toHaveAttribute('data-requires-wish');
    expect(search).not.toHaveAttribute('data-requires-write');
    expect(screen.getByRole('button', { name: /Retag/ })).toBeDisabled();
  });

  it('gives a read-only profile neither', () => {
    renderAs(false, false);
    expect(screen.getByRole('button', { name: /Automatic Search/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Retag/ })).toBeDisabled();
  });

  it('gives the admin both', () => {
    renderAs(true, false);
    expect(screen.getByRole('button', { name: /Automatic Search/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Retag/ })).toBeEnabled();
  });
});
