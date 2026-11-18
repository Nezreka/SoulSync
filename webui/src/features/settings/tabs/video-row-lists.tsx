/**
 * The video side's two row editors (SETTINGS_PORT.md, video pass V3c):
 * Notifications on the Connections tab (a Discord / webhook / Telegram
 * connection per row, the events it hears, Test) and Import Lists on the
 * Discovery tab (a TMDB / IMDb / Plex list per row). Were video-settings.js.
 * Each loads each time the video settings page is shown; a row saves alone
 * when one of its fields changes (a text field when it is left), as before.
 *
 * As before, the rows are drawn from the server's list when it loads and
 * when a row is added or removed; a row saved keeps what was typed in it (the
 * page did not draw it again), except a notification whose type changed,
 * which is drawn again so its URL or token fields swap.
 */

import { type ReactNode, useEffect, useRef, useSyncExternalStore } from 'react';

import { Section } from './parts';
import { videoShown } from './video-shown';

type Row = Record<string, unknown> & { id: number };

const toast = (message: string, type: string) =>
  (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(message, type);
const str = (v: unknown) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');

/**
 * One list: the server's rows, the rows as last drawn, and what each drawn
 * row's fields hold now.
 */
function createRows(url: string) {
  let state = {
    /** null until loaded: the editor shows nothing (a non-admin's 403 too) */
    rows: null as Row[] | null,
    drawn: [] as Row[],
    fields: {} as Record<number, Row>,
    /** extra from the load (the notification events) */
    extra: {} as Record<string, unknown>,
  };
  const listeners = new Set<() => void>();
  const set = (next: Partial<typeof state>) => {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  };
  /** draw the rows again from the server's list */
  const draw = (rows: Row[]) =>
    set({
      rows,
      drawn: rows.map((r) => ({ ...r })),
      fields: Object.fromEntries(rows.map((r) => [r.id, { ...r }])),
    });

  return {
    url,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    get: () => state,
    load: async (key: string) => {
      try {
        const r = await fetch(url, { headers: { Accept: 'application/json' } });
        const d = r.ok ? ((await r.json()) as Record<string, unknown> | null) : null;
        if (!d) return;
        state = { ...state, extra: d };
        draw((d[key] as Row[]) || []);
      } catch {
        // non-admins get a 403: the section stays empty
      }
    },
    draw,
    edit: (id: number, field: string, value: unknown) =>
      set({ fields: { ...state.fields, [id]: { ...state.fields[id], [field]: value } } }),
    /** a row saved: the server's copy replaces the list's, not what is shown */
    saved: (res: Row) => set({ rows: (state.rows || []).map((r) => (r.id === res.id ? res : r)) }),
    reset: () => {
      state = { rows: null, drawn: [], fields: {}, extra: {} };
    },
  };
}

export const importLists = createRows('/api/video/downloads/config/import-lists');
export const notifications = createRows('/api/video/notifications');
type Rows = ReturnType<typeof createRows>;

function useRows(model: Rows) {
  return useSyncExternalStore(model.subscribe, model.get);
}

const post = (url: string, body: unknown) =>
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

if (typeof document !== 'undefined') {
  let seen = videoShown.get();
  videoShown.subscribe(() => {
    if (videoShown.get() === seen) return;
    seen = videoShown.get();
    void importLists.load('lists');
    void notifications.load('connections');
  });
}

/** the browser's change event inside a row, which saves that row. it is
 *  heard before React's own (React listens at its root), so the row is read
 *  off the page, as the page did */
function useRowChange(onChange: (row: HTMLElement, field: string | null) => void) {
  const host = useRef<HTMLDivElement | null>(null);
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const changed = (event: Event) => {
      const target = event.target as HTMLElement;
      const row = target.closest<HTMLElement>('[data-vq-implist], [data-vq-notify]');
      if (!row) return;
      latest.current(
        row,
        target.getAttribute('data-vq-nt-f') || target.getAttribute('data-vq-il-f'),
      );
    };
    el.addEventListener('change', changed);
    return () => el.removeEventListener('change', changed);
  }, []);
  return host;
}

function Options({ options }: { options: Array<[string, string]> }) {
  return (
    <>
      {options.map(([v, label]) => (
        <option key={v} value={v}>
          {label}
        </option>
      ))}
    </>
  );
}

// ── import lists ──

/** a field of a row, off the page */
function rowValue(row: HTMLElement, attr: string, k: string): string | boolean {
  const el = row.querySelector<HTMLInputElement>(`[${attr}="${k}"]`);
  return el ? (el.type === 'checkbox' ? el.checked : el.value) : '';
}

