import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExplorerArtist } from './-explorer.types';

import {
  buildConnectionPaths,
  CONNECTION_BUILD_DELAY_MS,
  CONNECTION_EXPAND_DELAY_MS,
  CONNECTION_RESIZE_DEBOUNCE_MS,
  EMPTY_CONNECTIONS,
  useExplorerConnections,
  type RedrawOptions,
} from './-explorer.connections';
import {
  ALBUM_CLICK_DELAY_MS,
  createAlbumClickController,
  useExplorerPan,
  useExplorerZoom,
} from './-explorer.interactions';
import { ExplorerTree } from './-ui/explorer-tree';

/**
 * The interaction layer: the click discriminator, the measured connection
 * geometry, and the zoom/pan controllers.
 */

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('createAlbumClickController', () => {
  beforeEach(() => vi.useFakeTimers());

  function setup() {
    const onSelect = vi.fn();
    const onExpandTracks = vi.fn();
    const controller = createAlbumClickController({ onSelect, onExpandTracks });
    return { controller, onSelect, onExpandTracks };
  }

  it('waits 250ms before treating a click as a selection', () => {
    expect(ALBUM_CLICK_DELAY_MS).toBe(250);
    const { controller, onSelect, onExpandTracks } = setup();
    controller.click('al1');
    vi.advanceTimersByTime(249);
    expect(onSelect).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onSelect).toHaveBeenCalledWith('al1');
    expect(onExpandTracks).not.toHaveBeenCalled();
  });

  it('a second click inside the window opens the tracklist instead', () => {
    const { controller, onSelect, onExpandTracks } = setup();
    controller.click('al1');
    vi.advanceTimersByTime(100);
    controller.click('al1');
    expect(onExpandTracks).toHaveBeenCalledWith('al1');
    // And the selection that was pending must NOT also land.
    vi.advanceTimersByTime(500);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('never fetches a tracklist for a positional fallback id', () => {
    const { controller, onSelect, onExpandTracks } = setup();
    controller.click('Boards_of_Canada_2');
    vi.advanceTimersByTime(100);
    controller.click('Boards_of_Canada_2');
    expect(onExpandTracks).not.toHaveBeenCalled();
    // The double click still cancels the pending selection, exactly as the
    // vanilla did — it returned before reaching the fetch, not before the
    // clearTimeout.
    vi.advanceTimersByTime(500);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('two clicks either side of the window are two selections', () => {
    const { controller, onSelect, onExpandTracks } = setup();
    controller.click('al1');
    vi.advanceTimersByTime(250);
    controller.click('al1');
    vi.advanceTimersByTime(250);
    expect(onSelect.mock.calls).toEqual([['al1'], ['al1']]);
    expect(onExpandTracks).not.toHaveBeenCalled();
  });

  it('keeps the vanilla single-slot quirk when two albums are clicked in a row', () => {
    // The pending slot is shared. Clicking B does not cancel A's timer, so A
    // still gets selected; A's timer then clears the slot, so an immediate
    // second click on B reads as another single click rather than a
    // double-click. This is the vanilla's behaviour, preserved deliberately.
    const { controller, onSelect, onExpandTracks } = setup();
    controller.click('a');
    vi.advanceTimersByTime(50);
    controller.click('b');
    vi.advanceTimersByTime(200); // a's 250ms elapses, clearing the slot
    expect(onSelect.mock.calls).toEqual([['a']]);
    controller.click('b');
    vi.advanceTimersByTime(250);
    expect(onSelect.mock.calls).toEqual([['a'], ['b'], ['b']]);
    expect(onExpandTracks).not.toHaveBeenCalled();
  });

  it('dispose drops a pending selection', () => {
    const { controller, onSelect } = setup();
    controller.click('al1');
    controller.dispose();
    vi.advanceTimersByTime(1000);
    expect(onSelect).not.toHaveBeenCalled();
  });
});

// ── Connection geometry ───────────────────────────────────────────────────

function setRect(
  element: Element,
  rect: { left: number; top: number; bottom: number; width: number },
) {
  Object.defineProperty(element, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ ...rect, right: rect.left + rect.width, height: rect.bottom - rect.top }),
  });
}

