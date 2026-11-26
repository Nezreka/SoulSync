import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import {
  adminUsersQuery,
  formatBytes,
  setAdminUserDownloadPerm,
  type DownloadItemType,
} from './-lib/downloads';
import { MyDownloadsPanel } from './-ui/download-history';
import { PeopleIcon } from './-ui/icons';
import { PageTitle } from './-ui/kit';
import { notifyError } from './-ui/notice';
import s from './-ui/social.module.css';

export const Route = createFileRoute('/player/admin-users')({
  component: AdminUsersDownloadsPage,
});

const MEDIA_TYPES: { id: '' | DownloadItemType; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'movie', label: 'Movies' },
  { id: 'episode', label: 'TV' },
  { id: 'track', label: 'Music' },
  { id: 'youtube', label: 'YouTube' },
  { id: 'podcast', label: 'Podcasts' },
  { id: 'audiobook', label: 'Audiobooks' },
];

function ShieldIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function DownloadCloudIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function AdminUsersDownloadsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<'admin' | 'mine'>('admin');
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [typeFilter, setTypeFilter] = useState<'' | DownloadItemType>('');
  const [statusFilter, setStatusFilter] = useState<'' | 'downloaded' | 'removed'>('');
  const [search, setSearch] = useState('');

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
    onError: notifyError("Couldn't change their downloads"),
  });

  const data = query.data;
  const users = data?.users ?? [];
  const downloads = useMemo(() => {
    const raw = data?.downloads ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return raw;
    return raw.filter(
      (d) =>
        d.title.toLowerCase().includes(q) ||
        (d.profile_name ?? '').toLowerCase().includes(q) ||
        d.device_name.toLowerCase().includes(q),
    );
  }, [data?.downloads, search]);

  const selectedUser = users.find((u) => u.id === selectedUserId) ?? null;

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '12px 24px 64px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
          marginBottom: 16,
        }}
      >
        <div>
          <PageTitle>Users & Downloads</PageTitle>
          <p style={{ margin: '4px 0 0', fontSize: 14, lineHeight: 1.45, opacity: 0.55 }}>
            Manage your offline downloads, per-user download permissions, and device activity.
          </p>
        </div>
        <div
          style={{
            display: 'inline-flex',
            padding: 4,
            borderRadius: 999,
            background: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          {(
            [
              ['admin', 'Users & Devices'],
              ['mine', 'My Downloads'],
            ] as const
          ).map(([id, label]) => {
            const on = tab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                style={{
                  padding: '7px 16px',
                  borderRadius: 999,
                  border: 'none',
                  background: on ? 'rgba(var(--accent-rgb, 129,140,248), 0.28)' : 'transparent',
                  color: on ? '#fff' : 'rgba(255,255,255,0.68)',
                  fontSize: 13,
                  fontWeight: on ? 700 : 500,
                  cursor: 'pointer',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {tab === 'mine' ? (
        <MyDownloadsPanel />
      ) : query.isError ? (
        <div
          style={{
            padding: '16px 20px',
            borderRadius: 12,
            background: 'rgba(239,68,68,0.1)',
            border: '1px solid rgba(239,68,68,0.3)',
            color: '#fca5a5',
            fontSize: 13.5,
          }}
        >
          {(query.error as { response?: { status?: number } } | null)?.response?.status === 403
            ? 'Admin privileges are required to view this page.'
            : "Couldn't load your users. Check the connection and try again."}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Compact KPI Strip */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 12,
            }}
          >
            {[
              { label: 'Profiles', value: String(users.length), accent: '#818cf8' },
              {
                label: 'Active on devices',
                value: String(data?.totals.active_downloads ?? 0),
                accent: '#22c55e',
              },
              {
                label: 'Device storage',
                value: formatBytes(data?.totals.active_bytes ?? 0),
                accent: '#38bdf8',
              },
              {
                label: 'Removed history',
                value: String(data?.totals.removed_downloads ?? 0),
                accent: '#f59e0b',
              },
            ].map((kpi) => (
              <div
                key={kpi.label}
                style={{
                  padding: '14px 16px',
                  borderRadius: 12,
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.07)',
                  borderLeft: `3px solid ${kpi.accent}`,
                }}
              >
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: '0.04em',
                    textTransform: 'uppercase',
                    opacity: 0.6,
                  }}
                >
                  {kpi.label}
                </div>
                <div style={{ fontSize: 22, fontWeight: 800, marginTop: 4 }}>{kpi.value}</div>
              </div>
            ))}
          </div>

          {/* Profiles & Permissions Panel (uses Settings' sleek .group / .row / .switch system) */}
          <section className={`${s.group} ${s.groupWide}`} aria-label="Profiles">
            <div className={s.groupHead}>
              <div className={s.groupHeadTop}>
                <span className={s.groupIcon} aria-hidden="true">
                  <PeopleIcon />
                </span>
                <div className={s.groupHeadText}>
                  <div className={s.groupTitleRow}>
                    <h2 className={s.groupTitle}>Profiles &amp; permissions</h2>
                    <span className={s.scopePill}>Admin</span>
                  </div>
                  <p className={s.groupNote}>
                    Click a profile to filter their downloads below, or toggle offline download
                    access.
                  </p>
                </div>
              </div>
            </div>
            <div className={s.groupBody}>
              {users.map((u) => {
                const isFiltered = selectedUserId === u.id;
                return (
                  <div
                    key={u.id}
                    className={s.row}
                    onClick={() => setSelectedUserId(isFiltered ? null : u.id)}
                    style={{
                      cursor: 'pointer',
                      background: isFiltered ? 'rgba(129, 140, 248, 0.08)' : undefined,
                      borderRadius: 10,
                      transition: 'background 140ms ease',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        minWidth: 0,
                        flex: 1,
                      }}
                    >
                      <div
                        style={{
                          width: 34,
                          height: 34,
                          borderRadius: '50%',
                          background: u.avatar_color || '#6366f1',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontWeight: 800,
                          fontSize: 14,
                          flexShrink: 0,
                          color: '#fff',
                        }}
                      >
                        {u.name.slice(0, 1).toUpperCase()}
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span className={s.rowLabel}>{u.name}</span>
                          <span
                            style={{
                              fontSize: 10,
                              fontWeight: 700,
                              letterSpacing: '0.05em',
                              textTransform: 'uppercase',
                              padding: '2px 7px',
                              borderRadius: 999,
                              background: u.is_admin
                                ? 'rgba(129,140,248,0.18)'
                                : 'rgba(255,255,255,0.06)',
                              color: u.is_admin ? '#c7d2fe' : 'rgba(255,255,255,0.65)',
                            }}
                          >
                            {u.is_admin ? 'Admin' : 'Member'}
                          </span>
                          {isFiltered && (
                            <span
                              style={{
                                fontSize: 10,
                                fontWeight: 700,
                                padding: '2px 7px',
                                borderRadius: 999,
                                background: 'rgba(129,140,248,0.28)',
                                color: '#fff',
                              }}
                            >
                              Filtered
                            </span>
                          )}
                        </div>
                        <span className={s.rowHelp}>
                          {u.active_downloads} on device · {formatBytes(u.active_bytes)} ·{' '}
                          {u.active_devices} {u.active_devices === 1 ? 'device' : 'devices'} ·{' '}
                          {u.removed_downloads} removed
                        </span>
                      </div>
                    </div>

                    <div
                      style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {u.is_admin ? (
                        <span style={{ fontSize: 12, opacity: 0.55, fontWeight: 500 }}>
                          Always allowed
                        </span>
                      ) : (
                        <>
                          <span style={{ fontSize: 12, opacity: 0.7 }}>
                            {u.allow_downloads ? 'Downloads on' : 'Downloads off'}
                          </span>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={u.allow_downloads}
                            aria-label={`Allow downloads for ${u.name}`}
                            className={s.switch}
                            disabled={togglePerm.isPending}
                            onClick={() =>
                              togglePerm.mutate({ profileId: u.id, allow: !u.allow_downloads })
                            }
                          />
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Download Activity Audit Panel */}
          <section className={`${s.group} ${s.groupWide}`} aria-label="Download activity">
            <div className={s.groupHead}>
              <div className={s.groupHeadTop}>
                <span className={s.groupIcon} aria-hidden="true">
                  <DownloadCloudIcon />
                </span>
                <div className={s.groupHeadText}>
                  <div className={s.groupTitleRow}>
                    <h2 className={s.groupTitle}>
                      Download activity{selectedUser ? ` · ${selectedUser.name}` : ''}
                    </h2>
                    <span className={s.scopePill}>{downloads.length}</span>
                  </div>
                  <p className={s.groupNote}>
                    Live device inventory and synced removal log across all media types.
                  </p>
                </div>
              </div>
            </div>

            <div className={s.groupBody}>
              {/* Sleek single-line filter toolbar */}
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  gap: 10,
                  padding: '12px 16px',
                  borderBottom: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <div className={s.segGroup} role="radiogroup" aria-label="Media type">
                  {MEDIA_TYPES.map((t) => {
                    const on = typeFilter === t.id;
                    return (
                      <button
                        key={t.id || 'all'}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        className={s.segBtn}
                        data-on={on ? 'true' : undefined}
                        onClick={() => setTypeFilter(t.id)}
                      >
                        {t.label}
                      </button>
                    );
                  })}
                </div>

                <div className={s.segGroup} role="radiogroup" aria-label="Status">
                  {(
                    [
                      ['', 'All'],
                      ['downloaded', 'On device'],
                      ['removed', 'Removed'],
                    ] as const
                  ).map(([st, lbl]) => {
                    const on = statusFilter === st;
                    return (
                      <button
                        key={st || 'all-st'}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        className={s.segBtn}
                        data-on={on ? 'true' : undefined}
                        onClick={() => setStatusFilter(st)}
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
                  placeholder="Search title, user, or device…"
                  style={{
                    marginLeft: 'auto',
                    minWidth: 200,
                    height: 32,
                    padding: '0 12px',
                    borderRadius: 8,
                    border: '1px solid rgba(255,255,255,0.1)',
                    background: 'rgba(0,0,0,0.28)',
                    color: '#fff',
                    fontSize: 12.5,
                    outline: 'none',
                  }}
                />
              </div>

              {downloads.length === 0 ? (
                <div
                  style={{
                    padding: '36px 20px',
                    textAlign: 'center',
                    fontSize: 13,
                    opacity: 0.6,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  <ShieldIcon />
                  <span>No download activity matches the current filter.</span>
                </div>
              ) : (
                downloads.map((d) => {
                  const active = d.status === 'downloaded';
                  return (
                    <div key={d.id} className={s.row}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 12,
                          minWidth: 0,
                          flex: 1,
                        }}
                      >
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: 8,
                            overflow: 'hidden',
                            background: 'rgba(255,255,255,0.06)',
                            flexShrink: 0,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontSize: 10,
                            fontWeight: 700,
                            textTransform: 'uppercase',
                            opacity: 0.85,
                          }}
                        >
                          {d.artwork ? (
                            <img
                              src={d.artwork}
                              alt=""
                              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                            />
                          ) : (
                            d.item_type.slice(0, 3)
                          )}
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span
                              className={s.rowLabel}
                              style={{
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                              }}
                            >
                              {d.title}
                            </span>
                            <span
                              style={{
                                fontSize: 10,
                                fontWeight: 700,
                                textTransform: 'uppercase',
                                opacity: 0.55,
                              }}
                            >
                              {d.item_type}
                            </span>
                          </div>
                          <span className={s.rowHelp}>
                            {d.profile_name} · {d.device_name} · {d.quality} ·{' '}
                            {formatBytes(d.size_bytes)} ·{' '}
                            {new Date(d.downloaded_at).toLocaleString()}
                            {d.removed_at
                              ? ` · Removed ${new Date(d.removed_at).toLocaleString()}`
                              : ''}
                          </span>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                        <span
                          style={{
                            width: 7,
                            height: 7,
                            borderRadius: '50%',
                            background: active ? '#22c55e' : '#f43f5e',
                          }}
                        />
                        <span
                          style={{
                            fontSize: 12,
                            fontWeight: 600,
                            color: active ? '#86efac' : '#fda4af',
                          }}
                        >
                          {active ? 'On device' : 'Removed'}
                        </span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
