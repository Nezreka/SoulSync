/**
 * comments on a title: what people on this server said about it, newest
 * first, and a box to add yours. everyone who can see the title reads
 * them; yours can be taken back. marks (moments) live on the seek bar
 * and are listed too, so the ones others shared can be jumped to.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { Note } from '../-player.api';

import { clock } from '../-engine/timeline';
import { remoteImage } from '../-lib/images';
import { noteEdits, notesQuery } from '../-player.api';
import s from './comments.module.css';

function ago(epoch: number): string {
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function Face({ note }: { note: Note }) {
  return (
    <span
      className={s.face}
      style={note.profile.avatar_color ? { background: note.profile.avatar_color } : undefined}
      aria-hidden="true"
    >
      {note.profile.avatar_url ? (
        <img src={remoteImage(note.profile.avatar_url) ?? undefined} alt="" />
      ) : (
        note.profile.name.charAt(0).toUpperCase()
      )}
    </span>
  );
}

export function Comments({
  itemType,
  itemId,
  title,
}: {
  itemType: string;
  itemId: string;
  title: string;
}) {
  const queryClient = useQueryClient();
  const notes = useQuery(notesQuery(itemType, itemId));
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const all = notes.data ?? [];
  const comments = all.filter((n) => n.at === null);
  const marks = all.filter((n) => n.at !== null && (n.shared || n.mine));
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['player', 'notes', itemType, itemId] });

  const post = async () => {
    if (!body.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await noteEdits.add(itemType, itemId, { body: body.trim() });
      setBody('');
      await refresh();
    } catch {
      setError("That didn't post. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={s.box} aria-label="Comments">
      <h2 className={s.title}>
        Comments
        {comments.length ? <span className={s.count}>{comments.length}</span> : null}
      </h2>
      <form
        className={s.compose}
        onSubmit={(e) => {
          e.preventDefault();
          void post();
        }}
      >
        <textarea
          className={s.input}
          placeholder={`Say something about ${title}`}
          value={body}
          maxLength={1000}
          rows={2}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void post();
            }
          }}
          aria-label="Your comment"
        />
        <div className={s.composeRow}>
          <span className={s.hint}>Everyone on this server who can see it reads it.</span>
          <button type="submit" className={s.post} disabled={!body.trim() || busy} data-nav>
            {busy ? 'Posting…' : 'Post'}
          </button>
        </div>
        {error ? <p className={s.error}>{error}</p> : null}
      </form>

      {notes.isLoading ? <p className={s.hint}>Loading…</p> : null}
      {!notes.isLoading && !comments.length ? (
        <p className={s.empty}>Nobody has said anything yet. Be the first.</p>
      ) : null}
      <ol className={s.list}>
        {comments.map((n) => (
          <li key={n.id} className={s.item}>
            <Face note={n} />
            <div className={s.itemBody}>
              <div className={s.itemHead}>
                <span className={s.name}>{n.mine ? 'You' : n.profile.name}</span>
                <span className={s.when}>
                  {ago(n.created_at)}
                  {n.edited_at ? ' · edited' : ''}
                </span>
                {n.mine ? (
                  <button
                    type="button"
                    className={s.remove}
                    onClick={() => void noteEdits.remove(n.id).then(refresh)}
                    data-nav
                  >
                    Take back
                  </button>
                ) : null}
              </div>
              <p className={s.text}>{n.body}</p>
            </div>
          </li>
        ))}
      </ol>

      {marks.length ? (
        <>
          <h3 className={s.sub}>Marked moments</h3>
          <ol className={s.marks}>
            {marks.map((n) => (
              <li key={n.id} className={s.mark}>
                <span className={s.markAt}>{clock(n.at!)}</span>
                <span className={s.markText}>
                  <span className={s.name}>{n.mine ? 'You' : n.profile.name}</span>
                  {n.body ? ` · ${n.body}` : ''}
                </span>
                {n.mine ? (
                  <button
                    type="button"
                    className={s.remove}
                    onClick={() => void noteEdits.remove(n.id).then(refresh)}
                    data-nav
                  >
                    Remove
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </section>
  );
}