function setBox(element: Element, box: Record<string, number>) {
  for (const [key, value] of Object.entries(box)) {
    Object.defineProperty(element, key, { configurable: true, value });
  }
}

const CONNECTED_ARTISTS: ExplorerArtist[] = [
  { name: 'Alpha', albums: [{ spotify_id: 'al1', title: 'One' }] },
  { name: 'Beta', albums: [{ spotify_id: 'al2', title: 'Two' }] },
];

function renderMeasurableTree(zoom = 1) {
  const view = render(
    <ExplorerTree
      meta={{ type: 'meta', playlist_name: 'Mix', total_tracks: 2, total_artists: 2 }}
      artists={CONNECTED_ARTISTS}
      expandedArtists={new Set(['Alpha'])}
      artistsWithSelection={new Set()}
      selectedAlbums={new Set()}
      addedAlbums={new Set()}
      expandedTracks={{ al1: [{ track_number: 1, name: 'Radiator', duration_ms: 1000 }] }}
      onToggleArtist={vi.fn()}
      onAlbumClick={vi.fn()}
      zoom={zoom}
      connections={{ width: 0, height: 0, paths: [] }}
    />,
  );
  const tree = view.container.querySelector('#explorer-tree') as HTMLElement;
  setBox(tree, { scrollWidth: 1000, offsetWidth: 800, scrollHeight: 600, offsetHeight: 500 });
  setRect(tree, { left: 0, top: 0, bottom: 600, width: 1000 });
  setRect(view.container.querySelector('#explorer-root')!, {
    left: 400,
    top: 0,
    bottom: 100,
    width: 200,
  });
  setRect(view.container.querySelector('#explorer-node-Alpha')!, {
    left: 100,
    top: 200,
    bottom: 300,
    width: 100,
  });
  setRect(view.container.querySelector('#explorer-node-Beta')!, {
    left: 600,
    top: 200,
    bottom: 300,
    width: 100,
  });
  setRect(view.container.querySelector('.explorer-node-album')!, {
    left: 80,
    top: 400,
    bottom: 480,
    width: 80,
  });
  setRect(view.container.querySelector('.explorer-node-track')!, {
    left: 80,
    top: 520,
    bottom: 560,
    width: 60,
  });
  return { view, tree };
}

