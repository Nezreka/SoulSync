import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import {
  formatBytes,
  markDownloadRemoved,
  myDownloadsQuery,
  type DownloadItemType,
} from './-lib/downloads';
import { PageTitle } from './-ui/kit';

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

export function MyDownloadsPanel() {
  const qc = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<'all' | DownloadItemType>('all');
  const [statusFilter, setStatusFilter] = useState<'downloaded' | 'all' | 'removed'>('downloaded');
  const [search, setSearch] = useState('');

  const query = useQuery(myDownloadsQuery(statusFilter !== 'downloaded'));
  const data = query.data;
  const allRows = data?.downloads ?? [];

  const activeRows = useMemo(() => allRows.filter((r) => r.status !== 'removed'), [allRows]);
  const removedCount = useMemo(
    () => allRows.filter((r) => r.status === 'removed').length,
    [allRows],
  );
  const totalBytes = useMemo(
    () => activeRows.reduce((sum, r) => sum + (r.size_bytes ?? 0), 0),
    [activeRows],
  );
  const uniqueDevices = useMemo(
    () => new Set(activeRows.map((r) => r.device_name).filter(Boolean)).size,
    [activeRows],
  );

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allRows.filter((r) => {
      if (statusFilter === 'downloaded' && r.status === 'removed') return false;
      if (statusFilter === 'removed' && r.status !== 'removed') return false;
      if (typeFilter !== 'all' && r.item_type !== typeFilter) return false;
      if (q && !r.title.toLowerCase().includes(q) && !(r.subtitle ?? '').toLowerCase().includes(q) && !r.device_name.toLowerCase().includes(q)) {
        return false;
      }
      return true;
    });
  }, [allRows, statusFilter, typeFilter, search]);

  const removeMut = useMutation({
    mutationFn: (id: number) => markDownloadRemoved(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['player', 'downloads'] });
      void qc.invalidateQueries({ queryKey: ['player', 'admin', 'users'] });
    },
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {data && !data.allow_downloads && !data.is_admin ? (
        <div
          style={{
            padding: '14px 18px',
            borderRadius: 12,
            background: 'rgba(245,158,11,0.1)',
            border: '1px solid rgba(245,158,11,0.32)',
            color: '#fcd34d',
            fontSize: 13.5,
          }}
        >
          Offline downloads are currently disabled for your profile by an admin.
        </div>
      ) : null}

      {/* Best-in-class KPI Accent Strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(175px, 1fr))',
          gap: 12,
        }}
      >
        {[
          { label: 'On Devices', value: String(activeRows.length), accent: '#22c55e' },
          { label: 'Storage Used', value: formatBytes(totalBytes), accent: '#38bdf8' },
          { label: 'Devices', value: String(uniqueDevices), accent: '#818cf8' },
          {
            label: 'Removed History',
            value: statusFilter === 'downloaded' ? '—' : String(removedCount),
            accent: '#f59e0b',
          },
        ].map((kpi) => (
          <div
            key={kpi.label}
            style={{
              position: 'relative',
              overflow: 'hidden',
              padding: '15px 18px',
              borderRadius: 14,
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.08)',
            }}
          >
            <div
              style={{
                position: 'absolute',
                left: 0,
                top: 0,
                bottom: 0,
                width: 3,
                background: kpi.accent,
              }}
            />
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.07em',
                color: 'var(--text-muted, rgba(255,255,255,0.55))',
              }}
            >
              {kpi.label}
            </div>
            <div style={{ fontSize: 24, fontWeight: 800, marginTop: 4, letterSpacing: '-0.02em' }}>
              {kpi.value}
            </div>
          </div>
        ))}
      </div>

      {/* Compact Toolbar: Media Type Pills + Status Segmented Control + Search */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 10,
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 14px',
          borderRadius: 14,
          background: 'rgba(255,255,255,0.025)',
          border: '1px solid rgba(255,255,255,0.07)',
        }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {TYPE_FILTERS.map((f) => {
            const on = typeFilter === f.id;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => setTypeFilter(f.id)}
                style={{
                  padding: '6px 12px',
                  borderRadius: 999,
                  border: on
                    ? '1px solid rgba(var(--accent-rgb, 129,140,248), 0.65)'
                    : '1px solid rgba(255,255,255,0.1)',
                  background: on
                    ? 'rgba(var(--accent-rgb, 129,140,248), 0.22)'
                    : 'rgba(255,255,255,0.03)',
                  color: on ? '#fff' : 'rgba(255,255,255,0.72)',
                  fontSize: 12.5,
                  fontWeight: on ? 700 : 500,
                  cursor: 'pointer',
                }}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div
            style={{
              display: 'inline-flex',
              padding: 3,
              borderRadius: 999,
              background: 'rgba(0,0,0,0.28)',
              border: '1px solid rgba(255,255,255,0.09)',
            }}
          >
            {(
              [
                ['downloaded', 'On device'],
                ['all', 'All'],
                ['removed', 'Removed'],
              ] as const
            ).map(([st, lbl]) => {
              const on = statusFilter === st;
              return (
                <button
                  key={st}
                  type="button"
                  onClick={() => setStatusFilter(st)}
                  style={{
                    padding: '4px 11px',
                    borderRadius: 999,
                    border: 'none',
                    background: on ? 'rgba(255,255,255,0.14)' : 'transparent',
                    color: on ? '#fff' : 'rgba(255,255,255,0.62)',
                    fontSize: 12,
                    fontWeight: on ? 700 : 500,
                    cursor: 'pointer',
                  }}
                >
                  {lbl}
                </button>
              );
            })}
          </div>

          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter title or device…"
            style={{
              width: 195,
              padding: '6px 12px',
              borderRadius: 999,
              border: '1px solid rgba(255,255,255,0.12)',
              background: 'rgba(0,0,0,0.25)',
              color: '#fff',
              fontSize: 12.5,
              outline: 'none',
            }}
          />
        </div>
      </div>

      {/* Download Rows */}
      {query.isLoading ? (
        <div style={{ padding: '44px 0', textAlign: 'center', opacity: 0.65 }}>
          Loading downloads…
        </div>
      ) : rows.length === 0 ? (
        <div
          style={{
            padding: '52px 24px',
            textAlign: 'center',
            borderRadius: 16,
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(255,255,255,0.07)',
          }}
        >
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
            No downloads match this view
          </div>
          <div style={{ fontSize: 13, opacity: 0.65, maxWidth: 460, margin: '0 auto' }}>
            Use the Download button on any movie, TV episode, song, album, podcast, audiobook, or
            YouTube video to save it for offline playback.
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {rows.map((row) => {
            const removed = row.status === 'removed';
            return (
              <div
                key={row.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 14,
                  padding: '12px 16px',
                  borderRadius: 14,
                  background: 'rgba(255,255,255,0.035)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  opacity: removed ? 0.58 : 1,
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
                    color: 'rgba(255,255,255,0.55)',
                  }}
                >
                  {row.artwork_url ? (
                    <img
                      src={row.artwork_url}
                      alt=""
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                    />
                  ) : (
                    row.item_type.slice(0, 3)
                  )}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontWeight: 700,
                      fontSize: 14.5,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {row.title}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      opacity: 0.68,
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: '4px 8px',
                      marginTop: 2,
                    }}
                  >
                    {row.subtitle ? <span>{row.subtitle}</span> : null}
                    <span style={{ textTransform: 'capitalize' }}>{row.item_type}</span>
                    {row.quality ? <span>· {row.quality}</span> : null}
                    <span>· {row.device_name || 'Device'}</span>
                    {row.size_bytes ? <span>· {formatBytes(row.size_bytes)}</span> : null}
                  </div>
                </div>

                <span
                  style={{
                    padding: '4px 10px',
                    borderRadius: 999,
                    fontSize: 11.5,
                    fontWeight: 700,
                    background: removed ? 'rgba(245,158,11,0.14)' : 'rgba(34,197,94,0.14)',
                    color: removed ? '#fbbf24' : '#4ade80',
                    border: removed
                      ? '1px solid rgba(245,158,11,0.3)'
                      : '1px solid rgba(34,197,94,0.3)',
                  }}
                >
                  {removed ? 'Removed' : 'On device'}
                </span>

                {!removed ? (
                  <button
                    type="button"
                    disabled={removeMut.isPending}
                    onClick={() => removeMut.mutate(row.id)}
                    style={{
                      padding: '6px 12px',
                      borderRadius: 999,
                      border: '1px solid rgba(244,63,94,0.35)',
                      background: 'rgba(244,63,94,0.12)',
                      color: '#fda4af',
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    Remove
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PlayerDownloadsPage() {
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '12px 24px 64px' }}>
      <PageTitle
        title="Downloads"
        subtitle="Manage movies, TV episodes, music, YouTube, podcasts, and audiobooks saved across your devices."
      />
      <MyDownloadsPanel />
    </div>
  );
}
