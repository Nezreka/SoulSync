/**
 * downloads: pick a device and see what's on it, from any screen. a phone's
 * shows, films, albums, playlists, books and podcasts in groups, each with
 * what it is doing (on the phone, 60%, waiting, watched), and what can be
 * done about it (add the rest, smart settings, remove). changes reach the
 * phone the next time it's online. "this computer" lists the files saved
 * from this browser, each downloadable again in another quality.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';

import { remoteImage } from './-lib/images';
import {
  bytes,
  countWords,
  deleteRule,
  devicesQuery,
  forgetSaved,
  offlineKeys,
  qualityWord,
  reportThisComputer,
  savedFiles,
  seenAgo,
  sendWish,
  thisDeviceId,
  THIS,
  type Device,
  type DeviceItem,
  type GroupType,
  type Rule,
  type SavedFile,
  type Wish,
} from './-lib/offline';
import { useLocalJobs } from './-lib/offline-jobs';
import { DownloadSheet } from './-ui/download-sheet';
import p from './-ui/downloads-page.module.css';
import {
  BookIcon,
  LaptopIcon,
  MovieIcon,
  MusicIcon,
  PhoneIcon,
  PodcastIcon,
  SparkIcon,
  TvIcon,
} from './-ui/icons';
import { Art, PageTitle } from './-ui/kit';
import { notify, notifyError } from './-ui/notice';
import { ConfirmSheet } from './-ui/sheet';

export const Route = createFileRoute('/player/downloads')({
  component: DownloadsPage,
});

type Shelf = 'movies' | 'tv' | 'music' | 'books' | 'podcasts';
type Filter = 'all' | Shelf;
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'tv', label: 'TV' },
  { id: 'movies', label: 'Movies' },
  { id: 'music', label: 'Music' },
  { id: 'books', label: 'Books' },
  { id: 'podcasts', label: 'Podcasts' },
];
const SHELF_WORDS: Record<Shelf, string> = {
  tv: 'TV',
  movies: 'Movies',
  music: 'Music',
  books: 'Books',
  podcasts: 'Podcasts',
};

function shelfOf(t: string): Shelf {
  if (t === 'movie' || t === 'youtube') return 'movies';
  if (t === 'episode' || t === 'season' || t === 'show') return 'tv';
  if (t === 'audiobook') return 'books';
  if (t === 'podcast_episode' || t === 'podcast') return 'podcasts';
  return 'music';
}

const SHELF_ICON: Record<Shelf, () => React.ReactNode> = {
  tv: TvIcon,
  movies: MovieIcon,
  music: MusicIcon,
  books: BookIcon,
  podcasts: PodcastIcon,
};

/** a small picture, or the kind's icon when there isn't one */
function Pic({ src, alt, shelf }: { src?: string | null; alt: string; shelf: Shelf }) {
  const url = remoteImage(src);
  const Icon = SHELF_ICON[shelf];
  return url ? (
    <Art src={url} alt={alt} />
  ) : (
    <span className={p.noArt} aria-hidden="true">
      <Icon />
    </span>
  );
}

// ── grouping ────────────────────────────────────────────────────────────

interface Group {
  key: string;
  /** what the sheet and a remove act on */
  type: GroupType;
  id: string;
  title: string;
  artwork?: string | null;
  shelf: Shelf;
  items: DeviceItem[];
  /** a show's seasons on the device ("12:2"), for "add the rest" */
  seasons: string[];
}

/** a device's items as the page's groups: seasons fold into their show */
function groupItems(items: DeviceItem[]): Group[] {
  const out = new Map<string, Group>();
  for (const it of items) {
    const g = it.group;
    let type: GroupType =
      g?.type ?? (it.item_type === 'podcast_episode' ? 'podcast' : it.item_type);
    let id = g?.id ?? it.item_id;
    if (type === 'season') {
      type = 'show';
      id = (g?.id ?? '').split(':')[0] || id;
    }
    const key = `${type}:${id}`;
    let group = out.get(key);
    if (!group) {
      group = {
        key,
        type,
        id,
        title: g?.title || it.title || 'Untitled',
        artwork: g?.artwork ?? it.artwork,
        shelf: shelfOf(it.item_type),
        items: [],
        seasons: [],
      };
      out.set(key, group);
    }
    group.items.push(it);
    if (g?.type === 'season' && !group.seasons.includes(g.id)) group.seasons.push(g.id);
  }
  const order: Shelf[] = ['tv', 'movies', 'music', 'books', 'podcasts'];
  return [...out.values()].sort(
    (a, b) => order.indexOf(a.shelf) - order.indexOf(b.shelf) || a.title.localeCompare(b.title),
  );
}