describe('buildConnectionPaths', () => {
  it('sizes the canvas from the tree box plus slack', () => {
    const { tree } = renderMeasurableTree();
    const geometry = buildConnectionPaths(tree, 1, false);
    expect(geometry.width).toBe(1040);
    expect(geometry.height).toBe(640);
  });

  it('draws root to every artist, and album/track only under the expanded one', () => {
    const { tree } = renderMeasurableTree();
    const { paths } = buildConnectionPaths(tree, 1, false);
    expect(paths.map((path) => path.id)).toEqual([
      'root-0-Alpha',
      'album-0-Alpha-al1',
      'track-0-Alpha-al1-0',
      'root-1-Beta',
    ]);
    expect(paths.map((path) => path.d)).toEqual([
      // root bottom 100 -> artist top 200, bend at 100 + 100*0.45
      'M 500 100 C 500 145, 150 145, 150 200',
      // artist bottom 300 -> album top 400
      'M 150 300 C 150 345, 120 345, 120 400',
      // album bottom 480 -> track top 520
      'M 120 480 C 120 498, 110 498, 110 520',
      'M 500 100 C 500 145, 650 145, 650 200',
    ]);
  });

  it('gives each tier its own stroke', () => {
    const { tree } = renderMeasurableTree();
    const { paths } = buildConnectionPaths(tree, 1, false);
    expect(paths.map((path) => [path.stroke, path.strokeWidth])).toEqual([
      ['url(#explorer-grad-root)', '1.5'],
      ['url(#explorer-grad-album)', '1'],
      ['rgba(255,255,255,0.05)', '0.8'],
      ['url(#explorer-grad-root)', '1.5'],
    ]);
  });

  it('divides measured positions by the zoom', () => {
    const { tree } = renderMeasurableTree(2);
    const { paths } = buildConnectionPaths(tree, 2, false);
    // Every coordinate of the first curve is exactly half its scale-1 value.
    expect(paths[0]?.d).toBe('M 250 50 C 250 72.5, 75 72.5, 75 100');
  });

  it('flags the paths as animated only when asked', () => {
    const { tree } = renderMeasurableTree();
    expect(buildConnectionPaths(tree, 1, false).paths.every((p) => !p.animated)).toBe(true);
    expect(buildConnectionPaths(tree, 1, true).paths.every((p) => p.animated)).toBe(true);
  });

  // These two pin guards the rendered tree cannot currently exercise: the
  // component never gives a collapsed artist album children, and never nests
  // an album deeper than one level. Both guards are carried over from the
  // vanilla, and buildConnectionPaths takes ANY element — so the contract is
  // pinned against hand-built DOM rather than left to a mutation that no test
  // can see.
  function handBuiltTree(html: string) {
    const tree = document.createElement('div');
    tree.innerHTML = `<div id="explorer-root"></div>${html}`;
    setBox(tree, { scrollWidth: 0, offsetWidth: 0, scrollHeight: 0, offsetHeight: 0 });
    setRect(tree, { left: 0, top: 0, bottom: 0, width: 0 });
    for (const node of tree.querySelectorAll('*')) {
      setRect(node, { left: 0, top: 0, bottom: 0, width: 0 });
    }
    return tree;
  }

  it('skips the albums of an artist that is not expanded', () => {
    const tree = handBuiltTree(`
      <div class="explorer-branch">
        <div class="explorer-node explorer-node-artist" data-key="Collapsed"></div>
        <div class="explorer-children">
          <div class="explorer-branch">
            <div class="explorer-node explorer-node-album" data-id="ghost"></div>
          </div>
        </div>
      </div>`);
    expect(buildConnectionPaths(tree, 1, false).paths.map((p) => p.id)).toEqual([
      'root-0-Collapsed',
    ]);
  });

  it('claims only the album nodes directly under the artist', () => {
    const tree = handBuiltTree(`
      <div class="explorer-branch">
        <div class="explorer-node explorer-node-artist expanded" data-key="Deep"></div>
        <div class="explorer-children">
          <div class="explorer-branch">
            <div class="explorer-node explorer-node-album" data-id="own"></div>
            <div class="explorer-children">
              <div class="explorer-branch">
                <div class="explorer-node explorer-node-album" data-id="nested"></div>
              </div>
            </div>
          </div>
        </div>
      </div>`);
    const ids = buildConnectionPaths(tree, 1, false).paths.map((p) => p.id);
    expect(ids).toEqual(['root-0-Deep', 'album-0-Deep-own']);
    expect(ids).not.toContain('album-0-Deep-nested');
  });

  it('keeps path ids unique when two artists sanitise to the same key', () => {
    // explorerArtistKey turns both "AC/DC" and "AC-DC" into "AC_DC". Without
    // the position prefix both curves would be id "root-AC_DC", and React
    // would render two SVG children with the same key.
    const tree = handBuiltTree(`
      <div class="explorer-branch">
        <div class="explorer-node explorer-node-artist" data-key="AC_DC"></div>
      </div>
      <div class="explorer-branch">
        <div class="explorer-node explorer-node-artist" data-key="AC_DC"></div>
      </div>`);
    const ids = buildConnectionPaths(tree, 1, false).paths.map((p) => p.id);
    expect(ids).toEqual(['root-0-AC_DC', 'root-1-AC_DC']);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('draws nothing but still reports a size when there is no root', () => {
    const empty = document.createElement('div');
    setBox(empty, { scrollWidth: 10, offsetWidth: 20, scrollHeight: 30, offsetHeight: 40 });
    const geometry = buildConnectionPaths(empty, 1, false);
    expect(geometry).toEqual({ width: 60, height: 80, paths: [] });
  });
});

// ── The redraw scheduler ──────────────────────────────────────────────────

function ConnectionsHarness({
  hasTree,
  zoom,
  onReady,
}: {
  hasTree: boolean;
  zoom: number;
  onReady: (schedule: (options?: RedrawOptions) => void) => void;
}) {
  const treeRef = useRef<HTMLDivElement>(null);
  const { geometry, scheduleRedraw } = useExplorerConnections(treeRef, zoom, hasTree);
  onReady(scheduleRedraw);
  return (
    <>
      <ExplorerTree
        meta={hasTree ? { type: 'meta', playlist_name: 'Mix', total_artists: 2 } : null}
        artists={CONNECTED_ARTISTS}
        expandedArtists={new Set(['Alpha'])}
        artistsWithSelection={new Set()}
        selectedAlbums={new Set()}
        addedAlbums={new Set()}
        expandedTracks={{}}
        onToggleArtist={vi.fn()}
        onAlbumClick={vi.fn()}
        zoom={zoom}
        connections={geometry}
        treeRef={treeRef}
      />
      <span data-testid="paths">{geometry.paths.length}</span>
      <span data-testid="width">{geometry.width}</span>
    </>
  );
}

describe('useExplorerConnections', () => {
  it('uses the vanilla delays', () => {
    expect(CONNECTION_RESIZE_DEBOUNCE_MS).toBe(150);
    expect(CONNECTION_EXPAND_DELAY_MS).toBe(50);
    expect(CONNECTION_BUILD_DELAY_MS).toBe(100);
    expect(EMPTY_CONNECTIONS).toEqual({ width: 0, height: 0, paths: [] });
  });

  function mountHarness(hasTree = true, zoom = 1) {
    vi.useFakeTimers();
    let schedule: (options?: RedrawOptions) => void = () => {};
    const view = render(
      <ConnectionsHarness
        hasTree={hasTree}
        zoom={zoom}
        onReady={(next) => {
          schedule = next;
        }}
      />,
    );
    const tree = view.container.querySelector('#explorer-tree') as HTMLElement;
    setBox(tree, { scrollWidth: 300, offsetWidth: 200, scrollHeight: 100, offsetHeight: 50 });
    setRect(tree, { left: 0, top: 0, bottom: 100, width: 300 });
    for (const node of view.container.querySelectorAll('.explorer-node, #explorer-root')) {
      setRect(node, { left: 0, top: 0, bottom: 10, width: 10 });
    }
    return { view, schedule: () => schedule };
  }

  it('waits a frame AND the delay before measuring', () => {
    const { view, schedule } = mountHarness();
    expect(view.getByTestId('paths').textContent).toBe('0');

    act(() => schedule()({ delayMs: CONNECTION_BUILD_DELAY_MS }));
    // A frame has not even been served yet.
    act(() => {
      vi.advanceTimersByTime(20);
    });
    expect(view.getByTestId('paths').textContent).toBe('0');

    act(() => {
      vi.advanceTimersByTime(CONNECTION_BUILD_DELAY_MS);
    });
    // root -> Alpha, Alpha -> its album, root -> Beta
    expect(view.getByTestId('paths').textContent).toBe('3');
    expect(view.getByTestId('width').textContent).toBe('340');
  });

  it('collapses a burst of redraws so the LAST one wins', () => {
    const { view, schedule } = mountHarness();
    act(() => {
      // A queued animated redraw must not be able to overwrite a later plain
      // one — the pending frame/timer is cancelled, not stacked.
      schedule()({ animate: true });
      schedule()({ animate: true });
      schedule()({ animate: false });
      vi.advanceTimersByTime(100);
    });
    const paths = view.container.querySelectorAll('path');
    expect(paths).toHaveLength(3);
    expect([...paths].some((p) => p.getAttribute('class')?.includes('animated'))).toBe(false);
  });

  it('re-measures on a resize, but only after the debounce', () => {
    const { view, schedule } = mountHarness();
    const tree = view.container.querySelector('#explorer-tree') as HTMLElement;
    act(() => {
      schedule()();
      vi.advanceTimersByTime(100);
    });
    expect(view.getByTestId('width').textContent).toBe('340');

    // Grow the tree, then tell the page the window changed.
    setBox(tree, { scrollWidth: 900, offsetWidth: 200, scrollHeight: 100, offsetHeight: 50 });
    act(() => {
      window.dispatchEvent(new Event('resize'));
      vi.advanceTimersByTime(CONNECTION_RESIZE_DEBOUNCE_MS - 1);
    });
    expect(view.getByTestId('width').textContent).toBe('340');

    act(() => {
      vi.advanceTimersByTime(50);
    });
    expect(view.getByTestId('width').textContent).toBe('940');
  });

  it('clears a measured geometry when the tree goes away', () => {
    const { view, schedule } = mountHarness();
    act(() => {
      schedule()();
      vi.advanceTimersByTime(100);
    });
    expect(view.getByTestId('paths').textContent).toBe('3');

    // Rebuilding from scratch drops the tree; the stale curves must go with
    // it, or they would hang in the empty viewport.
    view.rerender(<ConnectionsHarness hasTree={false} zoom={1} onReady={() => {}} />);
    expect(view.getByTestId('paths').textContent).toBe('0');
    expect(view.getByTestId('width').textContent).toBe('0');
  });

  it('cancels the pending frame when it unmounts', () => {
    const { view, schedule } = mountHarness();
    act(() => schedule()({ delayMs: CONNECTION_BUILD_DELAY_MS }));
    // Spy only now: scheduling already cancels whatever was queued before it,
    // so counting from zero would attribute that to the unmount.
    const cancel = vi.spyOn(globalThis, 'cancelAnimationFrame');
    view.unmount();
    // The queued frame is cancelled rather than left to measure a tree that
    // is no longer on the page.
    expect(cancel).toHaveBeenCalledTimes(1);
    cancel.mockRestore();
  });
});

// ── Zoom + pan ────────────────────────────────────────────────────────────

function ZoomHarness({
  onReady,
}: {
  onReady: (controls: ReturnType<typeof useExplorerZoom>) => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const treeRef = useRef<HTMLDivElement>(null);
  const controls = useExplorerZoom(viewportRef, treeRef);
  onReady(controls);
  return (
    <div ref={viewportRef} data-testid="viewport">
      <div ref={treeRef} data-testid="tree" />
      <span data-testid="zoom">{controls.zoom}</span>
    </div>
  );
}

describe('useExplorerZoom', () => {
  let controls: ReturnType<typeof useExplorerZoom>;

  function renderZoom() {
    return render(
      <ZoomHarness
        onReady={(next) => {
          controls = next;
        }}
      />,
    );
  }

  it('clamps as it steps', () => {
    const { getByTestId } = renderZoom();
    act(() => controls.zoomBy(0.15));
    expect(getByTestId('zoom').textContent).toBe('1.15');
    act(() => controls.zoomBy(-5));
    expect(getByTestId('zoom').textContent).toBe('0.2');
    act(() => controls.zoomBy(9));
    expect(getByTestId('zoom').textContent).toBe('3');
    act(() => controls.resetZoom());
    expect(getByTestId('zoom').textContent).toBe('1');
  });

  it('zooms on wheel, and stops the page scrolling underneath', () => {
    const { getByTestId } = renderZoom();
    const viewport = getByTestId('viewport');

    const up = new WheelEvent('wheel', { deltaY: -120, cancelable: true, bubbles: true });
    act(() => {
      viewport.dispatchEvent(up);
    });
    expect(up.defaultPrevented).toBe(true);
    expect(getByTestId('zoom').textContent).toBe('1.08');

    act(() => {
      viewport.dispatchEvent(new WheelEvent('wheel', { deltaY: 120, cancelable: true }));
    });
    expect(getByTestId('zoom').textContent).toBe('1');
  });

  it('detaches the wheel listener on unmount', () => {
    const { getByTestId, unmount } = renderZoom();
    const viewport = getByTestId('viewport');
    const remove = vi.spyOn(viewport, 'removeEventListener');
    unmount();
    expect(remove).toHaveBeenCalledWith('wheel', expect.any(Function));
  });

  it('fits the tree to the viewport and centres it', () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.push(cb);
      return frames.length;
    });
    const { getByTestId } = renderZoom();
    const viewport = getByTestId('viewport');
    const tree = getByTestId('tree');
    setBox(tree, { scrollWidth: 2000, scrollHeight: 500 });
    setBox(viewport, { clientWidth: 1040, clientHeight: 1040 });
    let scrollLeft = 0;
    let scrollTop = 99;
    Object.defineProperty(viewport, 'scrollLeft', {
      configurable: true,
      get: () => scrollLeft,
      set: (v: number) => {
        scrollLeft = v;
      },
    });
    Object.defineProperty(viewport, 'scrollTop', {
      configurable: true,
      get: () => scrollTop,
      set: (v: number) => {
        scrollTop = v;
      },
    });

    act(() => controls.fitToView());
    // 1000 usable width / 2000 tree = 0.5, the smaller of the two axes.
    expect(getByTestId('zoom').textContent).toBe('0.5');
    // The scroll is deferred to the next frame so the new scale has rendered.
    expect(scrollLeft).toBe(0);
    act(() => {
      frames.forEach((frame) => frame(0));
    });
    expect(scrollTop).toBe(0);
    expect(scrollLeft).toBe(0); // 2000 * 0.5 = 1000, which fits in 1000
    vi.unstubAllGlobals();
  });
});

