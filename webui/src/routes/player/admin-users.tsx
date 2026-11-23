import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';

import {
  adminUsersQuery,
  formatBytes,
  setAdminUserDownloadPerm,
  type DownloadItemType,
} from './-lib/downloads';
import { PageTitle } from './-ui/kit';
import s from './-ui/player.module.css';

export const Route = createFileRoute('/player/admin-users')({
  component: AdminUsersDownloadsPage,
});

const TYPES: { id: '' | DownloadItemType; label: string }[] = [
  { id: '', label: 'All media' },
  { id: 'movie', label: 'Movies' },
  { id: 'episode', label: 'TV' },
  { id: 'track', label: 'Music' },
  { id: 'youtube', label: 'YouTube' },
  { id: 'podcast', label: 'Podcasts' },
  { id: 'audiobook', label: 'Audiobooks' },
];

function AdminUsersDownloadsPage() {
  const qc = useQueryClient();
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [typeFilter, setTypeFilter] = useState<'' | DownloadItemType>('');
  const [statusFilter, setStatusFilter] = useState<'' | 'downloaded' | 'removed'>('');

  const query = useQuery(
    adminUsersQuery({
      profileId: selectedUserId,
      itemType: typeFilter || null,
      status: statusFilter || null,
    }),
  );

  const togglePerm = useMutation({
    mutationFn: ({ profileId, allow }: { profileId: number; allow: boolean }) =>
      setAdminUserDownloadPerm(profileId, allow),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['player', 'admin', 'users'] });
    },
  });

  const data = query.data;
  const users = data?.users ?? [];
  const downloads = data?.downloads ?? [];

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto', padding: '16px 24px 64px' }}>
      <PageTitle
        title="Users & Downloads"
        subtitle="Admin control center — manage per-user download permissions and track offline downloads & deletions across devices."
      />

      {query.isError ? (
        <div
          style={{
            padding: 24,
            borderRadius: 14,
            background: 'rgba(239,68,68,0.12)',
            border: '1px solid rgba(239,68,68,0.35)',
          }}
        >
          Admin privileges are required to view this page.
        </div>
      ) : (
        <>
          {/* Summary Stats */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: 14,
              marginBottom: 24,
            }}
          >
            {[
              { label: 'Users', value: String(users.length) },
              { label: 'Active on devices', value: String(data?.totals.active_downloads ?? 0) },
              { label: 'Device storage', value: formatBytes(data?.totals.active_bytes ?? 0) },
              { label: 'Removed history', value: String(data?.totals.removed_downloads ?? 0) },
            ].map((stat) => (
              <div
                key={stat.label}
                style={{
                  padding: '16px 18px',
                  borderRadius: 14,
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.09)',
                }}
              >
                <div style={{ fontSize: 12, opacity: 0.65, marginBottom: 4 }}>{stat.label}</div>
                <div style={{ fontSize: 24, fontWeight: 800 }}>{stat.value}</div>
              </div>
            ))}
          </div>

          {/* Per-User Cards */}
          <h2 style={{ fontSize: 18, fontWeight: 800, marginBottom: 12 }}>Profiles</h2>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
              gap: 14,
              marginBottom: 28,
            }}
          >
            {users.map((u) => {
              const selected = selectedUserId === u.id;
              return (
                <div
                  key={u.id}
                  style={{
                    padding: 16,
                    borderRadius: 16,
                    background: selected ? 'rgba(139,92,246,0.14)' : 'rgba(255,255,255,0.04)',
                    border: selected
                      ? '1px solid rgba(139,92,246,0.6)'
                      : '1px solid rgba(255,255,255,0.09)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 10,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <div
                      style={{
                        width: 40,
                        height: 40,
                        borderRadius: '50%',
                        background: u.avatar_color || '#6366f1',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontWeight: 800,
                        fontSize: 16,
                      }}
                    >
                      {u.name.slice(0, 1).toUpperCase()}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 700, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>{u.name}</span>
                        {u.is_admin && (
                          <span
                            style={{
                              fontSize: 10,
                              fontWeight: 800,
                              padding: '2px 7px',
                              borderRadius: 999,
                              background: 'rgba(139,92,246,0.25)',
                              color: '#c4b5fd',
                            }}
                          >
                            ADMIN
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 12, opacity: 0.7 }}>
                        {u.active_downloads} on device · {formatBytes(u.active_bytes)} · {u.removed_downloads} removed
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    <button
                      type="button"
                      className={selected ? s.btnPrimary : s.btn}
                      onClick={() => setSelectedUserId(selected ? null : u.id)}
                    >
                      {selected ? 'Showing activity' : 'Filter activity'}
                    </button>
                    {!u.is_admin && (
                      <button
                        type="button"
                        className={u.allow_downloads ? s.btn : s.btnPrimary}
                        disabled={togglePerm.isPending}
                        onClick={() =>
                          togglePerm.mutate({ profileId: u.id, allow: !u.allow_downloads })
                        }
                      >
                        {u.allow_downloads ? 'Downloads: ON' : 'Downloads: OFF'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Audit Log Filters */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 14 }}>
            <h2 style={{ fontSize: 18, fontWeight: 800, margin: '0 12px 0 0' }}>Download Activity</h2>
            {TYPES.map((t) => (
              <button
                key={t.id || 'all'}
                type="button"
                className={typeFilter === t.id ? s.btnPrimary : s.btn}
                onClick={() => setTypeFilter(t.id)}
              >
                {t.label}
              </button>
            ))}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              {([
                ['', 'All states'],
                ['downloaded', 'On device'],
                ['removed', 'Removed'],
              ] as const).map(([st, lbl]) => (
                <button
                  key={st || 'all-st'}
                  type="button"
                  className={statusFilter === st ? s.btnPrimary : s.btn}
                  onClick={() => setStatusFilter(st)}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          {/* Activity List */}
          {downloads.length === 0 ? (
            <div
              style={{
                padding: '36px 24px',
                textAlign: 'center',
                borderRadius: 14,
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.08)',
                opacity: 0.75,
              }}
            >
              No download activity matches the current filter.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {downloads.map((d) => (
                <div
                  key={d.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 14,
                    padding: '12px 16px',
                    borderRadius: 12,
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 700, fontSize: 14.5 }}>
                      {d.title}{' '}
                      <span style={{ fontSize: 11, fontWeight: 700, opacity: 0.6 }}>
                        ({d.item_type.toUpperCase()})
                      </span>
                    </div>
                    <div style={{ fontSize: 12, opacity: 0.72 }}>
                      {d.profile_name} · {d.device_name} · {d.quality} · {formatBytes(d.size_bytes)} ·{' '}
                      {new Date(d.downloaded_at).toLocaleString()}
                      {d.removed_at ? ` · Removed ${new Date(d.removed_at).toLocaleString()}` : ''}
                    </div>
                  </div>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      padding: '4px 10px',
                      borderRadius: 999,
                      background:
                        d.status === 'downloaded'
                          ? 'rgba(16,185,129,0.16)'
                          : 'rgba(244,63,94,0.16)',
                      color: d.status === 'downloaded' ? '#6ee7b7' : '#fda4af',
                    }}
                  >
                    {d.status === 'downloaded' ? 'On device' : 'Removed'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
