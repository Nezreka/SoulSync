/**
 * downloads: pick a device and see what's on it, from any screen. what's
 * still coming sits on top; the rest is filed by kind (tv shows with their
 * seasons, movies, youtube, music by album and playlist, audiobooks,
 * podcasts, home videos), each section folding away, newest first or by
 * name. every card says what it is doing and what can be done about it (add
 * the rest, smart settings, remove). changes reach the phone the next time
 * it's online. "this computer" files what this browser saved the same way,
 * each downloadable again in another quality.
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
import {
  organize,
  SECTIONS,
  sectionOf,
  stamp,
  type Card,
  type Section,
  type SectionId,
  type SortBy,
} from './-lib/organize';
import { DownloadSheet } from './-ui/download-sheet';
import p from './-ui/downloads-page.module.css';
import {
  BookIcon,
  CamcorderIcon,
  ChevronDownIcon,
  LaptopIcon,
  MovieIcon,
  MusicIcon,
  PhoneIcon,
  PodcastIcon,
  SparkIcon,
  TvIcon,
  YoutubeIcon,
} from './-ui/icons';
import { Art, PageTitle } from './-ui/kit';
import { notify, notifyError } from './-ui/notice';
import { ConfirmSheet } from './-ui/sheet';

export const Route = createFileRoute('/player/downloads')({
  component: DownloadsPage,
});

type Filter = 'all' | SectionId;

const SECTION_ICON: Record<SectionId, () => React.ReactNode> = {
  tv: TvIcon,
  movies: MovieIcon,
  youtube: YoutubeIcon,
  music: MusicIcon,
  books: BookIcon,
  podcasts: PodcastIcon,
  home: CamcorderIcon,
};

const SECTION_WORDS = Object.fromEntries(SECTIONS.map((x) => [x.id, x.label])) as Record<
  SectionId,
  string
>;

/** a small picture, or the kind's icon when there isn't one */
function Pic({ src, alt, shelf }: { src?: string | null; alt: string; shelf: SectionId }) {
  const url = remoteImage(src);
  const Icon = SECTION_ICON[shelf];
  return url ? (
    <Art src={url} alt={alt} />
  ) : (
    <span className={p.noArt} aria-hidden="true">
      <Icon />
    </span>
  );
}

/** the card's picture shape: posters for films and shows, squares for sound, wide for clips */
function artShape(shelf: SectionId): 'poster' | 'square' | 'wide' {
  if (shelf === 'tv' || shelf === 'movies') return 'poster';
  if (shelf === 'youtube' || shelf === 'home') return 'wide';
  return 'square';
}

// ── how the page is sorted and folded (this browser remembers) ──────────

const PREFS_KEY = 'ss-downloads-view';

function readPrefs(): { sort: SortBy; folded: SectionId[] } {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as {
      sort?: unknown;
      folded?: unknown;
    };
    return {
      sort: raw.sort === 'az' ? 'az' : 'recent',
      folded: Array.isArray(raw.folded)
        ? (raw.folded.filter((x) => SECTIONS.some((s) => s.id === x)) as SectionId[])
        : [],
    };
  } catch {
    return { sort: 'recent', folded: [] };
  }
}

function usePrefs() {
  const [prefs, setPrefs] = useState(readPrefs);
  const save = (next: typeof prefs) => {
    setPrefs(next);
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(next));
    } catch {
      // no storage: this visit only
    }
  };
  return {
    sort: prefs.sort,
    setSort: (sort: SortBy) => save({ ...prefs, sort }),
    folded: (id: SectionId) => prefs.folded.includes(id),
    toggle: (id: SectionId) =>
      save({
        ...prefs,
        folded: prefs.folded.includes(id)
          ? prefs.folded.filter((x) => x !== id)
          : [...prefs.folded, id],
      }),
  };
}

