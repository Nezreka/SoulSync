import { ActionMenu } from './action-menu';
import { ExternalIcon, SearchIcon, RefreshIcon } from './lib-icons';

export interface SourceEntry {
  service: string;
  /** short label the chip helpers hand over ('MB', 'Spotify'); the full name comes from SERVICE_NAMES. */
  label: string;
  /** raw match status: matched / not_found / pending / anything else. */
  status: string;
  /** tooltip text from the chip helper (last attempt, click hint). */
  title: string;
  /** external page for a matched id, when the service has one. */
  url?: string | null;
}

export const SERVICE_NAMES: Record<string, string> = {
  spotify: 'Spotify',
  musicbrainz: 'MusicBrainz',
  deezer: 'Deezer',
  discogs: 'Discogs',
  audiodb: 'AudioDB',
  itunes: 'iTunes',
  lastfm: 'Last.fm',
  genius: 'Genius',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  amazon: 'Amazon',
  bandcamp: 'Bandcamp',
  jiosaavn: 'JioSaavn',
};

/** two letters per service, the same in every row so the eye learns them once. */
export const SERVICE_MONOGRAMS: Record<string, string> = {
  spotify: 'SP',
  musicbrainz: 'MB',
  deezer: 'DZ',
  discogs: 'DC',
  audiodb: 'AD',
  itunes: 'IT',
  lastfm: 'LF',
  genius: 'GE',
  tidal: 'TD',
  qobuz: 'QB',
  amazon: 'AZ',
  bandcamp: 'BC',
  jiosaavn: 'JS',
};

export function sourceState(status: string | undefined): 'matched' | 'not-found' | 'pending' {
  return status === 'matched' ? 'matched' : status === 'not_found' ? 'not-found' : 'pending';
}

export function serviceName(service: string, fallback = ''): string {
  return SERVICE_NAMES[service] ?? fallback ?? service;
}

export function monogram(service: string, fallback: string): string {
  return SERVICE_MONOGRAMS[service] ?? fallback.slice(0, 2).toUpperCase();
}

/** "3 of 9 matched" for the row's trailing summary. */
export function matchedSummary(entries: { status: string }[]): string {
  const matched = entries.filter((e) => sourceState(e.status) === 'matched').length;
  return `${matched} of ${entries.length} matched`;
}

interface Props {
  entries: SourceEntry[];
  /** admin only; without it the dots are read-only status. */
  onRematch?: (service: string) => void;
  /** hide the leading "Sources" word (the expanded album header has its own label column). */
  label?: string | null;
  className?: string;
}

/**
 * the source health row: one small monogram per metadata service, coloured
 * by match state, with the external link and the rematch action behind a
 * click instead of two rows of "Service: status" chips.
 *
 * keeps the enhanced-match-chip class on each dot so the vanilla's chip
 * styling hooks and the existing tests still find them.
 */
