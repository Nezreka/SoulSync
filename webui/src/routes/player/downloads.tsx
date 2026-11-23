import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';

import {
  formatBytes,
  markDownloadRemoved,
  myDownloadsQuery,
  type DownloadItemType,
} from './-lib/downloads';
import { PageTitle } from './-ui/kit';
import s from './-ui/player.module.css';

export const Route = createFileRoute('/player/downloads')({
  component: PlayerDownloadsPage,
});

const TYPE_FILTERS: { id: 'all' | DownloadItemType; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'movie', label: 'Movies' },
  { id: 'episode', label: 'TV' },
  { id: 'track', label: 'Music' },
  { id: 'youtube', label: 'YouTube' },
  { id: 'podcast', label: 'Podcasts' },
  { id: 'audiobook', label: 'Audiobooks' },
];

function PlayerDownloadsPage() {
  const qc = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<'all' | DownloadItemType>('all');
  const [showRemoved, setShowRemoved] = useState(false);

  const query = useQuery(myDownloadsQuery(showRemoved));
  const data = query.data;
  const rows = (data?.downloads ?? []).filter(
    (r) => typeFilter === 'all' || r.item_type === typeFilter,
  );

  const removeMut = useMutation({
    mutationFn: (id: number) => markDownloadRemoved(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['player', 'downloads'] });
    },
  });

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 24px 64px' }}>
      <PageTitle
        title="Downloads"
        subtitle={
          data && !data.allow_downloads && !data.is_admin
            ? 'Downloads are currently disabled for your profile by an admin.'
            : 'Movies, TV episodes, music, YouTube, podcasts, and audiobooks saved across your devices.'
        }
      />

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 20 }}>
        {TYPE_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={typeFilter === f.id ? s.btnPrimary : s.btn}
            onClick={() => setTypeFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
        <button
          type="button"
          className={showRemoved ? s.btnPrimary : s.btn}
          style={{ marginLeft: 'auto' }}
          onClick={() => setShowRemoved((v) => !v)}
        >
          {showRemoved ? 'Showing removed' : 'Show removed history'}
        </button>
      </div>

      {query.isLoading ? (
        <div style={{ padding: '48px 0', textAlign: 'center', opacity: 0.7 }}>Loading downloads…</div>
      ) : rows.length === 0 ? (
        <div
          style={{
            padding: '48px 24px',
            textAlign: 'center',
            borderRadius: 16,
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>No downloads yet</div>
          <div style={{ fontSize: 13, opacity: 0.7 }}>
            Tap Download on any movie, episode, song, album, podcast, audiobook, or YouTube video to save it for offline playback.
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {rows.map((row) => (
            <div
              key={row.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 14,
                padding: '12px 16px',
                borderRadius: 14,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
                opacity: row.status === 'removed' ? 0.55 : 1,
              }}
            >
              <div
                style={{
                  width: 48,
                  height: 48,
                  borderRadius: 10,
                  overflow: 'hidden',
                  background: 'rgba(255,255,255,0.07)',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  fontWeight: 700,
                  textTransform: 'uppercase',
                }}
              >
                {row.artwork ? (
                  <img
                    src={row.artwork}
                    alt=""
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                ) : (
                  row.item_type
                )}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {row.title}
                </div>
                <div style={{ fontSize: 12.5, opacity: 0.72 }}>
                  {[
                    row.item_type.toUpperCase(),
                    row.subtitle,
                    row.device_name,
                    formatBytes(row.size_bytes),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </div>
              </div>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  padding: '4px 10px',
                  borderRadius: 999,
                  background:
                    row.status === 'downloaded'
                      ? 'rgba(16,185,129,0.16)'
                      : 'rgba(148,163,184,0.16)',
                  color: row.status === 'downloaded' ? '#6ee7b7' : '#cbd5e1',
                }}
              >
                {row.status === 'downloaded' ? 'On device' : 'Removed'}
              </span>
              {row.status === 'downloaded' && (
                <button
                  type="button"
                  className={s.btn}
                  disabled={removeMut.isPending}
                  onClick={() => removeMut.mutate(row.id)}
                >
                  Mark removed
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
