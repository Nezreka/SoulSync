import { useEffect, useState } from 'react';

import { submitVideoIssue, type VideoIssueEntityType } from '../-player.api';
import { styles as s } from './kit';
import { inPlayer } from './trailer';

interface IssueCategory {
  id: string;
  label: string;
  for: readonly VideoIssueEntityType[];
}

export const VIDEO_ISSUE_CATEGORIES: readonly IssueCategory[] = [
  { id: 'playback_issue', label: "Won't play / buffers / stutters", for: ['movie', 'episode'] },
  { id: 'audio_issue', label: 'Audio problem (sync, language, missing)', for: ['movie', 'episode'] },
  { id: 'subtitle_issue', label: 'Subtitle problem', for: ['movie', 'episode'] },
  { id: 'bad_quality', label: 'Poor video quality', for: ['movie', 'episode'] },
  { id: 'missing_content', label: 'Missing episodes or seasons', for: ['show'] },
  { id: 'wrong_match', label: 'Matched to the wrong title', for: ['movie', 'show'] },
  { id: 'wrong_metadata', label: 'Wrong details (year, summary, genres…)', for: ['movie', 'show', 'episode'] },
  { id: 'wrong_poster', label: 'Wrong poster / artwork', for: ['movie', 'show'] },
  { id: 'duplicate', label: 'Duplicate copies', for: ['movie', 'show'] },
  { id: 'other', label: 'Something else', for: ['movie', 'show', 'episode'] },
];

export function ReportIssueButton({
  entityType,
  entityId,
  title,
  compact = false,
}: {
  entityType: VideoIssueEntityType;
  entityId: number;
  title: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const categories = VIDEO_ISSUE_CATEGORIES.filter((c) => c.for.includes(entityType));
  const [category, setCategory] = useState(categories[0]?.id ?? 'other');
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open]);

  const handleOpen = () => {
    setCategory(categories[0]?.id ?? 'other');
    setDescription('');
    setError(null);
    setDone(false);
    setOpen(true);
  };

  const handleSubmit = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitVideoIssue({
        entity_type: entityType,
        entity_id: entityId,
        category,
        title,
        description: description.trim() || undefined,
      });
      setDone(true);
      window.setTimeout(() => setOpen(false), 1100);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not submit issue');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className={s.ghostBtn}
        onClick={(e) => {
          e.stopPropagation();
          handleOpen();
        }}
        data-nav
        style={
          compact
            ? { padding: '7px 12px', fontSize: '0.84rem', minHeight: 34 }
            : undefined
        }
      >
        Report issue
      </button>
      {open
        ? inPlayer(
            <div
              className={s.trailerModal}
              role="dialog"
              aria-modal="true"
              aria-label={`Report an issue with ${title}`}
              onClick={(e) => {
                if (e.target === e.currentTarget && !submitting) setOpen(false);
              }}
            >
              <div
                style={{
                  width: 'min(520px, calc(100vw - 32px))',
                  background: 'var(--pl-surface, #161822)',
                  border: '1px solid var(--pl-line, rgba(255,255,255,0.12))',
                  borderRadius: 18,
                  padding: '22px 24px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 16,
                  boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
                  color: 'var(--pl-text, #f5f7fb)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                  <div>
                    <h3 style={{ margin: 0, fontSize: '1.12rem', fontWeight: 700 }}>Report an issue</h3>
                    <p style={{ margin: '4px 0 0', fontSize: '0.88rem', color: 'var(--pl-text-2, #a8b0c0)' }}>
                      {title}
                    </p>
                  </div>
                  <button
                    type="button"
                    className={s.ghostBtn}
                    onClick={() => setOpen(false)}
                    disabled={submitting}
                    style={{ padding: '4px 10px', minHeight: 30, fontSize: '0.82rem' }}
                  >
                    Close
                  </button>
                </div>

                {done ? (
                  <div
                    style={{
                      padding: '20px 16px',
                      borderRadius: 12,
                      background: 'rgba(46, 204, 113, 0.14)',
                      border: '1px solid rgba(46, 204, 113, 0.35)',
                      color: '#8ff0b3',
                      fontWeight: 600,
                      textAlign: 'center',
                    }}
                  >
                    Issue submitted — thanks for flagging it!
                  </div>
                ) : (
                  <>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--pl-text-2, #a8b0c0)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        What&apos;s wrong?
                      </span>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {categories.map((c) => {
                          const active = c.id === category;
                          return (
                            <button
                              key={c.id}
                              type="button"
                              onClick={() => setCategory(c.id)}
                              style={{
                                padding: '7px 12px',
                                borderRadius: 999,
                                fontSize: '0.84rem',
                                fontWeight: active ? 600 : 500,
                                cursor: 'pointer',
                                border: active
                                  ? '1px solid var(--pl-accent, #7c5cff)'
                                  : '1px solid var(--pl-line, rgba(255,255,255,0.14))',
                                background: active
                                  ? 'rgba(124, 92, 255, 0.22)'
                                  : 'rgba(255,255,255,0.04)',
                                color: active ? '#fff' : 'var(--pl-text-2, #c5ccda)',
                              }}
                            >
                              {c.label}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <label
                        htmlFor="issue-desc"
                        style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--pl-text-2, #a8b0c0)', textTransform: 'uppercase', letterSpacing: '0.06em' }}
                      >
                        Details (optional)
                      </label>
                      <textarea
                        id="issue-desc"
                        rows={3}
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Add timestamps, language, or anything that helps us fix it…"
                        style={{
                          width: '100%',
                          boxSizing: 'border-box',
                          borderRadius: 10,
                          border: '1px solid var(--pl-line, rgba(255,255,255,0.14))',
                          background: 'rgba(0,0,0,0.28)',
                          color: 'inherit',
                          padding: '10px 12px',
                          fontSize: '0.9rem',
                          resize: 'vertical',
                          fontFamily: 'inherit',
                        }}
                      />
                    </div>

                    {error ? (
                      <div style={{ fontSize: '0.86rem', color: '#ff7b7b' }}>{error}</div>
                    ) : null}

                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                      <button
                        type="button"
                        className={s.ghostBtn}
                        onClick={() => setOpen(false)}
                        disabled={submitting}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className={s.playBtn}
                        onClick={() => void handleSubmit()}
                        disabled={submitting}
                      >
                        {submitting ? 'Submitting…' : 'Submit issue'}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>,
          )
        : null}
    </>
  );
}
