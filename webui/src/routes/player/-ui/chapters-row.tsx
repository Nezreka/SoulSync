/**
 * "Chapters" on a movie's or an episode's page: a row of the file's
 * chapters, each with its picture, name, start and length. picking one plays
 * the video from there. a file with no chapters (or none known yet) has no
 * row at all.
 */

import { useQuery } from '@tanstack/react-query';

import { clock } from '../-engine/timeline';
import { chaptersQuery } from '../-player.api';
import { Section } from './detail';
import { PlayIcon } from './icons';
import { HScroll, styles as s } from './kit';
import { ChapterArt, span } from './playback/chapters-panel';
import p from './playback/playback.module.css';

export function ChaptersRow({
  itemType,
  itemId,
  part,
  duration,
  onPlayAt,
}: {
  itemType: 'movie' | 'episode';
  itemId: string;
  /** the version that plays (a media file id) */
  part?: number | string | null;
  /** the file's length, for the last chapter's */
  duration?: number | null;
  onPlayAt: (seconds: number) => void;
}) {
  const query = useQuery(chaptersQuery(itemType, itemId, part));
  const chapters = query.data ?? [];
  if (chapters.length < 2) return null;
  return (
    <Section title="Chapters">
      <HScroll className={s.inlineRail}>
        {chapters.map((c, i) => {
          const end = c.end ?? chapters[i + 1]?.start ?? duration ?? null;
          const length = end ? span(end - c.start) : '';
          return (
            <button
              key={c.start}
              type="button"
              className={s.videoTile}
              aria-label={`Play from ${c.title}, ${clock(c.start)}`}
              onClick={() => onPlayAt(c.start)}
              data-nav
            >
              <span className={s.art} data-wide="true">
                <ChapterArt chapter={c} index={i} eager={i < 6} />
                <span className={p.chapterTime}>{clock(c.start)}</span>
                <span className={s.videoPlay} aria-hidden="true">
                  <PlayIcon />
                </span>
              </span>
              <span className={s.cardTitle}>
                <span className={p.chapterNum}>{i + 1}</span>
                {c.title}
              </span>
              {length ? <span className={s.cardSub}>{length}</span> : null}
            </button>
          );
        })}
      </HScroll>
    </Section>
  );
}
