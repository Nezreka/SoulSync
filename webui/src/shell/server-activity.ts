/**
 * SoulSync - Live Server Activity (app-wide, music + video).
 * Ported from webui/static/server-activity.js (was a self-contained IIFE;
 * module scope now plays the IIFE's role).
 *
 * A Tautulli-style live view of every active Plex stream: who's playing what,
 * direct play vs transcode (with the codec line), bandwidth, and progress.
 * Opened by the floating activity button (next to the notifications bell);
 * slides a right-side drawer. Prefers the WebSocket push; falls back to a 3s
 * HTTP poll, and a light 20s tick keeps the button badge current anywhere.
 * Exposes window.ServerActivity (consumed by core.js's socket wiring).
 */

const URL_ACT = '/api/server-activity';
const HIST = '/api/server-activity/history';
const IMG = '/api/server-activity/image?path=';

interface SactStream {
  method?: string;
  video?: string;
  audio?: string;
  resolution?: string;
  throttled?: boolean;
  hw?: boolean;
}

interface SactSession {
  session_key?: string;
  user?: string;
  title?: string;
  subtitle?: string;
  state?: string;
  media_type?: string;
  art?: string;
  thumb?: string;
  stream?: SactStream;
  bandwidth_kbps?: number;
  location?: string;
  progress_pct?: number;
  duration_ms?: number;
  offset_ms?: number;
  player?: { product?: string; device?: string };
  link?: { kind: string; id: string | number; source?: string };
}

interface SactPayload {
  ok?: boolean;
  reason?: string;
  message?: string;
  server?: { name?: string; version?: string };
  summary?: { streams?: number; transcodes?: number; total_bandwidth_kbps?: number; wan?: number };
  sessions?: SactSession[];
}

let drawer: HTMLDivElement | null = null;
let isOpen = false;
let poll: ReturnType<typeof setInterval> | null = null;
let badgePoll: ReturnType<typeof setInterval> | null = null;
let tab = 'activity';