export function SourceHealth({ entries, onRematch, label = 'Sources', className }: Props) {
  if (entries.length === 0) return null;
  return (
    <div className={`lib-sources${className ? ` ${className}` : ''}`}>
      {label ? <span className="lib-sources-label">{label}</span> : null}
      <div className="lib-sources-dots" role="list">
        {entries.map((entry) => {
          const state = sourceState(entry.status);
          const name = serviceName(entry.service, entry.label);
          const items = [] as {
            key: string;
            label: string;
            icon: React.ReactNode;
            onSelect: () => void;
          }[];
          if (entry.url) {
            const url = entry.url;
            items.push({
              key: 'open',
              label: `Open on ${name}`,
              icon: <ExternalIcon />,
              onSelect: () => window.open(url, '_blank', 'noopener,noreferrer'),
            });
          }
          if (onRematch) {
            items.push({
              key: 'match',
              label: state === 'matched' ? `Rematch on ${name}…` : `Find on ${name}…`,
              icon: state === 'matched' ? <RefreshIcon /> : <SearchIcon />,
              onSelect: () => onRematch(entry.service),
            });
          }
          const stateWord =
            state === 'matched' ? 'matched' : state === 'not-found' ? 'no match' : 'not tried';
          const tip = `${name} · ${stateWord}${entry.title ? ` · ${entry.title}` : ''}`;
          const dot = (props: Record<string, unknown> = {}) => (
            <button
              type="button"
              className={`lib-source enhanced-match-chip ${state}${items.length ? ' clickable' : ''}`}
              title={tip}
              aria-label={tip}
              data-service={entry.service}
              role="listitem"
              {...props}
            >
              <span className="lib-source-mono">{monogram(entry.service, entry.label)}</span>
              <span className="lib-source-state" aria-hidden="true" />
            </button>
          );
          if (!items.length) {
            return (
              <span key={entry.service}>
                {dot({ onClick: (e: React.MouseEvent) => e.stopPropagation() })}
              </span>
            );
          }
          return (
            <ActionMenu
              key={entry.service}
              align="left"
              heading={
                <>
                  <strong>{name}</strong>
                  <span className={`lib-menu-state ${state}`}>{stateWord}</span>
                  {entry.title.startsWith('Last:') ? (
                    <span className="lib-menu-sub">{entry.title.split(' · ')[0]}</span>
                  ) : null}
                </>
              }
              items={items}
              trigger={(t) => dot(t)}
            />
          );
        })}
      </div>
      <span className="lib-sources-summary">{matchedSummary(entries)}</span>
    </div>
  );
}

interface MeterProps {
  entries: { service: string; label: string; matched: boolean; title: string }[];
  /** admin only; opens the matcher for the picked service. */
  onMatch?: (service: string) => void;
}

/**
 * the track-row version: one small "6/9" pill with a fill bar instead of nine
 * chips wrapping onto three lines. the per-service list, with its rematch
 * action, sits behind a click; each row in that menu keeps the old
 * enhanced-track-match-chip class as a hook.
 */
export function SourceMeter({ entries, onMatch }: MeterProps) {
  const matched = entries.filter((e) => e.matched).length;
  const ratio = entries.length ? matched / entries.length : 0;
  const tone = ratio >= 0.66 ? 'good' : ratio > 0 ? 'partial' : 'none';
  const summary = `${matched} of ${entries.length} sources matched`;
  const pill = (props: Record<string, unknown> = {}) => (
    <button
      type="button"
      className={`lib-meter ${tone}${onMatch ? ' clickable' : ''}`}
      title={onMatch ? `${summary} · click to see or rematch` : summary}
      aria-label={summary}
      {...props}
    >
      <span className="lib-meter-bar" aria-hidden="true">
        <span className="lib-meter-fill" style={{ width: `${Math.round(ratio * 100)}%` }} />
      </span>
      <span className="lib-meter-text">
        {matched}/{entries.length}
      </span>
    </button>
  );
  if (!onMatch) {
    return (
      <div className="enhanced-track-match-cell">
        {pill({ onClick: (e: React.MouseEvent) => e.stopPropagation() })}
      </div>
    );
  }
  return (
    <div className="enhanced-track-match-cell">
      <ActionMenu
        align="right"
        className="lib-menu-sources"
        heading={<strong>{summary}</strong>}
        items={entries.map((entry) => ({
          key: entry.service,
          className: `enhanced-track-match-chip ${entry.matched ? 'matched' : 'not-found'}`,
          data: { service: entry.service },
          icon: (
            <span
              className={`lib-menu-dot ${entry.matched ? 'matched' : 'not-found'}`}
              aria-hidden="true"
            />
          ),
          label: (
            <>
              {serviceName(entry.service, entry.label)}
              <span className="lib-menu-hint">{entry.matched ? 'rematch' : 'find match'}</span>
            </>
          ),
          title: entry.title,
          onSelect: () => onMatch(entry.service),
        }))}
        trigger={(t) => pill(t)}
      />
    </div>
  );
}
