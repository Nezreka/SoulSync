/**
 * The shared playlist card — the archetype every vertical renders
 * (createTidalCard and its clones; here transcribed from the LB/Last.fm
 * variant, sync-listenbrainz.js 121-138): icon, name, count + owner + phase
 * line, a progress line, and the phase-driven action button. Phase text,
 * color and button label come from the shared pure maps; the vanilla's 500ms
 * card refresh loop dies here — the card re-renders whenever its state does.
 *
 * The URL-import variants add a type badge (spotify-public/itunes cards,
 * 6740/7766) and richer progress content (the check-note spans), and YouTube
 * overrides the button map — all provided via props by the tab that knows.
 */

import type { ReactNode } from 'react';

import { actionButtonText, phaseColor, phaseText } from '../-sync.core';

export interface SourceCardProps {
  id?: string;
  cardClassName: string;
  icon: string;
  /** Extra class on the icon element (YouTube's 'youtube-icon', 8893). */
  iconClassName?: string;
  name: string;
  countText: string;
  /** Omitted entirely for the URL-import cards (their info row has no owner span). */
  owner?: string;
  phase: string;
  /** The type badge the spotify-public/itunes cards render before the count. */
  typeBadge?: { text: string; color: string };
  /** Overrides the phase line (the SSD staleness line, e.g.). */
  statusText?: string;
  statusColor?: string;
  /** Overrides the phase-derived button label (SSD's 'Refresh & Mirror'). */
  buttonText?: string;
  buttonDisabled?: boolean;
  /** The ♪/✓/✗ line; NULL hides it (fresh cards) — '' renders it visible
   * and empty, the vanilla's unhidden-while-refreshing state. */
  progressLine?: ReactNode | null;
  onClick: () => void;
}

export function SourceCard({
  id,
  cardClassName,
  icon,
  iconClassName,
  name,
  countText,
  owner,
  phase,
  typeBadge,
  statusText,
  statusColor,
  buttonText,
  buttonDisabled,
  progressLine,
  onClick,
}: SourceCardProps) {
  return (
    <div className={`youtube-playlist-card ${cardClassName}`} id={id} onClick={onClick}>
      <div className={`playlist-card-icon ${iconClassName ?? ''}`}>{icon}</div>
      <div className="playlist-card-content">
        <div className="playlist-card-name">{name}</div>
        <div className="playlist-card-info">
          {typeBadge && (
            <span
              className="playlist-card-type-badge"
              style={{
                color: typeBadge.color,
                fontSize: '0.75em',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              {typeBadge.text}
            </span>
          )}
          <span className="playlist-card-track-count">{countText}</span>
          {owner !== undefined && <span className="playlist-card-owner">{owner}</span>}
          <span
            className="playlist-card-phase-text"
            style={{ color: statusColor ?? phaseColor(phase) }}
          >
            {statusText ?? phaseText(phase)}
          </span>
        </div>
      </div>
      <div className={`playlist-card-progress ${progressLine != null ? '' : 'hidden'}`}>
        {progressLine ?? ''}
      </div>
      <button type="button" className="playlist-card-action-btn" disabled={buttonDisabled}>
        {buttonText ?? actionButtonText(phase)}
      </button>
    </div>
  );
}