/** the kind chips (all, or one section) and newest first or a to z */
function Toolbar({
  sections,
  filter,
  onFilter,
  sort,
  onSort,
}: {
  sections: Section<unknown>[];
  filter: Filter;
  onFilter: (f: Filter) => void;
  sort: SortBy;
  onSort: (s: SortBy) => void;
}) {
  if (!sections.length) return null;
  return (
    <div className={p.toolbar}>
      {sections.length > 1 ? (
        <div className={p.chips} role="radiogroup" aria-label="Show">
          {[{ id: 'all' as Filter, label: 'All' }, ...sections].map((f) => (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={filter === f.id}
              className={p.chip}
              onClick={() => onFilter(f.id)}
              data-nav
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className={p.sortBy} role="radiogroup" aria-label="Sort">
        {(
          [
            ['recent', 'Recent'],
            ['az', 'A-Z'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={sort === id}
            className={p.chip}
            onClick={() => onSort(id)}
            data-nav
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** a kind's heading: its name, how many and how big; tap to fold it away */
function KindSection({
  section,
  folded,
  onToggle,
  noun,
  children,
}: {
  section: Section<unknown>;
  folded: boolean;
  onToggle: () => void;
  noun: (n: number) => string;
  children: React.ReactNode;
}) {
  const Icon = SECTION_ICON[section.id];
  const bodyId = `dl-kind-${section.id}`;
  return (
    <section className={p.kind} aria-label={section.label} data-kind={section.id}>
      <button
        type="button"
        className={p.kindHead}
        aria-expanded={!folded}
        aria-controls={bodyId}
        onClick={onToggle}
        data-nav
      >
        <span className={p.kindIcon} data-shelf={section.id}>
          <Icon />
        </span>
        <b>{section.label}</b>
        <span className={p.kindCount}>
          {noun(section.count)}
          {section.bytes ? ` · ${bytes(section.bytes)}` : ''}
        </span>
        <span className={p.chev} aria-hidden="true">
          <ChevronDownIcon />
        </span>
      </button>
      {folded ? null : (
        <div id={bodyId} className={p.kindBody}>
          {children}
        </div>
      )}
    </section>
  );
}

/** "12 episodes", "3 songs": what a section holds, in its own words */
function kindNoun(id: SectionId) {
  const word =
    id === 'tv' || id === 'podcasts'
      ? 'episode'
      : id === 'movies'
        ? 'movie'
        : id === 'books'
          ? 'book'
          : id === 'music'
            ? 'song'
            : 'video';
  return (n: number) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
}

/** the sections a filter shows */
function shownSections<E>(all: Section<E>[], filter: Filter) {
  return filter === 'all' ? all : all.filter((x) => x.id === filter);
}

// ── a phone's downloads ─────────────────────────────────────────────────

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

const sizeOf = (list: { size_bytes?: number | null }[]) =>
  list.reduce((n, it) => n + (it.size_bytes || 0), 0);

type DeviceCard = Card<DeviceItem>;

function nounFor(c: DeviceCard) {
  if (c.type === 'show') return 'episode';
  if (c.type === 'podcast') return 'podcast';
  return undefined;
}

/** a show's episodes under their season, anything else as one list */
function Rows<E>({ card, row }: { card: Card<E>; row: (it: E) => React.ReactNode }) {
  if (card.type === 'show' && card.seasons.length) {
    return (
      <div className={p.items}>
        {card.seasons.map((season) => (
          <div key={season.id || 'loose'} className={p.season}>
            <span className={p.seasonTitle}>
              {season.label}
              <span className={p.dim}>
                {' '}
                · {season.items.length} episode{season.items.length === 1 ? '' : 's'}
              </span>
            </span>
            {season.items.map(row)}
          </div>
        ))}
      </div>
    );
  }
  return <div className={p.items}>{card.items.map(row)}</div>;
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
  const by = new Map<SectionId, number>();
  for (const it of device.items) {
    const shelf = sectionOf(it.item_type);
    by.set(shelf, (by.get(shelf) ?? 0) + (it.size_bytes || 0));
  }
  const parts = SECTIONS.map((x) => [x.id, by.get(x.id) ?? 0] as const).filter(([, n]) => n > 0);
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
              {SECTION_WORDS[shelf]} {bytes(n)}
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
  shelf,
  device,
  rule,
  removing,
  onSheet,
  onRemove,
  onRemoveAll,
}: {
  g: DeviceCard;
  shelf: SectionId;
  device: Device;
  rule?: Rule;
  removing: Set<string>;
  onSheet: (ask: SheetAsk) => void;
  onRemove: (type: string, id: string) => void;
  onRemoveAll: (g: DeviceCard) => void;
}) {
  const first = g.items[0];
  const one =
    g.items.length === 1 && g.type !== 'show' && g.type !== 'podcast' && g.type !== 'songs';
  const groupGone = removing.has(`${g.type}:${g.id}`);
  const size = bytes(g.bytes);
  const seasons = g.seasons.filter((x) => x.id);
  const sub = one
    ? [
        g.subtitle,
        qualityWord(first.quality),
        size,
        first.position_s && !first.watched && first.duration_s
          ? `watched ${Math.round((first.position_s / first.duration_s) * 100)}%`
          : null,
      ]
    : [
        g.type === 'playlist' ? 'Playlist' : g.type === 'album' ? g.subtitle || 'Album' : null,
        seasons.length > 1 ? `${seasons.length} seasons` : null,
        `${countWords(g.items.length, first.kind ?? 'video', nounFor(g))} on ${device.device_name}`,
        size,
      ];
  const st = stateWords(first, device, groupGone);
  const shape = artShape(shelf);
  const oneSeason = g.type === 'show' && seasons.length === 1 ? seasons[0].id : null;
  const groupType = g.type === 'songs' ? null : g.type;
  return (
    <section className={p.group} aria-label={g.title}>
      <div className={p.ghead}>
        <span className={p.art} data-shape={shape}>
          {g.artwork ? (
            <Art src={remoteImage(g.artwork)} alt={g.title} />
          ) : (
            <Pic alt={g.title} shelf={shelf} />
          )}
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
          {!one && groupType ? (
            <button
              type="button"
              className={p.pill}
              onClick={() =>
                onSheet(
                  oneSeason
                    ? { type: 'season', id: oneSeason, title: g.title }
                    : { type: groupType, id: g.id, title: g.title },
                )
              }
              data-nav
            >
              {oneSeason
                ? `Add the rest of season ${oneSeason.split(':')[1] ?? ''}`
                : 'Add the rest'}
            </button>
          ) : null}
          {groupType === 'show' || groupType === 'playlist' || groupType === 'podcast' ? (
            <button
              type="button"
              className={p.pill}
              onClick={() => onSheet({ type: groupType, id: g.id, title: g.title })}
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
            {one ? 'Remove' : 'Remove all'}
          </button>
        </span>
      </div>
      {!one ? (
        <Rows
          card={g}
          row={(it) => {
            const gone = groupGone || removing.has(`${it.item_type}:${it.item_id}`);
            const ist = stateWords(it, device, gone);
            return (
              <div key={`${it.item_type}:${it.item_id}`} className={p.item}>
                <span className={p.thumb} data-shape={shape === 'square' ? 'small' : 'wide'}>
                  <Pic
                    src={it.artwork ?? (shape === 'square' ? g.artwork : null)}
                    alt={it.title ?? ''}
                    shelf={shelf}
                  />
                </span>
                <span className={p.rowText}>
                  <b>{it.title || it.item_id}</b>
                  <span>
                    {[g.type === 'songs' ? it.subtitle : null, qualityWord(it.quality)]
                      .concat(bytes(it.size_bytes))
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
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
                  Remove
                </button>
              </div>
            );
          }}
        />
      ) : null}
    </section>
  );
}

function DeviceView({ device }: { device: Device }) {
  const queryClient = useQueryClient();
  const prefs = usePrefs();
  const [filter, setFilter] = useState<Filter>('all');
  const [sheet, setSheet] = useState<SheetAsk | null>(null);
  const [confirm, setConfirm] = useState<DeviceCard | null>(null);
  const wishes = device.wishes ?? [];
  const removing = new Set(
    wishes.filter((w) => w.action === 'remove').map((w) => `${w.type}:${w.id}`),
  );
  const adds = wishes.filter((w) => w.action === 'add');
  // still coming: on top, with its progress; what's there is filed by kind
  const coming = device.items.filter((it) => it.state !== 'downloaded');
  const sections = organize(
    device.items.filter((it) => it.state === 'downloaded'),
    (it) => stamp(it.downloaded_at),
    prefs.sort,
  );
  const shown = shownSections(sections, filter);
  const ruleFor = (g: DeviceCard) => device.rules.find((r) => r.type === g.type && r.id === g.id);
  // rules with nothing on the device yet still show, so they can be changed
  const looseRules = device.rules.filter(
    (r) =>
      !sections.some((x) => x.cards.some((g) => g.type === r.type && g.id === r.id)) &&
      !coming.some((it) => it.group?.type === r.type && it.group.id === r.id),
  );

  const refresh = () => queryClient.invalidateQueries({ queryKey: offlineKeys.devices });
  const remove = useMutation({
    mutationFn: async (targets: { type: string; id: string }[]) => {
      for (const t of targets) await sendWish(device.device_id, 'remove', t.type, t.id);
    },
    onSuccess: () => {
      notify(`It comes off ${device.device_name} the next time it's online.`);
      void refresh();
    },
    onError: notifyError("Couldn't remove that."),
  });
  const removeCard = (g: DeviceCard) =>
    // loose songs have no group of their own: each one goes
    g.type === 'songs'
      ? g.items.map((it) => ({ type: it.item_type, id: it.item_id }))
      : [{ type: g.type, id: g.id }];
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

      {coming.length ? (
        <section className={p.section} aria-label="Downloading">
          <h2 className={p.sectionTitle}>Downloading · {coming.length}</h2>
          {coming.map((it) => {
            const shelf = sectionOf(it.item_type);
            const gone = removing.has(`${it.item_type}:${it.item_id}`);
            const st = stateWords(it, device, gone);
            const from = it.group && it.group.type !== it.item_type ? it.group.title : null;
            return (
              <div key={`${it.item_type}:${it.item_id}`} className={p.row}>
                <span
                  className={p.thumb}
                  data-shape={artShape(shelf) === 'square' ? 'small' : 'wide'}
                >
                  <Pic src={it.artwork ?? it.group?.artwork} alt={it.title ?? ''} shelf={shelf} />
                </span>
                <span className={p.rowText}>
                  <b>{it.title || it.item_id}</b>
                  <span>
                    {[from, qualityWord(it.quality), bytes(it.size_bytes)]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                  {it.state === 'downloading' ? (
                    <span className={p.prog}>
                      <i style={{ width: `${Math.round((it.progress ?? 0) * 100)}%` }} />
                    </span>
                  ) : null}
                </span>
                <span className={p.state} data-tone={st.tone}>
                  {st.text}
                </span>
                <button
                  type="button"
                  className={p.pill}
                  disabled={gone}
                  onClick={() => remove.mutate([{ type: it.item_type, id: it.item_id }])}
                  data-nav
                >
                  Cancel
                </button>
              </div>
            );
          })}
        </section>
      ) : null}

      {adds.length ? (
        <section className={p.section} aria-label="Waiting">
          <h2 className={p.sectionTitle}>Waiting for {device.device_name}</h2>
          {adds.map((w) => (
            <div key={w.wish_id} className={p.row}>
              <span className={p.thumb} data-shape="small">
                <Pic src={w.artwork} alt={w.title ?? ''} shelf={sectionOf(w.type)} />
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

      <Toolbar
        sections={sections}
        filter={filter}
        onFilter={setFilter}
        sort={prefs.sort}
        onSort={prefs.setSort}
      />

      {shown.map((section) => (
        <KindSection
          key={section.id}
          section={section}
          // picking a kind opens it
          folded={filter === 'all' && prefs.folded(section.id)}
          onToggle={() => prefs.toggle(section.id)}
          noun={kindNoun(section.id)}
        >
          {section.cards.map((g) => (
            <GroupBlock
              key={g.key}
              g={g}
              shelf={section.id}
              device={device}
              rule={ruleFor(g)}
              removing={removing}
              onSheet={setSheet}
              onRemove={(type, id) => remove.mutate([{ type, id }])}
              onRemoveAll={setConfirm}
            />
          ))}
        </KindSection>
      ))}

      {!device.items.length && !adds.length && !looseRules.length ? (
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
            remove.mutate(removeCard(confirm), { onSettled: () => setConfirm(null) })
          }
        >
          {countWords(confirm.items.length, confirm.items[0]?.kind ?? 'video', nounFor(confirm))} (
          {bytes(confirm.bytes)}) come off {device.device_name} the next time it's online.
          {ruleFor(confirm)
            ? ' Its smart download stays on; turn it off in Smart settings to stop new ones.'
            : ''}
        </ConfirmSheet>
      ) : null}
    </div>
  );
}

// ── this computer ───────────────────────────────────────────────────────

function SavedCard({
  g,
  shelf,
  onAgain,
  onForget,
}: {
  g: Card<SavedFile>;
  shelf: SectionId;
  onAgain: (f: SavedFile) => void;
  onForget: (files: SavedFile[], title: string) => void;
}) {
  const first = g.items[0];
  const one = g.items.length === 1 && g.type !== 'show' && g.type !== 'songs';
  const shape = artShape(shelf);
  const sub = one
    ? [first.subtitle, qualityWord(first.quality), bytes(first.size_bytes)]
    : [
        g.type === 'playlist' ? 'Playlist' : g.type === 'album' ? g.subtitle || 'Album' : null,
        `${g.items.length} file${g.items.length === 1 ? '' : 's'}`,
        bytes(g.bytes),
      ];
  return (
    <section className={p.group} aria-label={g.title}>
      <div className={p.ghead}>
        <span className={p.art} data-shape={shape}>
          {g.artwork ? (
            <Art src={remoteImage(g.artwork)} alt={g.title} />
          ) : (
            <Pic alt={g.title} shelf={shelf} />
          )}
        </span>
        <span className={p.gtext}>
          <b>{g.title}</b>
          <span>{sub.filter(Boolean).join(' · ')}</span>
        </span>
        <span className={p.acts}>
          {one ? (
            <button type="button" className={p.pill} onClick={() => onAgain(first)} data-nav>
              Download again
            </button>
          ) : null}
          <button
            type="button"
            className={p.pill}
            onClick={() => onForget(g.items, g.title)}
            data-nav
          >
            {one ? 'Forget' : 'Forget all'}
          </button>
        </span>
      </div>
      {!one ? (
        <Rows
          card={g}
          row={(f) => (
            <div key={`${f.item_type}:${f.item_id}`} className={p.item}>
              <span className={p.thumb} data-shape={shape === 'square' ? 'small' : 'wide'}>
                <Pic
                  src={f.artwork ?? (shape === 'square' ? g.artwork : null)}
                  alt={f.title}
                  shelf={shelf}
                />
              </span>
              <span className={p.rowText}>
                <b>{f.title}</b>
                <span>
                  {[
                    g.type === 'songs' ? f.subtitle : null,
                    qualityWord(f.quality),
                    bytes(f.size_bytes),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
              <span className={p.acts}>
                <button type="button" className={p.pill} onClick={() => onAgain(f)} data-nav>
                  Download again
                </button>
                <button
                  type="button"
                  className={p.pill}
                  onClick={() => onForget([f], f.title)}
                  data-nav
                >
                  Forget
                </button>
              </span>
            </div>
          )}
        />
      ) : null}
    </section>
  );
}

function ThisComputer() {
  const jobs = useLocalJobs();
  const prefs = usePrefs();
  const [filter, setFilter] = useState<Filter>('all');
  const [files, setFiles] = useState<SavedFile[]>(() => savedFiles());
  const [sheet, setSheet] = useState<SavedFile | null>(null);
  const [forget, setForget] = useState<{ files: SavedFile[]; title: string } | null>(null);
  useEffect(() => {
    // a finished job adds a file: read the list again
    setFiles(savedFiles());
  }, [jobs]);
  const working = jobs.filter((j) => j.status !== 'done');
  const total = files.reduce((n, f) => n + (f.size_bytes || 0), 0);
  const sections = useMemo(
    () => organize(files, (f) => stamp(f.saved_at), prefs.sort),
    [files, prefs.sort],
  );
  const shown = shownSections(sections, filter);

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
                <Pic src={j.artwork} alt={j.title} shelf={sectionOf(j.item_type)} />
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

      <Toolbar
        sections={sections}
        filter={filter}
        onFilter={setFilter}
        sort={prefs.sort}
        onSort={prefs.setSort}
      />

      {shown.map((section) => (
        <KindSection
          key={section.id}
          section={section}
          folded={filter === 'all' && prefs.folded(section.id)}
          onToggle={() => prefs.toggle(section.id)}
          noun={(n) => `${n.toLocaleString()} file${n === 1 ? '' : 's'}`}
        >
          {section.cards.map((g) => (
            <SavedCard
              key={g.key}
              g={g}
              shelf={section.id}
              onAgain={setSheet}
              onForget={(list, title) => setForget({ files: list, title })}
            />
          ))}
        </KindSection>
      ))}

      {!files.length && !working.length ? (
        <div className={p.empty}>
          <b>Nothing saved here yet</b>
          <span>
            Press Download on a movie, an episode, an album or a playlist and pick This computer.
            The file lands in your browser's downloads.
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
            for (const f of forget.files) forgetSaved(f.item_type, f.item_id);
            setFiles(savedFiles());
            setForget(null);
            void reportThisComputer().catch(() => {});
          }}
        >
          {forget.files.length === 1
            ? 'It comes off this list. The file you saved stays on this computer.'
            : `${forget.files.length} files come off this list. The files you saved stay on this computer.`}
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
