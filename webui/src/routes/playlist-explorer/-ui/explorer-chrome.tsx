/**
 * The three bits of chrome around the tree — the sticky action bar, the build
 * progress bar, and the zoom cluster inside the viewport
 * (index.html:4182-4239, and explorerBuildTree :320 for the progress numbers).
 */

import { explorerSelectionLabel } from '../-explorer.core';

export interface ExplorerActionBarProps {
  selectedCount: number;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onAddToWishlist: () => void;
}

export function ExplorerActionBar({
  selectedCount,
  onSelectAll,
  onDeselectAll,
  onAddToWishlist,
}: ExplorerActionBarProps) {
  return (
    <div className="explorer-action-bar" id="explorer-action-bar" style={{ display: 'flex' }}>
      <div className="explorer-action-left">
        <span className="explorer-action-eyebrow">Selection</span>
        <span className="explorer-selection-count" id="explorer-selection-count">
          {explorerSelectionLabel(selectedCount)}
        </span>
      </div>
      <div className="explorer-action-buttons">
        <button type="button" className="btn btn--sm btn--secondary" onClick={onSelectAll}>
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <polyline points="9 11 12 14 22 4" />
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
          Select All
        </button>
        <button type="button" className="btn btn--sm btn--secondary" onClick={onDeselectAll}>
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" />
          </svg>
          Deselect
        </button>
        <button type="button" className="btn btn--sm btn--primary" onClick={onAddToWishlist}>
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
          </svg>
          Add to Wishlist
        </button>
      </div>
      <span className="explorer-nav-hint">
        Scroll to zoom · Right-drag to pan · Double-click album for tracks
      </span>
    </div>
  );
}

export interface ExplorerProgressProps {
  percent: number;
  text: string;
}

export function ExplorerProgress({ percent, text }: ExplorerProgressProps) {
  return (
    <div className="explorer-progress" id="explorer-progress" style={{ display: 'flex' }}>
      <span className="explorer-progress-pct">{Math.round(percent)}%</span>
      <div className="explorer-progress-bar">
        <div
          className="explorer-progress-fill"
          id="explorer-progress-fill"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="explorer-progress-text" id="explorer-progress-text">
        {text}
      </span>
    </div>
  );
}

export interface ExplorerZoomControlsProps {
  onZoom: (delta: number) => void;
  onFitToView: () => void;
  onResetZoom: () => void;
}

export function ExplorerZoomControls({
  onZoom,
  onFitToView,
  onResetZoom,
}: ExplorerZoomControlsProps) {
  return (
    <div className="explorer-zoom-controls">
      <button
        type="button"
        className="explorer-zoom-btn"
        onClick={() => onZoom(0.15)}
        title="Zoom in"
      >
        +
      </button>
      <button
        type="button"
        className="explorer-zoom-btn"
        onClick={() => onZoom(-0.15)}
        title="Zoom out"
      >
        −
      </button>
      <button type="button" className="explorer-zoom-btn" onClick={onFitToView} title="Fit to view">
        ⬜
      </button>
      <button type="button" className="explorer-zoom-btn" onClick={onResetZoom} title="Reset zoom">
        1:1
      </button>
    </div>
  );
}