/** _impListFromRow */
function listBody(row: HTMLElement) {
  const val = (k: string) => rowValue(row, 'data-vq-il-f', k);
  return {
    id: parseInt(row.getAttribute('data-vq-implist') || '', 10),
    name: val('name'),
    source: val('source'),
    ref: val('ref'),
    media: val('media'),
    monitor: val('monitor'),
    enabled: val('enabled'),
  };
}

function ImportListRows() {
  const { rows, drawn, fields } = useRows(importLists);
  const host = useRowChange((row) => {
    void (async () => {
      try {
        const r = await post(importLists.url, listBody(row));
        const res = r.ok ? ((await r.json()) as (Row & { success?: boolean }) | null) : null;
        if (!res || !res.success) {
          toast('A list needs a valid source + ref', 'error');
          return;
        }
        importLists.saved(res);
      } catch {
        toast('Couldn’t save the list', 'error');
      }
    })();
  });

  async function remove(id: number) {
    try {
      const r = await fetch(`${importLists.url}/${id}`, { method: 'DELETE' });
      const res = r.ok ? ((await r.json()) as { success?: boolean } | null) : null;
      if (!res || !res.success) throw new Error();
      importLists.draw((importLists.get().rows || []).filter((l) => l.id !== id));
    } catch {
      toast('Couldn’t delete the list', 'error');
    }
  }

  const field = (id: number, k: string) => str(fields[id]?.[k]);
  const edit = (id: number, k: string) => (event: { target: { value: string } }) =>
    importLists.edit(id, k, event.target.value);

  return (
    <div id="vq-implist-rows" ref={host}>
      {rows === null ? null : drawn.length ? (
        drawn.map((l) => (
          <div className="vq-fmt-row vq-implist-row" key={l.id} data-vq-implist={l.id}>
            <input
              className="vq-fmt-in"
              data-vq-il-f="name"
              placeholder="Name"
              value={field(l.id, 'name')}
              onChange={edit(l.id, 'name')}
            />
            <select
              className="vq-fmt-in"
              data-vq-il-f="source"
              value={field(l.id, 'source')}
              onChange={edit(l.id, 'source')}
            >
              <Options
                options={[
                  ['tmdb_list', 'TMDB list'],
                  ['tmdb_chart', 'TMDB chart'],
                  ['imdb_list', 'IMDb list'],
                  ['plex_watchlist', 'Plex Watchlist'],
                ]}
              />
            </select>
            <input
              className="vq-fmt-in"
              data-vq-il-f="ref"
              placeholder="list id / chart / ls…"
              value={field(l.id, 'ref')}
              onChange={edit(l.id, 'ref')}
            />
            <select
              className="vq-fmt-in"
              data-vq-il-f="media"
              value={field(l.id, 'media')}
              onChange={edit(l.id, 'media')}
            >
              <Options
                options={[
                  ['both', 'Both'],
                  ['movie', 'Movies'],
                  ['show', 'Shows'],
                ]}
              />
            </select>
            <select
              className="vq-fmt-in"
              data-vq-il-f="monitor"
              title="Shows: what to wish when followed"
              value={field(l.id, 'monitor')}
              onChange={edit(l.id, 'monitor')}
            >
              <Options
                options={[
                  ['future', 'Future eps'],
                  ['all', 'All aired'],
                  ['latest_season', 'Latest season'],
                  ['first_season', 'First season'],
                  ['pilot', 'Pilot'],
                ]}
              />
            </select>
            <label className="vq-il-on" title="Enabled">
              <input
                type="checkbox"
                data-vq-il-f="enabled"
                checked={!!fields[l.id]?.enabled}
                onChange={(event) => importLists.edit(l.id, 'enabled', event.target.checked)}
              />
            </label>
            <button
              className="vq-fmt-del"
              type="button"
              data-vq-implist-del={l.id}
              title="Delete list"
              onClick={() => void remove(l.id)}
            >
              ✕
            </button>
          </div>
        ))
      ) : (
        <div className="settings-hint" style={{ padding: '6px 0' }}>
          No import lists yet.
        </div>
      )}
    </div>
  );
}

async function addImportList() {
  try {
    const r = await post(importLists.url, {
      name: 'New list',
      source: 'tmdb_chart',
      ref: 'trending_movies',
    });
    const res = r.ok ? ((await r.json()) as (Row & { success?: boolean }) | null) : null;
    if (!res || !res.success) throw new Error();
    importLists.draw([...(importLists.get().rows || []), res]);
  } catch {
    toast('Couldn’t add a list', 'error');
  }
}