function phoneWord(device: Device) {
  return /tablet|\btab\b|ipad/i.test(device.device_name) ? 'tablet' : 'phone';
}

function stateWords(it: DeviceItem, device: Device, removing: boolean) {
  if (removing) return { text: 'Removing', tone: 'muted' };
  if (it.state === 'downloading')
    return { text: `Downloading ${Math.round((it.progress ?? 0) * 100)}%`, tone: 'busy' };
  if (it.state === 'queued') return { text: 'Waiting', tone: 'muted' };
  if (it.state === 'paused') return { text: 'Paused', tone: 'muted' };
  if (it.state === 'failed') return { text: 'Failed', tone: 'bad' };
  if (it.watched) return { text: 'Watched', tone: 'watched' };
  return { text: `On the ${phoneWord(device)}`, tone: 'ok' };
}

function ruleWords(r: Rule): string {
  const bits: string[] = [];
  if (r.type === 'playlist') bits.push(r.follow === false ? 'kept as it was' : 'stays in step');
  else if (r.keep_next)
    bits.push(r.type === 'show' ? `next ${r.keep_next} unwatched` : `newest ${r.keep_next}`);
  if (r.delete_watched) bits.push(r.type === 'show' ? 'deletes watched' : 'deletes played');
  bits.push(qualityWord(r.quality));
  return bits.join(' · ');
}

const sizeOf = (list: DeviceItem[]) => list.reduce((n, it) => n + (it.size_bytes || 0), 0);

function nounFor(g: Group) {
  if (g.type === 'show') return 'episode';
  if (g.type === 'podcast') return 'podcast';
  return undefined;
}

// ── storage ─────────────────────────────────────────────────────────────

function deviceRoom(device: Device) {
  const used = device.used_bytes ?? sizeOf(device.items);
  // no limit set on the phone: just what it holds
  const cap = device.limit_bytes || 0;
  return { used, cap };
}

function StorageBar({ device }: { device: Device }) {
  const { used, cap } = deviceRoom(device);
  // the bar's full width: the limit, else what the phone has room for
  const scale = cap || used + (device.free_bytes ?? 0);
  const by = new Map<Shelf, number>();
  for (const it of device.items) {
    const shelf = shelfOf(it.item_type);
    by.set(shelf, (by.get(shelf) ?? 0) + (it.size_bytes || 0));
  }
  const parts = [...by.entries()].filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  return (
    <div className={p.storage}>
      <div className={p.storageHead}>
        <span>On {device.device_name}</span>
        <b>
          {bytes(used) || '0 B'}
          <span className={p.dim}>
            {cap
              ? ` of ${bytes(cap)}`
              : device.free_bytes != null
                ? ` · ${bytes(device.free_bytes) || '0 B'} free`
                : ' · No limit'}
          </span>
        </b>
      </div>
      <div className={p.bar} role="img" aria-label={`${bytes(used) || '0 B'} used`}>
        {parts.map(([shelf, n]) => (
          <i
            key={shelf}
            data-shelf={shelf}
            style={{ width: `${Math.max(1, (n / Math.max(scale, 1)) * 100)}%` }}
          />
        ))}
      </div>
      {parts.length || device.wifi_only ? (
        <div className={p.legend}>
          {parts.map(([shelf, n]) => (
            <span key={shelf}>
              <b data-shelf={shelf} />
              {SHELF_WORDS[shelf]} {bytes(n)}
            </span>
          ))}
          {device.wifi_only ? <span className={p.wifi}>Wi-Fi only</span> : null}
        </div>
      ) : null}
    </div>
  );
}

// ── one device ──────────────────────────────────────────────────────────

type SheetAsk = { type: GroupType; id: string; title: string };

