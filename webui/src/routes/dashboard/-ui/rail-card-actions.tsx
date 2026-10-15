/**
 * The hover buttons on a dashboard rail card: queue, play, add to playlist.
 *
 * Play is the one big accent disc in the middle, the other two are small
 * glass circles beside it. They sit on the cover, above the title, and every
 * one stops the click: the card underneath plays on its own tap, and a button
 * press must not also fire that.
 */

import { useState } from 'react';

export interface RailCardActionsProps {
  /** what the buttons act on, for their labels: an album or a song title. */
  label: string;
  onPlay: () => void | Promise<void>;
  onQueue: () => void | Promise<void>;
  onAddToPlaylist: (anchor: HTMLElement) => void | Promise<void>;
}

const ICON = { viewBox: '0 0 24 24', 'aria-hidden': true, fill: 'currentColor' } as const;

export function RailCardActions({ label, onPlay, onQueue, onAddToPlaylist }: RailCardActionsProps) {
  // an album has to be fetched before it plays; a second tap meanwhile would
  // start it twice
  // per button: queueing while the play is still loading is fine
  const [busy, setBusy] = useState({ play: false, queue: false });

  const run = (which: 'play' | 'queue', action: () => void | Promise<void>) => async () => {
    if (busy[which]) return;
    setBusy((b) => ({ ...b, [which]: true }));
    try {
      await action();
    } finally {
      setBusy((b) => ({ ...b, [which]: false }));
    }
  };

  return (
    <div className="dash-card-actions" onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="dash-card-action"
        title="Add to queue"
        aria-label={`Add ${label} to the queue`}
        disabled={busy.queue}
        aria-busy={busy.queue || undefined}
        onClick={run('queue', onQueue)}
      >
        <svg {...ICON} width={16} height={16}>
          <path d="M4 10h12v2H4zm0-4h12v2H4zm0 8h8v2H4zm10 0v6l5-3z" />
        </svg>
      </button>
      <button
        type="button"
        className="dash-card-action dash-card-action--play"
        title="Play"
        aria-label={`Play ${label}`}
        disabled={busy.play}
        aria-busy={busy.play || undefined}
        onClick={run('play', onPlay)}
      >
        <svg {...ICON} width={18} height={18}>
          <path d="M8 5.5v13l11-6.5z" />
        </svg>
      </button>
      <button
        type="button"
        className="dash-card-action"
        title="Add to playlist"
        aria-label={`Add ${label} to a playlist`}
        onClick={(event) => void onAddToPlaylist(event.currentTarget)}
      >
        <svg {...ICON} width={16} height={16}>
          <path d="M14 10H3v2h11v-2zm0-4H3v2h11V6zm4 8v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zM3 16h7v-2H3v2z" />
        </svg>
      </button>
    </div>
  );
}