export function ImportListsSection() {
  return (
    <Section
      tab="discovery"
      side="video"
      title="Import Lists"
      hint="Auto-add movies & shows from a list"
    >
      <div className="settings-group" data-stg="discovery" data-video-only="">
        <div className="form-group" id="video-import-lists">
          <label>Import lists:</label>
          <small className="settings-hint" style={{ display: 'block', marginBottom: '8px' }}>
            Everything on a list enters acquisition automatically (movies → wishlist, shows → follow
            with the chosen monitor policy). Only NEW list members are added, so removing something
            never boomerangs back. Synced by the &quot;Sync Import Lists&quot; automation. Ref: a
            TMDB list id, a chart key (trending_movies, top_shows, …), an IMDb ls-id/URL — Plex
            Watchlist needs no ref.
          </small>
          <ImportListRows />
          <button
            className="test-button"
            type="button"
            data-vq-implist-add=""
            onClick={() => void addImportList()}
          >
            + Add list
          </button>
        </div>
      </div>
    </Section>
  );
}

// ── notifications ──

export const NOTIFY_EVENT_LABEL: Record<string, string> = {
  video_download_completed: 'Imported',
  video_upgrade_completed: 'Upgraded',
  video_import_failed: 'Import failed',
  video_download_failed: 'Failed',
  video_wishlist_item_added: 'Wishlisted',
  video_watchlist_added: 'Followed',
};

/** _notifyFromBlock */
function notifyBody(block: HTMLElement) {
  const val = (k: string) => rowValue(block, 'data-vq-nt-f', k);
  return {
    id: parseInt(block.getAttribute('data-vq-notify') || '', 10),
    name: val('name'),
    type: val('type'),
    url: val('url'),
    token: val('token'),
    chat_id: val('chat_id'),
    enabled: val('enabled'),
    events: [...block.querySelectorAll<HTMLInputElement>('[data-vq-nt-ev]')]
      .filter((cb) => cb.checked)
      .map((cb) => cb.getAttribute('data-vq-nt-ev') || ''),
  };
}

