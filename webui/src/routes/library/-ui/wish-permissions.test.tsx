/**
 * The wish tier (#1199, E-13): a profile in a library of its own may monitor
 * and search there; everything that changes files stays the admin's.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  ActionButton,
  LibraryV2CanDownloadContext,
  LibraryV2CanGrabContext,
  LibraryV2CanWishContext,
  LibraryV2CanWriteContext,
} from './library-v2-page';

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
    // #2210: what it may not do isn't drawn, not drawn dead
    expect(screen.queryByRole('button', { name: /Retag/ })).toBeNull();
  });

  it('gives a read-only profile neither', () => {
    renderAs(false, false);
    expect(screen.queryByRole('button', { name: /Automatic Search/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Retag/ })).toBeNull();
  });

  it('gives the admin both', () => {
    renderAs(true, false);
    expect(screen.getByRole('button', { name: /Automatic Search/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Retag/ })).toBeEnabled();
  });
});

describe('#2173: actions that start a download', () => {
  function renderMember(canDownload: boolean, canGrab: boolean, admin = false) {
    return render(
      <LibraryV2CanWriteContext.Provider value={admin}>
        <LibraryV2CanWishContext.Provider value>
          <LibraryV2CanDownloadContext.Provider value={canDownload}>
            <LibraryV2CanGrabContext.Provider value={canGrab}>
              <ActionButton
                icon="automatic"
                label="Automatic Search"
                requiresWish
                requiresDownload="search"
                onClick={() => {}}
              />
              <ActionButton
                icon="interactive"
                label="Interactive Search"
                requiresWish
                requiresDownload="grab"
                onClick={() => {}}
              />
              <ActionButton icon="star" label="Add to Wishlist" requiresWish onClick={() => {}} />
            </LibraryV2CanGrabContext.Provider>
          </LibraryV2CanDownloadContext.Provider>
        </LibraryV2CanWishContext.Provider>
      </LibraryV2CanWriteContext.Provider>,
    );
  }

  it('are not drawn for a profile that asks first; the Wishlist stays', () => {
    renderMember(false, false);
    expect(screen.queryByRole('button', { name: /Automatic Search/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Interactive Search/ })).toBeNull();
    expect(screen.getByRole('button', { name: /Add to Wishlist/ })).toBeEnabled();
  });

  it('a profile that downloads searches; picking by hand needs can_grab too', () => {
    renderMember(true, false);
    expect(screen.getByRole('button', { name: /Automatic Search/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Interactive Search/ })).toBeNull();
  });

  it('the admin has both', () => {
    renderMember(false, false, true);
    expect(screen.getByRole('button', { name: /Automatic Search/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Interactive Search/ })).toBeEnabled();
  });

  it('follows the live permission map: an admin turning downloads off hides them', () => {
    const w = window as unknown as { canDownload?: () => boolean };
    w.canDownload = () => false;
    try {
      renderMember(true, true);
      expect(screen.queryByRole('button', { name: /Automatic Search/ })).toBeNull();
    } finally {
      delete w.canDownload;
    }
  });
});
