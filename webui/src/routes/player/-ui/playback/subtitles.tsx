/**
 * subtitles, drawn by the player over the video: the cues at the player's
 * own position (so they follow seeks and converted streams), in the size
 * and box this viewer picked. they rise above the controls when those show.
 */

import type { PlayerState } from '../../-engine/engine';

import { cuesAt } from '../../-engine/vtt';
import s from './playback.module.css';
import { useSubtitleStyle } from './use-subtitle-style';

export function SubtitleOverlay({ state, raised }: { state: PlayerState; raised: boolean }) {
  const style = useSubtitleStyle();
  if (!state.subtitle || !state.cues.length) return null;
  const showing = cuesAt(state.cues, state.position);
  if (!showing.length) return null;
  return (
    <div
      className={s.subs}
      data-size={style.size}
      data-box={style.box ? 'true' : undefined}
      data-raised={raised ? 'true' : undefined}
      aria-live="off"
    >
      {showing.map((c) => (
        <span key={`${c.start}-${c.text}`} className={s.subLine}>
          {c.text}
        </span>
      ))}
    </div>
  );
}