function NotifyBlocks() {
  const { rows, drawn, fields, extra } = useRows(notifications);
  const events = (Array.isArray(extra.events) ? extra.events : []) as string[];
  const host = useRowChange((block, changed) => {
    void (async () => {
      try {
        const r = await post(notifications.url, notifyBody(block));
        const res = r.ok ? ((await r.json()) as (Row & { success?: boolean }) | null) : null;
        if (!res || !res.success) {
          toast('Connection needs a valid target (URL or token + chat id)', 'error');
          return;
        }
        notifications.saved(res);
        // a new type swaps the target fields: drawn again
        if (changed === 'type') notifications.draw(notifications.get().rows || []);
      } catch {
        toast('Couldn’t save the connection', 'error');
      }
    })();
  });

  async function remove(id: number) {
    try {
      const r = await fetch(`${notifications.url}/${id}`, { method: 'DELETE' });
      const res = r.ok ? ((await r.json()) as { success?: boolean } | null) : null;
      if (!res || !res.success) throw new Error();
      notifications.draw((notifications.get().rows || []).filter((c) => c.id !== id));
    } catch {
      toast('Couldn’t delete the connection', 'error');
    }
  }

  async function test(button: HTMLButtonElement) {
    const block = button.closest<HTMLElement>('[data-vq-notify]');
    if (!block) return;
    button.disabled = true;
    try {
      const r = await post(`${notifications.url}/test`, notifyBody(block));
      const j = (await r.json().catch(() => null)) as { success?: boolean; error?: string } | null;
      button.disabled = false;
      if (r.ok && j && j.success) toast('Test sent — check the channel', 'success');
      else toast((j && j.error) || 'Test failed', 'error');
    } catch {
      button.disabled = false;
      toast('Test failed', 'error');
    }
  }

  const field = (id: number, k: string) => str(fields[id]?.[k]);
  const edit = (id: number, k: string) => (event: { target: { value: string } }) =>
    notifications.edit(id, k, event.target.value);
  const hide = (on: boolean) => (on ? undefined : { display: 'none' });

  return (
    <div id="vq-notify-rows" ref={host}>
      {rows === null ? null : drawn.length ? (
        drawn.map((c) => {
          // the target fields follow the type the row was drawn with
          const tg = c.type === 'telegram';
          const ticked = (fields[c.id]?.events as string[] | undefined) || [];
          return (
            <div className="vq-nt-block" key={c.id} data-vq-notify={c.id}>
              <div className="vq-fmt-row vq-nt-row">
                <input
                  className="vq-fmt-in"
                  data-vq-nt-f="name"
                  placeholder="Name"
                  value={field(c.id, 'name')}
                  onChange={edit(c.id, 'name')}
                />
                <select
                  className="vq-fmt-in"
                  data-vq-nt-f="type"
                  value={field(c.id, 'type')}
                  onChange={edit(c.id, 'type')}
                >
                  <Options
                    options={[
                      ['discord', 'Discord'],
                      ['webhook', 'Webhook'],
                      ['telegram', 'Telegram'],
                    ]}
                  />
                </select>
                <input
                  className="vq-fmt-in"
                  data-vq-nt-f="url"
                  placeholder="Webhook URL"
                  style={hide(!tg)}
                  value={field(c.id, 'url')}
                  onChange={edit(c.id, 'url')}
                />
                <input
                  className="vq-fmt-in"
                  data-vq-nt-f="token"
                  placeholder="Bot token"
                  style={hide(tg)}
                  value={field(c.id, 'token')}
                  onChange={edit(c.id, 'token')}
                />
                <input
                  className="vq-fmt-in"
                  data-vq-nt-f="chat_id"
                  placeholder="Chat id"
                  style={hide(tg)}
                  value={field(c.id, 'chat_id')}
                  onChange={edit(c.id, 'chat_id')}
                />
                <button
                  className="test-button vq-nt-test"
                  type="button"
                  data-vq-notify-test={c.id}
                  onClick={(event) => void test(event.currentTarget)}
                >
                  Test
                </button>
                <label className="vq-il-on" title="Enabled">
                  <input
                    type="checkbox"
                    data-vq-nt-f="enabled"
                    checked={!!fields[c.id]?.enabled}
                    onChange={(event) => notifications.edit(c.id, 'enabled', event.target.checked)}
                  />
                </label>
                <button
                  className="vq-fmt-del"
                  type="button"
                  data-vq-notify-del={c.id}
                  title="Delete"
                  onClick={() => void remove(c.id)}
                >
                  ✕
                </button>
              </div>
              <div className="vq-nt-events">
                {events.map((ev) => (
                  <label className="vq-nt-ev" key={ev}>
                    <input
                      type="checkbox"
                      data-vq-nt-ev={ev}
                      checked={ticked.includes(ev)}
                      onChange={(event) =>
                        notifications.edit(
                          c.id,
                          'events',
                          // in the page's order, as the boxes were read
                          events.filter((e) =>
                            e === ev ? event.target.checked : ticked.includes(e),
                          ),
                        )
                      }
                    />
                    {NOTIFY_EVENT_LABEL[ev] || ev}
                  </label>
                ))}
              </div>
            </div>
          );
        })
      ) : (
        <div className="settings-hint" style={{ padding: '6px 0' }}>
          No connections yet.
        </div>
      )}
    </div>
  );
}

async function addNotification() {
  try {
    const r = await post(notifications.url, {
      name: 'Discord',
      type: 'discord',
      url: 'https://discord.com/api/webhooks/REPLACE-ME',
    });
    const res = r.ok ? ((await r.json()) as (Row & { success?: boolean }) | null) : null;
    if (!res || !res.success) throw new Error();
    notifications.draw([...(notifications.get().rows || []), res]);
  } catch {
    toast('Couldn’t add a connection', 'error');
  }
}

export function NotificationsSection(): ReactNode {
  return (
    <Section
      tab="connections"
      side="video"
      title="Notifications"
      hint="Discord, webhooks & Telegram"
    >
      <div className="settings-group" data-stg="connections" data-video-only="">
        {/* the retired video source picker that rode along in here went in
            SETTINGS_PORT.md video pass V3c: nothing read it */}
        <div className="form-group" id="video-notifications">
          <label>Notifications:</label>
          <small className="settings-hint" style={{ display: 'block', marginBottom: '8px' }}>
            Get told when things happen — a Discord webhook, any generic webhook (raw JSON), or a
            Telegram bot. Each connection picks which events it cares about.
          </small>
          <NotifyBlocks />
          <button
            className="test-button"
            type="button"
            data-vq-notify-add=""
            onClick={() => void addNotification()}
          >
            + Add connection
          </button>
        </div>
      </div>
    </Section>
  );
}
