import { useEffect, useRef, useState } from 'react';

import type { SearchControllerState } from '../-search.use-controller';

import { sourceLabel, visibleSources } from '../-search.helpers';
import { SOURCE_LABELS } from '../-search.types';
import { CaretIcon, DiscIcon, FileIcon, FilmIcon } from './search-icons';
import styles from './search.module.css';

/** not metadata providers: each is its own tab */
const MODE_SOURCES = new Set(['youtube_videos', 'soulseek']);

export type SearchMode = 'catalog' | 'videos' | 'files';

export function modeOf(source: string): SearchMode {
  if (source === 'youtube_videos') return 'videos';
  if (source === 'soulseek') return 'files';
  return 'catalog';
}

/** the catalog (metadata) sources, in picker order */
export function catalogSources(state: SearchControllerState): string[] {
  return visibleSources(state.enabledExperimental).filter((s) => !MODE_SOURCES.has(s));
}

/**
 * What to search: Catalog (a metadata source), Videos, Files (basic search).
 *
 * #enh-source-row and the data-source on each tab are load-bearing. the global
 * download widget and the api monitor hand a query over by clicking
 * `#enh-source-row [data-source="soulseek"]`, and the page tour points here.
 * Catalog carries the catalog source it returns to, so a click on it lands
 * back where the user was.
 */
export function ModeTabs({
  state,
  catalogSource,
  onSelect,
}: {
  state: SearchControllerState;
  /** the metadata source Catalog returns to */
  catalogSource: string;
  onSelect: (source: string) => void;
}) {
  const mode = modeOf(state.activeSource);
  const tabs: { mode: SearchMode; source: string; label: string; icon: React.ReactNode }[] = [
    { mode: 'catalog', source: catalogSource, label: 'Catalog', icon: <DiscIcon /> },
    { mode: 'videos', source: 'youtube_videos', label: 'Videos', icon: <FilmIcon /> },
    { mode: 'files', source: 'soulseek', label: 'Files', icon: <FileIcon /> },
  ];
  return (
    <div className={styles.modes} id="enh-source-row" role="tablist" aria-label="What to search">
      {tabs.map((tab) => (
        <button
          key={tab.mode}
          type="button"
          role="tab"
          data-source={tab.source}
          aria-selected={mode === tab.mode}
          onClick={(event) => {
            // the page re-renders on select and detaches this node; stopping
            // here keeps the outside-click dismiss from also firing
            event.stopPropagation();
            onSelect(tab.source);
          }}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function SourceLogo({ source }: { source: string }) {
  const info = SOURCE_LABELS[source];
  return (
    <span className={styles.sourceLogo} aria-hidden="true">
      {info?.logo ? <img src={info.logo} alt="" loading="lazy" /> : (info?.icon ?? '♪')}
    </span>
  );
}

/**
 * The source pill inside the search field, and its menu.
 *
 * a source with no credentials opens Settings instead of becoming the active
 * source: an unconfigured source can only ever show an empty page and blame
 * the provider for it. a source the server answered from somewhere else says
 * so in the menu.
 */
export function SourcePicker({
  state,
  onSelect,
  onOpenSettings,
}: {
  state: SearchControllerState;
  onSelect: (source: string) => void;
  onOpenSettings: (source: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const mode = modeOf(state.activeSource);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (mode === 'videos') {
    return (
      <span className={styles.source} data-static>
        <SourceLogo source="youtube_videos" />
        YouTube
      </span>
    );
  }

  const current = state.activeSource;
  return (
    <div ref={wrapRef} style={{ display: 'contents' }}>
      <button
        type="button"
        className={styles.source}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Search with ${sourceLabel(current)}`}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <SourceLogo source={current} />
        {sourceLabel(current)}
        <CaretIcon className={styles.caret} />
      </button>
      {open ? (
        <div className={styles.menu} role="menu" aria-label="Search with">
          <div className={styles.menuHead}>Search with</div>
          {catalogSources(state).map((source) => {
            const configured = state.configuredSources[source] !== false;
            const served = state.fallbacks[source];
            const loading = state.loadingSources.has(source);
            const note = !configured
              ? 'Not set up'
              : served
                ? `Showing ${sourceLabel(served)}`
                : loading
                  ? 'Searching…'
                  : '';
            return (
              <button
                key={source}
                type="button"
                role="menuitemradio"
                aria-checked={source === current}
                className={styles.menuItem}
                data-source={source}
                data-unconfigured={configured ? undefined : true}
                title={
                  configured ? undefined : `${sourceLabel(source)} isn't set up. Opens Settings.`
                }
                onClick={(event) => {
                  event.stopPropagation();
                  setOpen(false);
                  if (!configured) {
                    onOpenSettings(source);
                    return;
                  }
                  onSelect(source);
                }}
              >
                <SourceLogo source={source} />
                <span>{sourceLabel(source)}</span>
                <span className={styles.menuNote}>{note}</span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