function PanHarness() {
  const viewportRef = useRef<HTMLDivElement>(null);
  useExplorerPan(viewportRef);
  return <div ref={viewportRef} data-testid="viewport" />;
}

describe('useExplorerPan', () => {
  function renderPan() {
    const view = render(<PanHarness />);
    const viewport = view.getByTestId('viewport');
    let scrollLeft = 500;
    let scrollTop = 300;
    Object.defineProperty(viewport, 'scrollLeft', {
      configurable: true,
      get: () => scrollLeft,
      set: (v: number) => {
        scrollLeft = v;
      },
    });
    Object.defineProperty(viewport, 'scrollTop', {
      configurable: true,
      get: () => scrollTop,
      set: (v: number) => {
        scrollTop = v;
      },
    });
    return { view, viewport, read: () => ({ scrollLeft, scrollTop }) };
  }

  it('pans on a right-drag, in the opposite direction to the pointer', () => {
    const { viewport, read } = renderPan();
    fireEvent.mouseDown(viewport, { button: 2, clientX: 100, clientY: 100 });
    expect(viewport.style.cursor).toBe('grabbing');
    fireEvent.mouseMove(document, { clientX: 130, clientY: 90 });
    // Dragging right by 30 scrolls LEFT by 30, the way grabbing paper works.
    expect(read()).toEqual({ scrollLeft: 470, scrollTop: 310 });
    fireEvent.mouseUp(document);
    expect(viewport.style.cursor).toBe('');
    fireEvent.mouseMove(document, { clientX: 400, clientY: 400 });
    expect(read()).toEqual({ scrollLeft: 470, scrollTop: 310 });
  });

  it('pans on a middle-drag too, and ignores a left-drag', () => {
    const { viewport, read } = renderPan();
    fireEvent.mouseDown(viewport, { button: 1, clientX: 0, clientY: 0 });
    fireEvent.mouseMove(document, { clientX: -10, clientY: -10 });
    expect(read()).toEqual({ scrollLeft: 510, scrollTop: 310 });
    fireEvent.mouseUp(document);

    fireEvent.mouseDown(viewport, { button: 0, clientX: 0, clientY: 0 });
    expect(viewport.style.cursor).toBe('');
    fireEvent.mouseMove(document, { clientX: 999, clientY: 999 });
    expect(read()).toEqual({ scrollLeft: 510, scrollTop: 310 });
  });

  it('suppresses the context menu inside the viewport, since right-drag pans', () => {
    const { viewport } = renderPan();
    const event = new MouseEvent('contextmenu', { cancelable: true, bubbles: true });
    viewport.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('releases the document listeners when it unmounts mid-drag', () => {
    const { viewport, view, read } = renderPan();
    fireEvent.mouseDown(viewport, { button: 2, clientX: 0, clientY: 0 });
    view.unmount();
    fireEvent.mouseMove(document, { clientX: 100, clientY: 100 });
    expect(read()).toEqual({ scrollLeft: 500, scrollTop: 300 });
  });
});