function esc(s: unknown): string {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function getJSON<T>(u: string): Promise<T | null> {
  return fetch(u, { headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? (r.json() as Promise<T>) : null))
    .catch(() => null);
}
function img(path: string | undefined): string {
  return path ? IMG + encodeURIComponent(path) : '';
}
function mbps(kbps: number | undefined): string {
  return kbps ? (kbps / 1000).toFixed(1) + ' Mbps' : '';
}
function fmtTime(ms: number | undefined): string {
  const t = Math.max(0, Math.floor((ms || 0) / 1000));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const mm = (h && m < 10 ? '0' : '') + m, ss = (s < 10 ? '0' : '') + s;
  return (h ? h + ':' : '') + mm + ':' + ss;
}
function initials(name: string | undefined): string {
  const p = String(name || '?').trim().split(/\s+/);
  return ((p[0] || '?')[0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase();
}
const TYPE_IC: Record<string, string> = { movie: '🎬', episode: '📺', track: '🎵', clip: '🎞️' };
function ago(epoch: number | undefined): string {
  if (!epoch) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  if (s < 604800) return Math.floor(s / 86400) + 'd ago';
  return Math.floor(s / 604800) + 'w ago';
}

// ── one activity card ────────────────────────────────────────────────────────
function actKey(s: SactSession): string {
  return s.session_key || (s.user + '|' + s.title);
}
function stateIcon(state: string | undefined): string {
  return state === 'paused' ? '❚❚' : (state === 'buffering' ? '◌' : '▶');
}
function card(s: SactSession): string {
  const st = s.stream || {}, method = st.method || 'Direct Play';
  const mCls = method === 'Transcode' ? 'tc' : (method === 'Direct Stream' ? 'ds' : 'ok');
  const artUrl = img(s.art || s.thumb);
  const poster = s.thumb ? img(s.thumb) : '';
  // transcode codec detail line (Tautulli signature)
  let xline = '';
  if (method !== 'Direct Play') {
    const bits: string[] = [];
    if (st.video) bits.push('Video ' + esc(st.video));
    if (st.audio && /→/.test(st.audio)) bits.push('Audio ' + esc(st.audio));
    if (st.throttled) bits.push('throttled');
    if (st.hw) bits.push('HW');
    if (bits.length) xline = '<div class="sact-xline">' + bits.join(' &middot; ') + '</div>';
  }
  let tags = '';
  if (st.resolution) tags += '<span class="sact-tag">' + esc(st.resolution) + '</span>';
  if (s.bandwidth_kbps) tags += '<span class="sact-tag">' + mbps(s.bandwidth_kbps) + '</span>';
  if (s.location) tags += '<span class="sact-tag sact-tag--' + esc(s.location) + '">' + esc(s.location.toUpperCase()) + '</span>';
  const stop = s.session_key
    ? '<button class="sact-stop" type="button" data-sact-stop="' + esc(s.session_key) +
      '" data-sact-title="' + esc(s.title) + '" title="Stop this stream">' +
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg></button>'
    : '';
  // a live equalizer glyph for music (CSS-animated; paused via the card state)
  const eq = (s.media_type === 'track')
    ? '<span class="sact-eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>' : '';
  const pct = s.progress_pct || 0;
  const remain = s.duration_ms ? ('-' + fmtTime(Math.max(0, s.duration_ms - (s.offset_ms || 0)))) : '';
  const lc = s.link ? ' sact-card--link' : '';
  const la = s.link ? ' data-link-kind="' + esc(s.link.kind) + '" data-link-id="' + esc(s.link.id) +
    '" data-link-source="' + esc(s.link.source) + '"' : '';
  const openIc = s.link ? '<span class="sact-open" title="Open in SoulSync"></span>' : '';
  return '<div class="sact-card sact-st-' + esc(s.state) + lc + '" data-key="' + esc(actKey(s)) + '"' + la + '>' +
    (artUrl ? '<div class="sact-art" style="background-image:url(\'' + artUrl + '\')"></div>' : '') +
    '<div class="sact-scrim"></div>' + stop +
    '<div class="sact-row">' +
      (poster
        ? '<div class="sact-poster"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' + eq + '</div>'
        : '<div class="sact-poster sact-poster--none">' + (TYPE_IC[s.media_type || ''] || '🎬') + eq + '</div>') +
      '<div class="sact-info">' +
        '<div class="sact-title" title="' + esc(s.title) + '">' + esc(s.title) + openIc + '</div>' +
        (s.subtitle ? '<div class="sact-sub">' + esc(s.subtitle) + '</div>' : '') +
        '<div class="sact-meta"><span class="sact-ava">' + esc(initials(s.user)) + '</span>' +
          '<span class="sact-uname">' + esc(s.user) + '</span>' +
          (s.player && (s.player.product || s.player.device)
            ? '<span class="sact-dot">&middot;</span><span class="sact-dev">' + esc(s.player.product || s.player.device) + '</span>' : '') +
        '</div>' +
        '<div class="sact-badges"><span class="sact-badge sact-badge--' + mCls + '">' + esc(method) + '</span>' + tags + '</div>' +
        xline +
      '</div>' +
    '</div>' +
    '<div class="sact-prog"><div class="sact-prog-fill" data-sact-fill style="width:' + pct + '%"><span class="sact-head-dot"></span></div></div>' +
    '<div class="sact-time"><span class="sact-elapsed" data-sact-elapsed>' + stateIcon(s.state) + ' ' + fmtTime(s.offset_ms) + '</span>' +
      '<span class="sact-remain" data-sact-remain>' + remain + '</span></div>' +
  '</div>';
}

function summaryBar(d: SactPayload): string {
  const sm = d.summary || {};
  let chips = '<span class="sact-chip sact-chip--hero"><strong>' + (sm.streams || 0) + '</strong> ' +
    ((sm.streams === 1) ? 'stream' : 'streams') + '</span>';
  if (sm.transcodes) chips += '<span class="sact-chip sact-chip--tc"><strong>' + sm.transcodes + '</strong> transcoding</span>';
  if (sm.total_bandwidth_kbps) chips += '<span class="sact-chip">' + mbps(sm.total_bandwidth_kbps) + '</span>';
  if (sm.wan) chips += '<span class="sact-chip">' + sm.wan + ' remote</span>';
  return '<div class="sact-summary">' + chips + '</div>';
}

function _body(): HTMLElement | null {
  return drawer && drawer.querySelector('[data-sact-body]');
}
function _noServer(d: SactPayload | null): string {
  return '<div class="sact-empty"><div class="sact-empty-ic">🔌</div>' +
    '<div class="sact-empty-t">' + esc((d && d.message) || 'Server unavailable') + '</div>' +
    '<div class="sact-empty-s">Set your Plex server in Settings to see live activity.</div></div>';
}

let _actData: SactPayload | null = null;
let _polledAt = 0;
let _actKeys = '';
function _cardMap(): Record<string, HTMLElement> {
  const m: Record<string, HTMLElement> = {};
  const list = drawer && drawer.querySelector('[data-sact-list]');
  if (list) {
    list.querySelectorAll<HTMLElement>('.sact-card').forEach((el) => {
      m[el.getAttribute('data-key')!] = el;
    });
  }
  return m;
}
function renderActivity(d: SactPayload | null): void {
  const body = _body();
  if (!body) return;
  if (!d || d.ok === false) {
    body.innerHTML = _noServer(d);
    _actData = null;
    _actKeys = '';
    return;
  }
  const sub = drawer!.querySelector('[data-sact-server]');
  if (sub) {
    sub.textContent = (d.server && d.server.name)
      ? (d.server.name + (d.server.version ? ' · ' + d.server.version : '')) : '';
  }
  const sessions = d.sessions || [];
  _actData = d;
  _polledAt = Date.now();
  if (!sessions.length) {
    _actKeys = '';
    body.innerHTML = summaryBar(d) + '<div class="sact-empty"><div class="sact-empty-ic">🌙</div>' +
      '<div class="sact-empty-t">Nothing playing right now</div>' +
      '<div class="sact-empty-s">Active streams show up here the moment someone hits play.</div></div>';
    return;
  }
  const keys = sessions.map(actKey).join('§');
  // Same streams as last poll -> DON'T rebuild the DOM (no art re-decode /
  // flicker); just refresh the summary + per-card state, and let the ticker
  // glide the bars.
  if (keys === _actKeys && drawer!.querySelector('[data-sact-list]')) {
    const sm = body.querySelector('[data-sact-summary]');
    if (sm) sm.innerHTML = summaryBar(d);
    const map = _cardMap();
    sessions.forEach((s) => {
      const el = map[actKey(s)];
      if (!el) return;
      // Keep the link modifier - dropping it here is what made the card
      // stop being clickable ~1 tick after it first rendered.
      el.className = 'sact-card sact-st-' + s.state + (s.link ? ' sact-card--link' : '');
      const mb = el.querySelector('.sact-badge');
      if (mb) {
        const m = (s.stream || {}).method || 'Direct Play';
        mb.className = 'sact-badge sact-badge--' + (m === 'Transcode' ? 'tc' : (m === 'Direct Stream' ? 'ds' : 'ok'));
        mb.textContent = m;
      }
    });
    liveTick();
    return;
  }
  _actKeys = keys;
  body.innerHTML = '<div data-sact-summary>' + summaryBar(d) + '</div>' +
    '<div class="sact-list sact-enter" data-sact-list>' + sessions.map(card).join('') + '</div>';
  liveTick();
}

// Smoothly advance the progress bar + times between the 3s polls, from the
// last poll's offset + wall-clock elapsed (playing only). This is what makes
// it feel LIVE instead of stepping every few seconds.
function liveTick(): void {
  if (!isOpen || tab !== 'activity' || !_actData) return;
  const now = Date.now(), map = _cardMap();
  (_actData.sessions || []).forEach((s) => {
    const el = map[actKey(s)];
    if (!el || !s.duration_ms) return;
    let live = (s.offset_ms || 0) + (s.state === 'playing' ? (now - _polledAt) : 0);
    if (live > s.duration_ms) live = s.duration_ms;
    const pct = 100 * live / s.duration_ms;
    const fill = el.querySelector('[data-sact-fill]') as HTMLElement | null;
    if (fill) fill.style.width = pct.toFixed(2) + '%';
    const ee = el.querySelector('[data-sact-elapsed]');
    if (ee) ee.textContent = stateIcon(s.state) + ' ' + fmtTime(live);
    const rr = el.querySelector('[data-sact-remain]');
    if (rr) rr.textContent = '-' + fmtTime(Math.max(0, s.duration_ms - live));
  });
}

// ── history tab ──────────────────────────────────────────────────────────────
interface SactHistoryRow {
  title?: string;
  subtitle?: string;
  user?: string;
  device?: string;
  media_type?: string;
  thumb?: string;
  viewed_epoch?: number;
}
function historyRow(h: SactHistoryRow): string {
  const poster = h.thumb ? img(h.thumb) : '';
  return '<div class="sact-hrow">' +
    (poster
      ? '<div class="sact-hthumb"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"></div>'
      : '<div class="sact-hthumb sact-hthumb--none">' + (TYPE_IC[h.media_type || ''] || '🎬') + '</div>') +
    '<div class="sact-hinfo">' +
      '<div class="sact-htitle" title="' + esc(h.title) + '">' + esc(h.title) + '</div>' +
      (h.subtitle ? '<div class="sact-hsub">' + esc(h.subtitle) + '</div>' : '') +
      '<div class="sact-hmeta"><span class="sact-ava">' + esc(initials(h.user)) + '</span>' +
        '<span class="sact-uname">' + esc(h.user) + '</span>' +
        (h.device ? '<span class="sact-dot">&middot;</span><span class="sact-dev">' + esc(h.device) + '</span>' : '') +
      '</div>' +
    '</div>' +
    '<div class="sact-hwhen">' + esc(ago(h.viewed_epoch)) + '</div></div>';
}
function renderHistory(d: (SactPayload & { history?: SactHistoryRow[] }) | null): void {
  const body = _body();
  if (!body) return;
  if (!d || d.ok === false) {
    body.innerHTML = _noServer(d);
    return;
  }
  const rows = d.history || [];
  if (!rows.length) {
    body.innerHTML = '<div class="sact-empty"><div class="sact-empty-ic">🕓</div>' +
      '<div class="sact-empty-t">No history yet</div>' +
      '<div class="sact-empty-s">Finished streams show up here.</div></div>';
    return;
  }
  body.innerHTML = '<div class="sact-hlist">' + rows.map(historyRow).join('') + '</div>';
}
function loadHistory(): void {
  void getJSON<SactPayload & { history?: SactHistoryRow[] }>(HIST + '?limit=50').then((d) => {
    if (isOpen && tab === 'history') renderHistory(d);
  });
}

// ── stats tab (beat the Tautulli/Plex dashboard glance) ──────────────────────
const STATS = '/api/server-activity/stats';
interface SactStats extends SactPayload {
  total_plays?: number;
  unique_users?: number;
  days?: number;
  series?: Array<{ date: string; plays: number }>;
  top_content?: Array<{ title?: string; media_type?: string; thumb?: string; plays: number }>;
  top_users?: Array<{ user: string; plays: number }>;
  top_devices?: Array<{ device: string; plays: number }>;
}
function graph(series: SactStats['series']): string {
  const s = series || [];
  const max = Math.max(...s.map((p) => p.plays), 1);
  const W = 416, H = 82, n = s.length || 1, gap = 4, bw = (W - (n - 1) * gap) / n;
  const bars = s.map((p, i) => {
    const h = Math.max(p.plays ? 4 : 2, Math.round((p.plays / max) * (H - 10)));
    const x = i * (bw + gap), y = H - h;
    const day = p.date.slice(5);
    const peak = (p.plays === max && p.plays > 0) ? ' sact-bar--peak' : '';
    const empty = p.plays ? '' : ' sact-bar--empty';
    return '<rect x="' + x.toFixed(1) + '" y="' + y + '" width="' + bw.toFixed(1) + '" height="' + h +
      '" rx="2.5" class="sact-bar' + peak + empty + '"><title>' + esc(day) + ': ' + p.plays + ' plays</title></rect>';
  }).join('');
  const first = (s[0] && s[0].date.slice(5)) || '', last = (s[n - 1] && s[n - 1].date.slice(5)) || '';
  return '<svg class="sact-graph" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
      '<defs><linearGradient id="sactBar" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0" stop-color="#4ade80"/><stop offset="1" stop-color="#22c55e" stop-opacity="0.45"/>' +
      '</linearGradient></defs>' + bars + '</svg>' +
    '<div class="sact-graph-x"><span>' + esc(first) + '</span><span>peak ' + max + '</span><span>' + esc(last) + '</span></div>';
}
function rankList(items: Array<Record<string, unknown> & { plays: number }>, nameKey: string, cls: string): string {
  const max = Math.max(...items.map((i) => i.plays), 1);
  return '<div class="sact-rank">' + items.map((it) => {
    const av = (cls === 'user') ? '<span class="sact-ava">' + esc(initials(String(it[nameKey]))) + '</span>' : '';
    return '<div class="sact-rank-row">' + av +
      '<span class="sact-rank-name" title="' + esc(it[nameKey]) + '">' + esc(it[nameKey]) + '</span>' +
      '<span class="sact-rank-bar"><span style="width:' + Math.round(100 * it.plays / max) + '%"></span></span>' +
      '<span class="sact-rank-n">' + it.plays + '</span></div>';
  }).join('') + '</div>';
}
function contentRow(c: NonNullable<SactStats['top_content']>[number]): string {
  const poster = c.thumb ? img(c.thumb) : '';
  return '<div class="sact-cw">' +
    (poster ? '<div class="sact-cw-th"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"></div>'
      : '<div class="sact-cw-th sact-cw-th--none">' + (TYPE_IC[c.media_type || ''] || '🎬') + '</div>') +
    '<div class="sact-cw-t" title="' + esc(c.title) + '">' + esc(c.title) + '</div>' +
    '<div class="sact-cw-n">' + c.plays + '</div></div>';
}
function section(title: string, inner: string): string {
  return '<div class="sact-sec"><div class="sact-sec-h">' + esc(title) + '</div>' + inner + '</div>';
}
function renderStats(d: SactStats | null): void {
  const body = _body();
  if (!body) return;
  if (!d || d.ok === false) {
    body.innerHTML = _noServer(d);
    return;
  }
  let html = '<div class="sact-summary">' +
    '<span class="sact-chip sact-chip--hero"><strong>' + (d.total_plays || 0) + '</strong> plays</span>' +
    '<span class="sact-chip"><strong>' + (d.unique_users || 0) + '</strong> users</span>' +
    '<span class="sact-chip">last ' + (d.days || 30) + ' days</span></div>';
  html += section('Plays over time', graph(d.series));
  if ((d.top_content || []).length)
    html += section('Most watched', '<div class="sact-cwlist">' + d.top_content!.map(contentRow).join('') + '</div>');
  if ((d.top_users || []).length)
    html += section('Most active users', rankList(d.top_users!, 'user', 'user'));
  if ((d.top_devices || []).length)
    html += section('Top devices', rankList(d.top_devices!, 'device', 'device'));
  if (!(d.total_plays)) html = '<div class="sact-empty"><div class="sact-empty-ic">📊</div>' +
    '<div class="sact-empty-t">No plays in the last ' + (d.days || 30) + ' days</div></div>';
  body.innerHTML = html;
}
function loadStats(): void {
  void getJSON<SactStats>(STATS).then((d) => {
    if (isOpen && tab === 'stats') renderStats(d);
  });
}

function setBadge(n: number): void {
  const b = document.getElementById('activity-float-badge');
  const btn = document.getElementById('activity-float-btn');
  if (!b || !btn) return;
  if (n > 0) {
    b.textContent = n > 99 ? '99+' : String(n);
    b.style.display = '';
    btn.classList.add('activity-live');
  } else {
    b.style.display = 'none';
    btn.classList.remove('activity-live');
  }
}

// Server Activity is a Plex/Jellyfin-only feature (live sessions). Hide the
// launcher entirely when neither is configured (e.g. a Navidrome music setup)
// so it isn't dead weight. A fetch failure leaves the button as-is - only a
// definitive 'no_server' hides it.
function setSupported(on: boolean): void {
  const btn = document.getElementById('activity-float-btn');
  if (btn) btn.style.display = on ? '' : 'none';
}

function refresh(): Promise<SactPayload | null> {
  return getJSON<SactPayload>(URL_ACT).then((d) => {
    if (d && d.ok === false && d.reason === 'no_server') setSupported(false);
    else if (d) setSupported(true);
    if (d) setBadge((d.summary && d.summary.streams) || 0);
    if (isOpen && tab === 'activity') renderActivity(d);
    return d;
  });
}
function startPoll(): void {
  stopPoll();
  poll = setInterval(() => void refresh(), 3000); // live cadence
}
function stopPoll(): void {
  if (poll) {
    clearInterval(poll);
    poll = null;
  }
}

// Live feed: prefer the WebSocket push (one upstream poll shared across every
// open drawer - matters when multiple profiles are watching at once) and fall
// back to the 3s HTTP poll when there's no socket. A watchdog re-arms HTTP if
// the socket is connected but pushes stop landing.
let _sockLive = false;
let _watchdog: ReturnType<typeof setInterval> | null = null;
let _lastPush = 0;
function onSocket(raw: unknown): void {
  const d = raw as SactPayload | null;
  _lastPush = Date.now();
  if (d) setBadge((d.summary && d.summary.streams) || 0);
  if (isOpen && tab === 'activity') renderActivity(d);
}
function startLive(): void {
  stopLive();
  void refresh(); // instant HTTP paint - don't wait up to 3s for the first push
  const s = window.SoulSyncActivitySocket;
  if (s && s.isConnected()) {
    s.subscribe();
    _sockLive = true;
    _lastPush = Date.now();
    _watchdog = setInterval(() => {
      if (_sockLive && Date.now() - _lastPush > 9000) {
        _sockLive = false;
        startPoll();
      }
    }, 3000);
  } else {
    startPoll();
  }
}
function stopLive(): void {
  stopPoll();
  if (_watchdog) {
    clearInterval(_watchdog);
    _watchdog = null;
  }
  const s = window.SoulSyncActivitySocket;
  if (_sockLive && s) s.unsubscribe();
  _sockLive = false;
}

function setTab(t: string): void {
  tab = t;
  if (drawer) {
    drawer.querySelectorAll('[data-sact-tab]').forEach((b) => {
      b.classList.toggle('sact-tab--on', b.getAttribute('data-sact-tab') === t);
    });
  }
  const body = _body();
  if (body) {
    body.innerHTML = '<div class="sact-empty"><div class="sact-empty-ic">…</div>' +
      '<div class="sact-empty-t">Loading…</div></div>';
  }
  if (t === 'activity') {
    startLive();
  } else if (t === 'history') {
    stopLive();
    loadHistory();
  } else {
    stopLive();
    loadStats();
  }
}

// ── drawer open/close ────────────────────────────────────────────────────────
function build(): void {
  drawer = document.createElement('div');
  drawer.className = 'sact-drawer';
  drawer.innerHTML =
    '<div class="sact-head">' +
      '<div class="sact-head-t"><span class="sact-live-dot"></span>Server Activity' +
        '<span class="sact-server" data-sact-server></span></div>' +
      '<button class="sact-x" type="button" data-sact-close aria-label="Close">&times;</button>' +
    '</div>' +
    '<div class="sact-tabs">' +
      '<button class="sact-tab sact-tab--on" type="button" data-sact-tab="activity">Activity</button>' +
      '<button class="sact-tab" type="button" data-sact-tab="history">History</button>' +
      '<button class="sact-tab" type="button" data-sact-tab="stats">Stats</button>' +
    '</div>' +
    '<div class="sact-body" data-sact-body></div>';
  document.body.appendChild(drawer);
  drawer.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sact-close]')) {
      close();
      return;
    }
    const tb = target.closest('[data-sact-tab]');
    if (tb) {
      setTab(tb.getAttribute('data-sact-tab')!);
      return;
    }
    const sb = target.closest('[data-sact-stop]');
    if (sb) {
      openStop(sb.getAttribute('data-sact-stop')!, sb.getAttribute('data-sact-title') || '');
      return;
    }
    // Click a card -> jump to that movie/show's page inside SoulSync.
    const lk = target.closest('.sact-card--link');
    if (lk) {
      const id = lk.getAttribute('data-link-id')!;
      close();
      if (window.SoulSyncVideo && window.SoulSyncVideo.openDetail) {
        window.SoulSyncVideo.openDetail({
          kind: lk.getAttribute('data-link-kind')!,
          id: parseInt(id, 10) || id,
          source: lk.getAttribute('data-link-source') || 'library',
        });
      }
      return;
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen) close();
  });
}

