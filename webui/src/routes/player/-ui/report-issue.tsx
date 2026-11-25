import { useEffect, useState } from 'react';

import {
  reportPlayerIssue,
  submitVideoIssue,
  type PlayerIssueCategory,
  type PlayerIssueItemType,
  type VideoIssueEntityType,
} from '../-player.api';
import { styles as s } from './kit';
import { inPlayer } from './trailer';

export type ReportableEntityType = VideoIssueEntityType | PlayerIssueItemType;

interface IssueCategory {
  id: string;
  playerCat: PlayerIssueCategory;
  label: string;
  for: readonly ReportableEntityType[];
}

const ALL_VIDEO: readonly ReportableEntityType[] = [
  'movie',
  'episode',
  'youtube',
  'music_video',
  'commercial',
];
const ALL_AUDIO: readonly ReportableEntityType[] = [
  'track',
  'podcast',
  'podcast_episode',
  'audiobook',
];
const ALL_TYPES: readonly ReportableEntityType[] = [...ALL_VIDEO, ...ALL_AUDIO, 'show'];

export const VIDEO_ISSUE_CATEGORIES: readonly IssueCategory[] = [
  {
    id: 'playback_issue',
    playerCat: 'playback',
    label: "Won't play / corrupted / freezes",
    for: [...ALL_VIDEO, ...ALL_AUDIO],
  },
  {
    id: 'audio_issue',
    playerCat: 'audio',
    label: 'Audio problem (sync, silence, wrong track)',
    for: [...ALL_VIDEO, ...ALL_AUDIO],
  },
  {
    id: 'subtitle_issue',
    playerCat: 'subtitles',
    label: 'Subtitles missing or out of sync',
    for: ALL_VIDEO,
  },
  {
    id: 'bad_quality',
    playerCat: 'quality',
    label: 'Poor video or audio quality',
    for: [...ALL_VIDEO, ...ALL_AUDIO],
  },
  {
    id: 'missing_content',
    playerCat: 'other',
    label: 'Missing episodes or seasons',
    for: ['show'],
  },
  {
    id: 'wrong_match',
    playerCat: 'metadata',
    label: 'Wrong media / mismatched file',
    for: ALL_TYPES,
  },
  {
    id: 'wrong_metadata',
    playerCat: 'metadata',
    label: 'Wrong details or artwork',
    for: ALL_TYPES,
  },
  {
    id: 'other',
    playerCat: 'other',
    label: 'Something else',
    for: ALL_TYPES,
  },
];

export function ReportIssueButton({
  entityType,
  entityId,
  title,
  subtitle,
  positionSec = 0,
  compact = false,
  iconOnly = false,
  className,
}: {
  entityType: ReportableEntityType;
  entityId: number | string;
  title: string;
  subtitle?: string;
  positionSec?: number;
  compact?: boolean;
  iconOnly?: boolean;
  className?: string;
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
      const picked = categories.find((c) => c.id === category);
      if (entityType === 'show') {
        await submitVideoIssue({
          entity_type: 'show',
          entity_id: Number(entityId),
          category,
          title,
          description: description.trim() || undefined,
        });
      } else {
        await reportPlayerIssue({
          item_type: entityType,
          item_id: String(entityId),
          title,
          subtitle: subtitle || undefined,
          category: picked?.playerCat ?? 'other',
          note: description.trim() || undefined,
          position_sec: Math.max(0, Math.floor(positionSec || 0)),
        });
        if (entityType === 'movie' || entityType === 'episode') {
          void submitVideoIssue({
            entity_type: entityType,
            entity_id: Number(entityId),
            category,
            title,
            description: description.trim() || undefined,
          }).catch(() => {});
        }
      }
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
        className={className ?? s.btn}
        title="Report an issue"
        aria-label={`Report an issue with ${title}`}
        onClick={(e) => {
          e.stopPropagation();
          handleOpen();
        }}
        data-nav
        style={
          iconOnly
            ? { height: 32, width: 32, padding: 0, justifyContent: 'center', borderRadius: 999 }
            : compact
              ? { height: 36, padding: '0 14px', fontSize: '0.82rem', gap: 6 }
              : { gap: 7 }
        }
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
          <line x1="4" y1="22" x2="4" y2="15" />
        </svg>
        {iconOnly ? null : 'Report issue'}
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
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'flex-start',
                    gap: 12,
                  }}
                >
                  <div>
                    <h3 style={{ margin: 0, fontSize: '1.12rem', fontWeight: 700 }}>
                      Report an issue
                    </h3>
                    <p
                      style={{
                        margin: '4px 0 0',
                        fontSize: '0.88rem',
                        color: 'var(--pl-text-2, #a8b0c0)',
                      }}
                    >
                      {title}
                    </p>
                  </div>
                  <button
                    type="button"
                    className={s.btn}
                    onClick={() => setOpen(false)}
                    disabled={submitting}
                    style={{ height: 32, padding: '0 12px', fontSize: '0.82rem' }}
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
                      <span
                        style={{
                          fontSize: '0.8rem',
                          fontWeight: 600,
                          color: 'var(--pl-text-2, #a8b0c0)',
                          textTransform: 'uppercase',
                          letterSpacing: '0.06em',
                        }}
                      >
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
                        style={{
                          fontSize: '0.8rem',
                          fontWeight: 600,
                          color: 'var(--pl-text-2, #a8b0c0)',
                          textTransform: 'uppercase',
                          letterSpacing: '0.06em',
                        }}
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
                        className={s.btn}
                        onClick={() => setOpen(false)}
                        disabled={submitting}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className={s.btnPrimary}
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
