/**
 * the queue: what's playing and what's next. click a row to play it, move
 * it up or down (buttons, so a remote can), or take it out.
 */

import type { PlayerState } from '../../-engine/engine';

import { current, upcoming } from '../../-engine/queue';
import { CloseIcon } from './icons';
import s from './playback.module.css';
import { getEngine } from './use-player';

export function QueuePanel({ state, onClose }: { state: PlayerState; onClose: () => void }) {
  const engine = getEngine();
  const now = current(state.queue);
  const next = upcoming(state.queue);
  return (
    <div className={s.queue} role="dialog" aria-label="Queue">
      <div className={s.queueHead}>
        <h2 className={s.queueTitle}>Queue</h2>
        <button
          type="button"
          className={s.ctl}
          aria-label="Close the queue"
          onClick={onClose}
          data-nav
        >
          <CloseIcon />
        </button>
      </div>
      {now ? (
        <>
          <div className={s.queueLabel}>Now playing</div>
          <div className={s.queueRow} data-now="true">
            {now.artwork ? <img src={now.artwork} alt="" /> : <span className={s.queueThumb} />}
            <span className={s.queueText}>
              <span className={s.queueName}>{now.title}</span>
              {now.subtitle ? <span className={s.queueSub}>{now.subtitle}</span> : null}
            </span>
          </div>
        </>
      ) : null}
      <div className={s.queueLabel}>
        {next.length ? `Next up · ${next.length}` : 'Nothing after this'}
      </div>
      <ol className={s.queueList}>
        {next.map(({ item, index }) => (
          <li key={`${item.type}:${item.id}:${index}`} className={s.queueRow}>
            <button
              type="button"
              className={s.queuePlay}
              onClick={() => engine.jump(index)}
              data-nav
            >
              {item.artwork ? <img src={item.artwork} alt="" /> : <span className={s.queueThumb} />}
              <span className={s.queueText}>
                <span className={s.queueName}>{item.title}</span>
                {item.subtitle ? <span className={s.queueSub}>{item.subtitle}</span> : null}
              </span>
            </button>
            <span className={s.queueTools}>
              <button
                type="button"
                aria-label={`Move ${item.title} up`}
                disabled={index <= state.queue.index + 1}
                onClick={() => engine.move(index, index - 1)}
                data-nav
              >
                ↑
              </button>
              <button
                type="button"
                aria-label={`Move ${item.title} down`}
                disabled={index >= state.queue.items.length - 1}
                onClick={() => engine.move(index, index + 1)}
                data-nav
              >
                ↓
              </button>
              <button
                type="button"
                aria-label={`Remove ${item.title}`}
                onClick={() => engine.remove(index)}
                data-nav
              >
                ×
              </button>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
