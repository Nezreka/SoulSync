/**
 * The connection layer's measuring half (_explorerRedrawAllConnections :925,
 * _explorerSizeSvg :961, _explorerGetPos :974, _explorerApplyTransform :1028).
 *
 * The vanilla drew the curves by walking the laid-out DOM, and so does this:
 * the tree is a flex layout whose node positions are not knowable from the
 * data alone. What changed is when — React re-renders the tree, then this
 * measures it, instead of the two being interleaved.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { ExplorerPath } from './-ui/explorer-connections';

import {
  explorerCurvePath,
  explorerCurveStroke,
  explorerNodePosition,
  explorerSvgSize,
} from './-explorer.core';

export interface ConnectionGeometry {
  width: number;
  height: number;
  paths: ExplorerPath[];
}

export const EMPTY_CONNECTIONS: ConnectionGeometry = { width: 0, height: 0, paths: [] };

/** The window resize listener (:1133) — the redraw debounce. */
export const CONNECTION_RESIZE_DEBOUNCE_MS = 150;
/** explorerToggleArtist (:505) — expand/collapse waits a frame, then a beat, for the flex reflow. */
export const CONNECTION_EXPAND_DELAY_MS = 50;
/** explorerBuildTree (:365) — the first draw after a build, once the tree has settled. */
export const CONNECTION_BUILD_DELAY_MS = 100;

/**
 * Every curve in the tree, measured from the DOM.
 *
 * Walks the same three tiers the vanilla did: root → artist for every artist,
 * artist → album for EXPANDED artists only, album → track for any tracklist
 * that has been fetched.
 *
 * The `expanded` check and the `:scope >` selectors are both carried over
 * verbatim, and both are belt-and-braces against the tree this component
 * actually renders: a collapsed artist has no album children to find, and
 * album nodes only ever sit one level under their artist. They are kept
 * because they are the function's contract — it takes any element, and the
 * tests pin that contract against hand-built DOM the component cannot produce
 * today but a future change might.
 */
export function buildConnectionPaths(
  tree: HTMLElement,
  zoom: number,
  animate: boolean,
): ConnectionGeometry {
  const root = tree.querySelector('#explorer-root');
  const size = explorerSvgSize(
    tree.scrollWidth,
    tree.offsetWidth,
    tree.scrollHeight,
    tree.offsetHeight,
  );
  if (!root) return { ...size, paths: [] };

  const treeRect = tree.getBoundingClientRect();
  const at = (element: Element) =>
    explorerNodePosition(element.getBoundingClientRect(), treeRect, zoom);

  const paths: ExplorerPath[] = [];
  const push = (
    id: string,
    from: { cx: number; bottom: number },
    to: { cx: number; top: number },
    type: 'root' | 'album' | 'track',
  ) => {
    paths.push({
      id,
      d: explorerCurvePath(from.cx, from.bottom, to.cx, to.top),
      ...explorerCurveStroke(type),
      animated: animate,
    });
  };

  const rootPos = at(root);

  // Path ids are prefixed with the artist's POSITION, not just its key.
  // explorerArtistKey collapses every non-alphanumeric to `_`, so "AC/DC" and
  // "AC-DC" share a key — without the position their curves would collide into
  // one id and React would render duplicate keys in the SVG.
  const artistNodes = tree.querySelectorAll('.explorer-node-artist');
  artistNodes.forEach((artistNode, artistIndex) => {
    const artistKey = (artistNode as HTMLElement).dataset.key ?? '';
    const stem = `${artistIndex}-${artistKey}`;
    const artistPos = at(artistNode);
    push(`root-${stem}`, rootPos, artistPos, 'root');

    if (!artistNode.classList.contains('expanded')) return;
    const branch = artistNode.closest('.explorer-branch');
    if (!branch) return;

    for (const albumNode of branch.querySelectorAll(
      ':scope > .explorer-children > .explorer-branch > .explorer-node-album',
    )) {
      const albumId = (albumNode as HTMLElement).dataset.id ?? '';
      const albumPos = at(albumNode);
      push(`album-${stem}-${albumId}`, artistPos, albumPos, 'album');

      const albumBranch = albumNode.closest('.explorer-branch');
      if (!albumBranch) continue;
      const trackNodes = albumBranch.querySelectorAll(
        ':scope > .explorer-children > .explorer-branch > .explorer-node-track',
      );
      trackNodes.forEach((trackNode, index) => {
        push(`track-${stem}-${albumId}-${index}`, albumPos, at(trackNode), 'track');
      });
    }
  });

  return { ...size, paths };
}

export interface RedrawOptions {
  /** Draw-on animation; only the first paint after a build uses it (explorerBuildTree :365). */
  animate?: boolean;
  delayMs?: number;
}

/**
 * Keeps the SVG in step with the laid-out tree.
 *
 * Redraws are always deferred by at least a frame: the vanilla learned the
 * hard way that measuring during a flex reflow leaves the lines drifting away
 * from the nodes they connect (:317).
 */
export function useExplorerConnections(
  treeRef: React.RefObject<HTMLDivElement | null>,
  zoom: number,
  hasTree: boolean,
): { geometry: ConnectionGeometry; scheduleRedraw: (options?: RedrawOptions) => void } {
  const [geometry, setGeometry] = useState<ConnectionGeometry>(EMPTY_CONNECTIONS);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const frameRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPending = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    frameRef.current = null;
    timerRef.current = null;
  }, []);

  const scheduleRedraw = useCallback(
    (options: RedrawOptions = {}) => {
      const { animate = false, delayMs = 0 } = options;
      cancelPending();
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null;
        timerRef.current = setTimeout(() => {
          timerRef.current = null;
          const tree = treeRef.current;
          if (!tree) return;
          setGeometry(buildConnectionPaths(tree, zoomRef.current, animate));
        }, delayMs);
      });
    },
    [cancelPending, treeRef],
  );

  // Zoom scales the tree, so every measured position moves with it.
  useEffect(() => {
    if (!hasTree) return;
    scheduleRedraw();
  }, [zoom, hasTree, scheduleRedraw]);

  useEffect(() => {
    if (!hasTree) {
      setGeometry(EMPTY_CONNECTIONS);
      return;
    }
    let debounce: ReturnType<typeof setTimeout> | null = null;
    const onResize = () => {
      if (debounce !== null) clearTimeout(debounce);
      debounce = setTimeout(() => scheduleRedraw(), CONNECTION_RESIZE_DEBOUNCE_MS);
    };
    window.addEventListener('resize', onResize);
    return () => {
      if (debounce !== null) clearTimeout(debounce);
      window.removeEventListener('resize', onResize);
    };
  }, [hasTree, scheduleRedraw]);

  useEffect(() => cancelPending, [cancelPending]);

  return { geometry, scheduleRedraw };
}