let ticker: ReturnType<typeof setInterval> | null = null;
function open(): void {
  if (!drawer) build();
  isOpen = true;
  _scrim().classList.add('visible');
  requestAnimationFrame(() => drawer!.classList.add('visible'));
  setTab('activity');
  if (ticker) clearInterval(ticker);
  ticker = setInterval(liveTick, 500); // glide the progress bars between polls
}
function close(): void {
  isOpen = false;
  if (drawer) drawer.classList.remove('visible');
  _scrim().classList.remove('visible');
  stopLive();
  if (ticker) {
    clearInterval(ticker);
    ticker = null;
  }
}
function toggle(): void {
  if (isOpen) close();
  else open();
}

// ── stop a stream (admin, with a message) ────────────────────────────────────
function toast(m: string, t: string): void {
  window.showToast?.(m, t);
}
function openStop(key: string, title: string): void {
  const ov = document.createElement('div');
  ov.className = 'sact-stop-ov';
  ov.innerHTML =
    '<div class="sact-stop-modal">' +
      '<div class="sact-stop-h">Stop stream</div>' +
      '<div class="sact-stop-sub">' + esc(title || 'this stream') + '</div>' +
      '<label class="sact-stop-lbl">Message shown to the viewer</label>' +
      '<textarea class="sact-stop-msg" rows="2">The server administrator ended this stream.</textarea>' +
      '<div class="sact-stop-foot">' +
        '<button class="sact-stop-btn" type="button" data-stop-cancel>Cancel</button>' +
        '<button class="sact-stop-btn sact-stop-btn--go" type="button" data-stop-go>Stop stream</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(ov);
  const shut = () => ov.remove();
  ov.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;
    if (e.target === ov || target.closest('[data-stop-cancel]')) {
      shut();
      return;
    }
    if (target.closest('[data-stop-go]')) {
      const msg = (ov.querySelector('.sact-stop-msg') as HTMLTextAreaElement).value;
      const go = ov.querySelector('[data-stop-go]') as HTMLButtonElement;
      go.disabled = true;
      go.textContent = 'Stopping…';
      fetch('/api/server-activity/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ session_key: key, message: msg }),
      }).then((r) => r.json().then((b: { ok?: boolean; error?: string }) => ({ ok: r.ok, b })))
        .then((res) => {
          shut();
          if (res.ok && res.b.ok) {
            toast('Stream stopped', 'success');
            void refresh();
          } else {
            toast((res.b && res.b.error) || 'Could not stop the stream', 'error');
          }
        }).catch(() => {
          shut();
          toast('Could not stop the stream', 'error');
        });
    }
  });
}

let _sc: HTMLDivElement | null = null;
function _scrim(): HTMLDivElement {
  if (!_sc) {
    _sc = document.createElement('div');
    _sc.className = 'sact-scrim-bg';
    _sc.addEventListener('click', close);
    document.body.appendChild(_sc);
  }
  return _sc;
}

// light background tick so the badge is live from any page (cheap: sessions()
// is fast; 20s is plenty for an ambient indicator)
function startBadgePoll(): void {
  if (badgePoll) return;
  void refresh();
  badgePoll = setInterval(() => {
    if (!isOpen) void refresh();
  }, 20000);
}

window.ServerActivity = {
  toggle, open, close, refresh,
  _onSocket: onSocket,
  _wantsLive: () => isOpen && tab === 'activity',
};
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', startBadgePoll);
} else {
  startBadgePoll();
}
