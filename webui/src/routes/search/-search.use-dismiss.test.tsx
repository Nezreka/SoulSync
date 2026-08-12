import { fireEvent, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { shouldDismiss, useDismissOnOutsideClick } from './-search.use-dismiss';

afterEach(() => {
  document.body.innerHTML = '';
});

/** Build a click target inside the given markup and return it. */
function targetInside(html: string, selector: string): Element {
  document.body.innerHTML = html;
  return document.querySelector(selector)!;
}

describe('shouldDismiss', () => {
  it('dismisses on a click somewhere unrelated', () => {
    expect(shouldDismiss(targetInside('<main><p id="t">hi</p></main>', '#t'))).toBe(true);
  });

  it('keeps the dropdown open while you are using the input', () => {
    const target = targetInside(
      '<div class="enhanced-search-input-wrapper"><input id="t"></div>',
      '#t',
    );
    expect(shouldDismiss(target)).toBe(false);
  });

  it('keeps it open for the source row, which sits OUTSIDE the dropdown', () => {
    // The icons live above the input and switch which cached source is shown —
    // dismissing on them would close the results they just asked for.
    const target = targetInside('<div id="enh-source-row"><button id="t"></button></div>', '#t');
    expect(shouldDismiss(target)).toBe(false);
  });

  it('keeps it open for the download modal that opens on top of it', () => {
    const target = targetInside(
      '<div class="download-missing-modal"><button id="t">×</button></div>',
      '#t',
    );
    expect(shouldDismiss(target)).toBe(false);
  });

  it('keeps it open for the media player and the now-playing modal (#732)', () => {
    // Clicking the mini bar to expand it used to dismiss the search results.
    expect(shouldDismiss(targetInside('<div id="media-player"><b id="t"></b></div>', '#t'))).toBe(
      false,
    );
    expect(
      shouldDismiss(targetInside('<div id="np-modal-overlay"><b id="t"></b></div>', '#t')),
    ).toBe(false);
  });

  it('keeps it open for a click on a RESULT inside the dropdown', () => {
    // The regression that broke music-video downloads: vanilla nested the
    // dropdown inside the input wrapper so results were implicitly exempt;
    // the React markup makes them siblings, so the dropdown must be exempt
    // BY NAME or every result click throws the results away.
    const target = targetInside(
      '<div id="enhanced-dropdown"><div class="enh-video-card" id="t"></div></div>',
      '#t',
    );
    expect(shouldDismiss(target)).toBe(false);
  });
  it('dismisses when the target is not an element at all', () => {
    // A click reported against the document itself is still a click outside.
    expect(shouldDismiss(document)).toBe(true);
    expect(shouldDismiss(null)).toBe(true);
  });
});

describe('useDismissOnOutsideClick', () => {
  it('fires once for an outside click while open', () => {
    document.body.innerHTML = '<p id="outside">x</p>';
    const onDismiss = vi.fn();
    renderHook(() => useDismissOnOutsideClick(true, onDismiss));

    fireEvent.click(document.getElementById('outside')!);
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it('listens for nothing while closed', () => {
    document.body.innerHTML = '<p id="outside">x</p>';
    const onDismiss = vi.fn();
    renderHook(() => useDismissOnOutsideClick(false, onDismiss));

    fireEvent.click(document.getElementById('outside')!);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('ignores a click on an exempt element', () => {
    document.body.innerHTML = '<div id="enh-source-row"><button id="icon"></button></div>';
    const onDismiss = vi.fn();
    renderHook(() => useDismissOnOutsideClick(true, onDismiss));

    fireEvent.click(document.getElementById('icon')!);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('stops listening once unmounted', () => {
    document.body.innerHTML = '<p id="outside">x</p>';
    const onDismiss = vi.fn();
    const { unmount } = renderHook(() => useDismissOnOutsideClick(true, onDismiss));
    unmount();

    fireEvent.click(document.getElementById('outside')!);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('survives the clicked node being re-rendered out of the dropdown', () => {
    // A video click sets download-progress state, which can replace the
    // clicked node before this document listener runs. closest() on the
    // detached node misses the dropdown; composedPath() still holds the
    // ancestry captured at dispatch.
    document.body.innerHTML =
      '<div id="enhanced-dropdown"><div class="enh-video-card" id="card"></div></div>';
    const card = document.getElementById('card')!;
    card.addEventListener('click', () => card.remove()); // the re-render, worst case
    const onDismiss = vi.fn();
    renderHook(() => useDismissOnOutsideClick(true, onDismiss));

    fireEvent.click(card);
    expect(onDismiss).not.toHaveBeenCalled();
  });
});