function GroupBlock({
  g,
  device,
  rule,
  removing,
  onSheet,
  onRemove,
  onRemoveAll,
}: {
  g: Group;
  device: Device;
  rule?: Rule;
  removing: Set<string>;
  onSheet: (ask: SheetAsk) => void;
  onRemove: (type: string, id: string) => void;
  onRemoveAll: (g: Group) => void;
}) {
  const first = g.items[0];
  const one = g.items.length === 1 && g.type !== 'show' && g.type !== 'podcast';
  const groupGone = removing.has(`${g.type}:${g.id}`);
  const size = bytes(sizeOf(g.items));
  const sub = one
    ? [
        qualityWord(first.quality),
        size,
        first.position_s && !first.watched && first.duration_s
          ? `watched ${Math.round((first.position_s / first.duration_s) * 100)}%`
          : null,
      ]
    : [
        `${countWords(g.items.length, first.kind ?? 'video', nounFor(g))} on ${device.device_name}`,
        size,
      ];
  const st = stateWords(first, device, groupGone);
  const square = g.shelf === 'music' || g.shelf === 'podcasts' || g.shelf === 'books';
  const oneSeason = g.type === 'show' && g.seasons.length === 1 ? g.seasons[0] : null;
  return (
    <section className={p.group} aria-label={g.title}>
      <div className={p.ghead}>
        <span className={p.art} data-shape={square ? 'square' : 'poster'}>
          <Art src={remoteImage(g.artwork)} alt={g.title} />
        </span>
        <span className={p.gtext}>
          <b>{g.title}</b>
          <span>{sub.filter(Boolean).join(' · ')}</span>
          {rule ? (
            <span className={p.smart}>
              <SparkIcon /> Smart · {ruleWords(rule)}
            </span>
          ) : null}
          {one ? (
            <span className={p.state} data-tone={st.tone}>
              {st.text}
            </span>
          ) : null}
        </span>
        <span className={p.acts}>
          {!one ? (
            <button
              type="button"
              className={p.pill}
              onClick={() =>
                onSheet(
                  oneSeason
                    ? { type: 'season', id: oneSeason, title: g.title }
                    : { type: g.type, id: g.id, title: g.title },
                )
              }
              data-nav
            >
              {oneSeason
                ? `Add the rest of season ${oneSeason.split(':')[1] ?? ''}`
                : 'Add the rest'}
            </button>
          ) : null}
          {g.type === 'show' || g.type === 'playlist' || g.type === 'podcast' ? (
            <button
              type="button"
              className={p.pill}
              onClick={() => onSheet({ type: g.type, id: g.id, title: g.title })}
              data-nav
            >
              Smart settings
            </button>
          ) : null}
          <button
            type="button"
            className={p.pill}
            data-tone="danger"
            disabled={groupGone}
            onClick={() => (one ? onRemove(first.item_type, first.item_id) : onRemoveAll(g))}
            data-nav
          >
            {one ? (first.state === 'downloaded' ? 'Remove' : 'Cancel') : 'Remove all'}
          </button>
        </span>
      </div>
      {one && first.state === 'downloading' ? (
        <span className={p.prog} data-wide="true">
          <i style={{ width: `${Math.round((first.progress ?? 0) * 100)}%` }} />
        </span>
      ) : null}
      {!one ? (
        <div className={p.items}>
          {g.items.map((it) => {
            const gone = groupGone || removing.has(`${it.item_type}:${it.item_id}`);
            const ist = stateWords(it, device, gone);
            const going = it.state !== 'downloaded';
            return (
              <div key={`${it.item_type}:${it.item_id}`} className={p.item}>
                <span className={p.thumb} data-shape={square ? 'small' : 'wide'}>
                  <Pic
                    src={it.artwork ?? (square ? g.artwork : null)}
                    alt={it.title ?? ''}
                    shelf={g.shelf}
                  />
                </span>
                <span className={p.rowText}>
                  <b>{it.title || it.item_id}</b>
                  <span>
                    {[qualityWord(it.quality), bytes(it.size_bytes)].filter(Boolean).join(' · ')}
                  </span>
                  {it.state === 'downloading' ? (
                    <span className={p.prog}>
                      <i style={{ width: `${Math.round((it.progress ?? 0) * 100)}%` }} />
                    </span>
                  ) : null}
                </span>
                <span className={p.state} data-tone={ist.tone}>
                  {ist.text}
                </span>
                <button
                  type="button"
                  className={p.pill}
                  disabled={gone}
                  onClick={() => onRemove(it.item_type, it.item_id)}
                  data-nav
                >
                  {going ? 'Cancel' : 'Remove'}
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

function DeviceView({ device }: { device: Device }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>('all');
  const [sheet, setSheet] = useState<SheetAsk | null>(null);
  const [confirm, setConfirm] = useState<Group | null>(null);
  const wishes = device.wishes ?? [];
  const removing = new Set(
    wishes.filter((w) => w.action === 'remove').map((w) => `${w.type}:${w.id}`),
  );
  const adds = wishes.filter((w) => w.action === 'add');
  const all = groupItems(device.items);
  const groups = all.filter((g) => filter === 'all' || g.shelf === filter);
  const shelves = new Set(all.map((g) => g.shelf));
  const ruleFor = (g: Group) => device.rules.find((r) => r.type === g.type && r.id === g.id);
  // rules with nothing on the device yet still show, so they can be changed
  const looseRules = device.rules.filter(
    (r) => !all.some((g) => g.type === r.type && g.id === r.id),
  );

  const refresh = () => queryClient.invalidateQueries({ queryKey: offlineKeys.devices });
  const remove = useMutation({
    mutationFn: ({ type, id }: { type: string; id: string }) =>
      sendWish(device.device_id, 'remove', type, id),
    onSuccess: () => {
      notify(`It comes off ${device.device_name} the next time it's online.`);
      void refresh();
    },
    onError: notifyError("Couldn't remove that."),
  });
  const cancelWish = useMutation({
    mutationFn: (w: Wish) => sendWish(device.device_id, 'remove', w.type, w.id),
    onSuccess: () => void refresh(),
    onError: notifyError("Couldn't cancel that."),
  });
  const ruleOff = useMutation({
    mutationFn: (r: Rule) => deleteRule(r.rule_id),
    onSuccess: () => {
      notify('Smart download off. What it downloaded stays.');
      void refresh();
    },
    onError: notifyError("Couldn't turn that off."),
  });

  return (
    <div className={p.body}>
      <StorageBar device={device} />

      {shelves.size > 1 ? (
        <div className={p.chips} role="radiogroup" aria-label="Show">
          {FILTERS.filter((f) => f.id === 'all' || shelves.has(f.id)).map((f) => (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={filter === f.id}
              className={p.chip}
              onClick={() => setFilter(f.id)}
              data-nav
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : null}

      {adds.length ? (
        <section className={p.section} aria-label="Waiting">
          <h2 className={p.sectionTitle}>Waiting for {device.device_name}</h2>
          {adds.map((w) => (
            <div key={w.wish_id} className={p.row}>
              <span className={p.thumb} data-shape="small">
                <Pic src={w.artwork} alt={w.title ?? ''} shelf={shelfOf(w.type)} />
              </span>
              <span className={p.rowText}>
                <b>{w.title || 'Something you sent'}</b>
                <span>{[w.subtitle, qualityWord(w.quality)].filter(Boolean).join(' · ')}</span>
              </span>
              <span className={p.state} data-tone="muted">
                Next time it's online
              </span>
              <button
                type="button"
                className={p.pill}
                disabled={cancelWish.isPending}
                onClick={() => cancelWish.mutate(w)}
                data-nav
              >
                Cancel
              </button>
            </div>
          ))}
        </section>
      ) : null}

      {looseRules.length ? (
        <section className={p.section} aria-label="Smart downloads">
          <h2 className={p.sectionTitle}>Smart downloads</h2>
          {looseRules.map((r) => (
            <div key={r.rule_id} className={p.row}>
              <span className={p.thumb} data-shape="small">
                <Pic
                  src={r.artwork}
                  alt={r.title ?? ''}
                  shelf={r.type === 'show' ? 'tv' : r.type === 'podcast' ? 'podcasts' : 'music'}
                />
              </span>
              <span className={p.rowText}>
                <b>{r.title || 'Smart download'}</b>
                <span className={p.smart}>
                  <SparkIcon /> {ruleWords(r)}
                </span>
              </span>
              <span className={p.acts}>
                <button
                  type="button"
                  className={p.pill}
                  onClick={() => setSheet({ type: r.type, id: r.id, title: r.title ?? '' })}
                  data-nav
                >
                  Change
                </button>
                <button
                  type="button"
                  className={p.pill}
                  disabled={ruleOff.isPending}
                  onClick={() => ruleOff.mutate(r)}
                  data-nav
                >
                  Turn off
                </button>
              </span>
            </div>
          ))}
        </section>
      ) : null}

      {groups.map((g) => (
        <GroupBlock
          key={g.key}
          g={g}
          device={device}
          rule={ruleFor(g)}
          removing={removing}
          onSheet={setSheet}
          onRemove={(type, id) => remove.mutate({ type, id })}
          onRemoveAll={setConfirm}
        />
      ))}

      {!all.length && !adds.length && !looseRules.length ? (
        <div className={p.empty}>
          <b>Nothing on {device.device_name} yet</b>
          <span>
            Open a movie, a show, an album or a playlist, press Download and pick{' '}
            {device.device_name}. It downloads the next time the app is open.
          </span>
        </div>
      ) : null}

      <p className={p.note}>
        Changes reach {device.device_name} the next time it's online
        {device.last_seen ? ` (${seenAgo(device.last_seen)})` : ''}.
      </p>

      {sheet ? (
        <DownloadSheet
          type={sheet.type}
          id={sheet.id}
          title={sheet.title}
          device={device.device_id}
          onClose={() => {
            setSheet(null);
            void refresh();
          }}
        />
      ) : null}
      {confirm ? (
        <ConfirmSheet
          title={`Remove ${confirm.title}?`}
          confirm="Remove all"
          danger
          busy={remove.isPending}
          onClose={() => setConfirm(null)}
          onConfirm={() =>
            remove.mutate(
              { type: confirm.type, id: confirm.id },
              { onSettled: () => setConfirm(null) },
            )
          }
        >
          {countWords(confirm.items.length, confirm.items[0]?.kind ?? 'video', nounFor(confirm))} (
          {bytes(sizeOf(confirm.items))}) come off {device.device_name} the next time it's online.
          {ruleFor(confirm)
            ? ' Its smart download stays on; turn it off in Smart settings to stop new ones.'
            : ''}
        </ConfirmSheet>
      ) : null}
    </div>
  );
}

// ── this computer ───────────────────────────────────────────────────────

function ThisComputer() {
  const jobs = useLocalJobs();
  const [files, setFiles] = useState<SavedFile[]>(() => savedFiles());
  const [sheet, setSheet] = useState<SavedFile | null>(null);
  const [forget, setForget] = useState<SavedFile | null>(null);
  useEffect(() => {
    // a finished job adds a file: read the list again
    setFiles(savedFiles());
  }, [jobs]);
  const working = jobs.filter((j) => j.status !== 'done');
  const total = files.reduce((n, f) => n + (f.size_bytes || 0), 0);

  return (
    <div className={p.body}>
      <div className={p.storage} hidden={!files.length}>
        <div className={p.storageHead}>
          <span>Saved from this browser</span>
          <b>
            {files.length} file{files.length === 1 ? '' : 's'}
            {total ? <span className={p.dim}> · {bytes(total)}</span> : null}
          </b>
        </div>
      </div>

      {working.length ? (
        <section className={p.section} aria-label="Getting ready">
          <h2 className={p.sectionTitle}>Getting ready</h2>
          {working.map((j) => (
            <div key={j.key} className={p.row}>
              <span className={p.thumb} data-shape="small">
                <Pic src={j.artwork} alt={j.title} shelf={shelfOf(j.item_type)} />
              </span>
              <span className={p.rowText}>
                <b>{j.title}</b>
                <span>{qualityWord(j.quality)}</span>
                {j.status === 'converting' ? (
                  <span className={p.prog}>
                    <i style={{ width: `${Math.round(j.progress * 100)}%` }} />
                  </span>
                ) : null}
              </span>
              <span className={p.state} data-tone={j.status === 'failed' ? 'bad' : 'busy'}>
                {j.status === 'failed'
                  ? (j.error ?? 'Failed')
                  : j.status === 'converting'
                    ? `Converting ${Math.round(j.progress * 100)}%`
                    : j.status === 'saving'
                      ? 'Saving'
                      : 'Waiting'}
              </span>
            </div>
          ))}
        </section>
      ) : null}

      {files.length ? (
        <section className={p.section} aria-label="Saved files">
          <h2 className={p.sectionTitle}>Saved files</h2>
          {files.map((f) => (
            <div key={`${f.item_type}:${f.item_id}`} className={p.row}>
              <span
                className={p.thumb}
                data-shape={f.item_type === 'episode' || f.item_type === 'movie' ? 'wide' : 'small'}
              >
                <Pic src={f.artwork} alt={f.title} shelf={shelfOf(f.item_type)} />
              </span>
              <span className={p.rowText}>
                <b>{f.title}</b>
                <span>
                  {[f.subtitle, qualityWord(f.quality), bytes(f.size_bytes)]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
              <span className={p.acts}>
                <button type="button" className={p.pill} onClick={() => setSheet(f)} data-nav>
                  Download again
                </button>
                <button type="button" className={p.pill} onClick={() => setForget(f)} data-nav>
                  Forget
                </button>
              </span>
            </div>
          ))}
        </section>
      ) : !working.length ? (
        <div className={p.empty}>
          <b>Nothing saved here yet</b>
          <span>
            Press Download on a movie, an episode or an album and pick This computer. The file lands
            in your browser's downloads.
          </span>
        </div>
      ) : null}

      <p className={p.note}>
        Files stay where your browser saved them. Forget only takes one off this list.
      </p>

      {sheet ? (
        <DownloadSheet
          type={sheet.item_type === 'podcast_episode' ? 'podcast' : sheet.item_type}
          id={sheet.item_type === 'podcast_episode' ? sheet.item_id.split(':')[0] : sheet.item_id}
          title={sheet.title}
          onClose={() => setSheet(null)}
        />
      ) : null}
      {forget ? (
        <ConfirmSheet
          title={`Forget ${forget.title}?`}
          confirm="Forget"
          onClose={() => setForget(null)}
          onConfirm={() => {
            forgetSaved(forget.item_type, forget.item_id);
            setFiles(savedFiles());
            setForget(null);
            void reportThisComputer().catch(() => {});
          }}
        >
          It comes off this list. The file you saved stays on this computer.
        </ConfirmSheet>
      ) : null}
    </div>
  );
}

// ── the page ────────────────────────────────────────────────────────────

function DownloadsPage() {
  const devices = useQuery(devicesQuery());
  const mine = thisDeviceId();
  // this browser shows as This computer, not as one of the devices
  const list = useMemo(
    () => (devices.data ?? []).filter((x) => x.device_id !== mine && x.platform !== 'web'),
    [devices.data, mine],
  );
  const [picked, setPicked] = useState<string | null>(null);
  const current = picked ?? list[0]?.device_id ?? THIS;
  const device = list.find((x) => x.device_id === current);
  const saved = savedFiles();

  return (
    <>
      <PageTitle>Downloads</PageTitle>
      <div className={p.devices} role="radiogroup" aria-label="Device">
        {list.map((x) => {
          const { used, cap } = deviceRoom(x);
          const fresh = seenAgo(x.last_seen);
          return (
            <button
              key={x.device_id}
              type="button"
              role="radio"
              aria-checked={current === x.device_id}
              className={p.device}
              onClick={() => setPicked(x.device_id)}
              data-nav
            >
              <span className={p.deviceIcon}>
                <PhoneIcon />
                {fresh === 'online now' ? <span className={p.live} title="Online now" /> : null}
              </span>
              <span className={p.deviceText}>
                <b>{x.device_name}</b>
                <span>
                  {cap ? `${bytes(used) || '0 B'} of ${bytes(cap)}` : bytes(used) || 'Empty'}
                  {fresh && fresh !== 'online now' ? ` · ${fresh}` : ''}
                </span>
                {cap ? (
                  <span className={p.mini}>
                    <i style={{ width: `${Math.min(100, (used / cap) * 100)}%` }} />
                  </span>
                ) : null}
              </span>
            </button>
          );
        })}
        <button
          type="button"
          role="radio"
          aria-checked={current === THIS}
          className={p.device}
          onClick={() => setPicked(THIS)}
          data-nav
        >
          <span className={p.deviceIcon}>
            <LaptopIcon />
          </span>
          <span className={p.deviceText}>
            <b>This computer</b>
            <span>
              {saved.length
                ? `${saved.length} saved file${saved.length === 1 ? '' : 's'}`
                : 'Saved files'}
            </span>
          </span>
        </button>
      </div>

      {devices.isLoading ? (
        <div className={p.skeleton} />
      ) : current === THIS || !device ? (
        <ThisComputer />
      ) : (
        <DeviceView key={device.device_id} device={device} />
      )}

      {!devices.isLoading && !list.length ? (
        <p className={p.note}>
          Get the SoulSync app on your phone or tablet to keep downloads there. Once it's signed in,
          it shows up here and you can send things to it from any screen.
        </p>
      ) : null}
    </>
  );
}
