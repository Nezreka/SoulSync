/**
 * Player Settings -> Live TV (admin): add HDHomeRun network tuners (LAN
 * discovery or manual IP), user M3U playlists, and XMLTV guides, then manage
 * channels and guide-ID mappings. Free and legal: SoulSync ships no streams.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  addLiveSource,
  deleteLiveSource,
  discoverHdHomeRun,
  fetchLiveChannels,
  fetchLiveSources,
  refreshLiveSource,
  updateLiveChannel,
  type DiscoveredHdhr,
  type LiveChannel,
  type LiveSource,
  type LiveSourceKind,
} from '../../-lib/live';
import p from './settings.module.css';

const KIND_LABEL: Record<LiveSourceKind, string> = {
  hdhomerun: 'HDHomeRun tuner',
  m3u: 'M3U playlist',
  xmltv: 'XMLTV guide',
};

export function LiveTvSection() {
  const [sources, setSources] = useState<LiveSource[]>([]);
  const [channels, setChannels] = useState<LiveChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [discovered, setDiscovered] = useState<DiscoveredHdhr[]>([]);

  const [kind, setKind] = useState<LiveSourceKind>('hdhomerun');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [chanFilter, setChanFilter] = useState('');

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, c] = await Promise.all([fetchLiveSources(), fetchLiveChannels(true)]);
      setSources(s);
      setChannels(c);
    } catch {
      setNote('Could not load Live TV settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const onDiscover = async () => {
    setBusy(true);
    setNote('');
    try {
      const res = await discoverHdHomeRun();
      const found = res.devices ?? [];
      setDiscovered(found);
      setNote(
        found.length
          ? `Found ${found.length} HDHomeRun tuner${found.length === 1 ? '' : 's'} on your network.`
          : 'No HDHomeRun found via broadcast. You can still enter its IP address below.',
      );
    } catch {
      setNote('Network discovery failed. Try entering the tuner IP address directly.');
    } finally {
      setBusy(false);
    }
  };

  const onAddDiscovered = async (dev: DiscoveredHdhr) => {
    setBusy(true);
    setNote('');
    try {
      const res = await addLiveSource({
        kind: 'hdhomerun',
        name: dev.friendly_name || `HDHomeRun ${dev.model_number}`,
        url: dev.base_url,
        tuner_count: dev.tuner_count,
        device_id: dev.device_id,
        device_auth: dev.device_auth,
      });
      if (!res.success) {
        setNote(res.error || 'Could not add tuner.');
      } else {
        setDiscovered((prev) => prev.filter((d) => d.device_id !== dev.device_id));
        setNote(`Added ${dev.friendly_name}.`);
        await loadAll();
      }
    } finally {
      setBusy(false);
    }
  };

  const onAddManual = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) return;
    setBusy(true);
    setNote('');
    try {
      const res = await addLiveSource({ kind, name: name.trim(), url: url.trim() });
      if (!res.success) {
        setNote(res.error || 'Could not add source.');
      } else {
        setName('');
        setUrl('');
        setNote(`Added ${KIND_LABEL[kind]}.`);
        await loadAll();
      }
    } finally {
      setBusy(false);
    }
  };

  const onRefresh = async (id: string) => {
    setBusy(true);
    setNote('');
    try {
      const res = await refreshLiveSource(id);
      if (res.success) {
        setNote(
          `Refreshed (${res.added ?? 0} added, ${res.updated ?? 0} updated, ${res.programs ?? 0} guide entries).`,
        );
      } else {
        setNote(res.error || 'Refresh failed.');
      }
      await loadAll();
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (id: string) => {
    setBusy(true);
    try {
      await deleteLiveSource(id);
      await loadAll();
    } finally {
      setBusy(false);
    }
  };

  const onToggleChannel = async (ch: LiveChannel) => {
    const res = await updateLiveChannel(ch.id, { enabled: !ch.enabled });
    if (res.channel) {
      setChannels((prev) => prev.map((c) => (c.id === ch.id ? res.channel! : c)));
    }
  };

  const onMapEpg = async (ch: LiveChannel, epgId: string) => {
    const res = await updateLiveChannel(ch.id, { epg_id: epgId });
    if (res.channel) {
      setChannels((prev) => prev.map((c) => (c.id === ch.id ? res.channel! : c)));
    }
  };

  const shownChannels = useMemo(() => {
    const q = chanFilter.trim().toLowerCase();
    if (!q) return channels;
    return channels.filter(
      (c) =>
        c.display_name.toLowerCase().includes(q) ||
        c.guide_number.toLowerCase().includes(q) ||
        (c.group_title || '').toLowerCase().includes(q),
    );
  }, [channels, chanFilter]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className={p.card}>
        <p style={{ margin: '0 0 12px', fontSize: 13, opacity: 0.78 }}>
          Bring an antenna tuner (HDHomeRun) or an M3U playlist and XMLTV guide you have rights to.
          Viewers on the same channel share one tuner automatically, and encrypted DRM channels are
          hidden.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
          <button
            type="button"
            className="dlc-btn"
            disabled={busy}
            onClick={() => void onDiscover()}
          >
            Discover HDHomeRun on network
          </button>
        </div>

        {discovered.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
            {discovered.map((d) => (
              <div
                key={d.device_id || d.base_url}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 10,
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-md)',
                  background: 'var(--white-a04)',
                }}
              >
                <div>
                  <div style={{ fontWeight: 600 }}>{d.friendly_name}</div>
                  <div style={{ fontSize: 12, opacity: 0.68 }}>
                    {d.model_number} · {d.tuner_count} tuners · {d.base_url}
                  </div>
                </div>
                <button
                  type="button"
                  className="dlc-btn"
                  disabled={busy}
                  onClick={() => void onAddDiscovered(d)}
                >
                  Add tuner
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <form
          onSubmit={(e) => void onAddManual(e)}
          style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8 }}
        >
          <select
            aria-label="Live source type"
            value={kind}
            onChange={(e) => setKind(e.target.value as LiveSourceKind)}
            style={{ height: 36, padding: '0 10px', borderRadius: 'var(--radius-sm)' }}
          >
            <option value="hdhomerun">HDHomeRun (IP / URL)</option>
            <option value="m3u">M3U playlist (URL or file)</option>
            <option value="xmltv">XMLTV guide (URL or file)</option>
          </select>
          <input
            type="text"
            placeholder="Label (e.g. Attic Antenna)"
            aria-label="Source name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ height: 36, padding: '0 10px', borderRadius: 'var(--radius-sm)' }}
          />
          <input
            type="text"
            placeholder={
              kind === 'hdhomerun'
                ? 'http://192.168.1.50'
                : kind === 'm3u'
                  ? 'https://…/channels.m3u or /path/to/list.m3u'
                  : 'https://…/guide.xml.gz or /path/to/epg.xml'
            }
            aria-label="Source URL or path"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            style={{ height: 36, padding: '0 10px', borderRadius: 'var(--radius-sm)' }}
          />
          <button type="submit" className="dlc-btn" disabled={busy || !name.trim() || !url.trim()}>
            Add source
          </button>
        </form>
        {note ? <p style={{ margin: '10px 0 0', fontSize: 13, opacity: 0.85 }}>{note}</p> : null}
      </div>

      <div className={p.card}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Sources ({sources.length})</h3>
        {loading ? (
          <p style={{ margin: 0, fontSize: 13, opacity: 0.65 }}>Loading…</p>
        ) : sources.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, opacity: 0.65 }}>No live TV sources added yet.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sources.map((src) => (
              <div
                key={src.id}
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 10,
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-md)',
                  background: 'var(--white-a04)',
                }}
              >
                <div>
                  <div style={{ fontWeight: 600 }}>
                    {src.name}{' '}
                    <span style={{ fontSize: 12, fontWeight: 400, opacity: 0.65 }}>
                      ({KIND_LABEL[src.kind]})
                    </span>
                  </div>
                  <div style={{ fontSize: 12, opacity: 0.68 }}>
                    {src.url}
                    {src.kind !== 'xmltv' ? ` · ${src.channel_count ?? 0} channels` : ''}
                    {src.tuner_count > 0 ? ` · ${src.tuner_count} tuners` : ''}
                  </div>
                  {src.last_error ? (
                    <div style={{ fontSize: 12, color: 'var(--warning)' }}>{src.last_error}</div>
                  ) : null}
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    type="button"
                    className="dlc-btn"
                    disabled={busy}
                    onClick={() => void onRefresh(src.id)}
                  >
                    Refresh
                  </button>
                  <button
                    type="button"
                    className="dlc-btn dlc-btn-danger"
                    disabled={busy}
                    onClick={() => void onDelete(src.id)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {channels.length > 0 ? (
        <div className={p.card}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 10,
              marginBottom: 10,
            }}
          >
            <h3 style={{ margin: 0, fontSize: 15 }}>Channels ({channels.length})</h3>
            <input
              type="search"
              placeholder="Filter channels…"
              aria-label="Filter live channels"
              value={chanFilter}
              onChange={(e) => setChanFilter(e.target.value)}
              style={{ height: 32, padding: '0 10px', borderRadius: 'var(--radius-sm)' }}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 360, overflowY: 'auto' }}>
            {shownChannels.map((ch) => (
              <div
                key={ch.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '6px 10px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--white-a04)',
                  fontSize: 13,
                }}
              >
                <input
                  type="checkbox"
                  checked={ch.enabled}
                  aria-label={`Enable ${ch.display_name}`}
                  onChange={() => void onToggleChannel(ch)}
                />
                <span style={{ minWidth: 42, fontVariantNumeric: 'tabular-nums', opacity: 0.72 }}>
                  {ch.guide_number || '—'}
                </span>
                <span style={{ flex: 1, fontWeight: 500 }}>{ch.display_name}</span>
                <input
                  type="text"
                  defaultValue={ch.epg_id || ''}
                  placeholder={ch.tvg_id || 'Guide ID'}
                  aria-label={`Guide ID for ${ch.display_name}`}
                  onBlur={(e) => {
                    const next = e.target.value.trim();
                    if (next !== (ch.epg_id || '')) void onMapEpg(ch, next);
                  }}
                  style={{ width: 140, height: 28, padding: '0 8px', fontSize: 12, borderRadius: 'var(--radius-sm)' }}
                />
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
