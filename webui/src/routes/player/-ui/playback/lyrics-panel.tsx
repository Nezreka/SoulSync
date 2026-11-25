/**
 * lyrics while it plays. synced ones follow the song: the line being sung
 * lit and kept in the middle, the ones sung dimmed, a click on any line
 * jumps there. scroll yourself and it stops following for a few seconds.
 * plain ones read as stanzas. where they came from is said at the bottom.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';

import { activeLine, parseLrc, stanzas } from '../../-lib/lyrics';
import { lyricsQuery } from '../../-player.api';
import s from './music-view.module.css';
import { getEngine } from './use-player';

const SOURCE: Record<string, string> = {
  sidecar: 'From the lyrics file beside this song',
  embedded: "From this song's own tags",
  lrclib: 'From LRCLIB',
};

/** you scrolled: following waits this long */
const HANDS_OFF_MS = 4000;

export function LyricsPanel({ state }: { state: PlayerState }) {
  const item = state.item;
  const trackId = item?.type === 'track' ? item.id : null;
  const lyrics = useQuery({ ...lyricsQuery(trackId ?? ''), enabled: !!trackId });
  const lines = useMemo(() => parseLrc(lyrics.data?.synced), [lyrics.data?.synced]);
  const plain = useMemo(
    () => (lines.length ? [] : stanzas(lyrics.data?.plain)),
    [lines.length, lyrics.data?.plain],
  );
  const at = activeLine(lines, state.position + 0.15);
  const box = useRef<HTMLDivElement>(null);
  const [handsOff, setHandsOff] = useState(0);
  const programmatic = useRef(false);

  useEffect(() => {
    if (at < 0 || Date.now() < handsOff) return;
    const el = box.current?.querySelector<HTMLElement>(`[data-line="${at}"]`);
    if (!el || !box.current) return;
    programmatic.current = true;
    box.current.scrollTo({
      top: el.offsetTop - box.current.clientHeight / 2 + el.clientHeight / 2,
      behavior: 'smooth',
    });
    const t = window.setTimeout(() => (programmatic.current = false), 600);
    return () => window.clearTimeout(t);
  }, [at, handsOff]);

  if (!trackId) return null;
  if (lyrics.isLoading)
    return (
      <div className={s.lyricsEmpty}>
        <span className={s.lyricsLoading} aria-hidden="true" />
        Finding the lyrics…
      </div>
    );
  const data = lyrics.data;
  if (!data?.success || (!lines.length && !plain.length))
    return (
      <div className={s.lyricsEmpty}>
        {data?.retryable
          ? 'The lyrics service is busy. They’ll be tried again next time.'
          : 'No lyrics for this song.'}
      </div>
    );

  return (
    <div
      ref={box}
      className={s.lyrics}
      data-synced={lines.length ? 'true' : undefined}
      onScroll={() => {
        if (!programmatic.current) setHandsOff(Date.now() + HANDS_OFF_MS);
      }}
    >
      {lines.length ? (
        <div className={s.lyricsLines}>
          {lines.map((line, i) => (
            <button
              key={`${line.time}-${i}`}
              type="button"
              className={s.lyricsLine}
              data-line={i}
              data-state={i === at ? 'now' : i < at ? 'sung' : undefined}
              data-gap={line.text ? undefined : 'true'}
              onClick={() => {
                setHandsOff(0);
                getEngine().seek(line.time);
              }}
              tabIndex={-1}
            >
              {line.text || '♪'}
            </button>
          ))}
        </div>
      ) : (
        <div className={s.lyricsPlain}>
          {plain.map((stanza, i) => (
            <p key={i}>
              {stanza.map((l, j) => (
                <span key={j}>{l}</span>
              ))}
            </p>
          ))}
        </div>
      )}
      {data.source ? <div className={s.lyricsSource}>{SOURCE[data.source] ?? ''}</div> : null}
    </div>
  );
}
