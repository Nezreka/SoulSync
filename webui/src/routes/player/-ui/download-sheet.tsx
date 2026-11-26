/**
 * the download sheet: where it goes (this computer or one of the profile's
 * phones), in what quality (sizes from the server's plan), and for shows,
 * playlists and podcasts the smart part (keep the next few ready, delete
 * what's been watched, keep a playlist in step). this computer prepares each
 * file on the server and hands it to the browser; a phone gets a wish it
 * picks up the next time it's online.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import {
  bytes,
  countWords,
  deleteRule,
  devicesQuery,
  itemKey,
  offlineKeys,
  planQuery,
  qualityWord,
  rememberedQuality,
  rememberQuality,
  saveRule,
  sendWish,
  THIS,
  type Device,
  type GroupType,
  type Plan,
  type RuleType,
} from '../-lib/offline';
import { saveLocally, useLocalJobs } from '../-lib/offline-jobs';
import d from './download-sheet.module.css';
import { LaptopIcon, PhoneIcon, SparkIcon } from './icons';
import { notify } from './notice';
import { Sheet } from './sheet';

/** the rule a group can carry: a season's smart download is its show's */
export function ruleTarget(type: GroupType, id: string): { type: RuleType; id: string } | null {
  if (type === 'show') return { type: 'show', id };
  if (type === 'season') return { type: 'show', id: id.split(':')[0] ?? id };
  if (type === 'playlist') return { type: 'playlist', id };
  if (type === 'podcast') return { type: 'podcast', id };
  return null;
}

const KEEP_STEPS = [1, 3, 5];

function Toggle({
  label,
  help,
  on,
  onChange,
}: {
  label: string;
  help?: string;
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className={d.toggle}>
      <span className={d.toggleText} onClick={() => onChange(!on)} aria-hidden="true">
        <span>{label}</span>
        {help ? <span className={d.toggleHelp}>{help}</span> : null}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        className={d.switch}
        onClick={() => onChange(!on)}
        data-nav
      />
    </div>
  );
}

export function remoteDevices(devices: Device[] | undefined): Device[] {
  return (devices ?? []).filter((x) => x.platform !== 'web');
}

export function DownloadSheet({
  type,
  id,
  title,
  device: startDevice,
  onClose,
}: {
  type: GroupType;
  id: string;
  title?: string;
  /** a device to start on (the downloads page's), else this computer */
  device?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const devices = useQuery(devicesQuery());
  const phones = remoteDevices(devices.data);
  const [device, setDevice] = useState(startDevice ?? THIS);
  const plan = useQuery(planQuery(type, id, device));
  const p: Plan | undefined = plan.data;
  const target = device === THIS ? null : phones.find((x) => x.device_id === device);

  const [quality, setQuality] = useState<string | null>(null);
  const qualities = p?.qualities ?? [];
  const picked =
    qualities.find((q) => q.id === quality) ??
    qualities.find((q) => q.id === p?.rule?.quality) ??
    qualities.find((q) => q.id === (p ? rememberedQuality(p.kind) : null)) ??
    qualities[0];

  // smart downloads, from the device's rule when it has one
  const rt = ruleTarget(type, id);
  const [keepOn, setKeepOn] = useState(false);
  const [keepNext, setKeepNext] = useState(3);
  const [deleteWatched, setDeleteWatched] = useState(false);
  const [follow, setFollow] = useState(true);
  const rule = p?.rule ?? null;
  useEffect(() => {
    if (!rule) {
      setKeepOn(false);
      setDeleteWatched(false);
      setFollow(true);
      return;
    }
    setKeepOn(Boolean(rule.keep_next));
    if (rule.keep_next) setKeepNext(rule.keep_next);
    setDeleteWatched(Boolean(rule.delete_watched));
    setFollow(rule.follow ?? true);
  }, [rule]);

  const smart = Boolean(target && rt);
  // a whole show or podcast with keep-next is the rule alone (wishing all of
  // it would undo the point); a season still downloads, with the rule beside it
  const keeping = smart && keepOn && (type === 'show' || type === 'podcast');
  const items = p?.items ?? [];
  const todo = target ? items.filter((it) => !it.on_device) : items;
  const per = picked?.per_item_bytes ?? {};
  const sizeOf = (t: string, i: string) => per[itemKey(t, i)] ?? 0;
  const avg = items.length ? (picked?.total_bytes ?? 0) / items.length : 0;
  const total = keeping
    ? Math.round(avg * keepNext)
    : todo.reduce((n, it) => n + sizeOf(it.item_type, it.item_id), 0);
  const kind = p?.kind ?? 'video';

  const jobs = useLocalJobs();
  const mine = useMemo(() => {
    const keys = new Set(items.map((it) => itemKey(it.item_type, it.item_id)));
    return jobs.filter((j) => keys.has(j.key));
  }, [jobs, items]);
  const working = mine.filter((j) => j.status !== 'done' && j.status !== 'failed');

  const [busy, setBusy] = useState(false);
  const free = p?.free_bytes ?? target?.free_bytes ?? null;
  const limit = p?.limit_bytes ?? target?.limit_bytes ?? null;
  const short = free != null && total > free ? total - free : 0;

  const goLabel = (() => {
    const size = bytes(total);
    if (keeping) {
      return `Keep the next ${keepNext} ready${size ? ` · ${size}` : ''}`;
    }
    if (!todo.length) return target ? `All on ${target.device_name}` : 'Nothing to download';
    const what =
      todo.length === 1 && items.length === 1 ? '' : ` ${countWords(todo.length, kind, type)}`;
    return `Download${what}${size ? ` · ${size}` : ''}`;
  })();
  const canGo = !busy && !!picked && (keeping || todo.length > 0 || (smart && !!rule));

  const go = async () => {
    if (!p || !picked) return;
    rememberQuality(p.kind, picked.id);
    if (!target) {
      saveLocally(
        todo.map((it) => ({
          item_type: it.item_type,
          item_id: it.item_id,
          title: it.title,
          subtitle: it.subtitle ?? p.title,
          artwork: it.artwork ?? p.artwork,
          quality: picked.id,
          group: itemKey(type, id),
          groupInfo: { type, id, title: p.title, subtitle: p.subtitle, artwork: p.artwork },
        })),
      );
      return;
    }
    setBusy(true);
    try {
      const wantRule = rt?.type === 'playlist' ? follow : keepOn || deleteWatched;
      if (smart && rt && !wantRule && rule) {
        // every smart part switched off: the rule goes
        await deleteRule(rule.rule_id);
      } else if (smart && rt && wantRule) {
        await saveRule(target.device_id, {
          type: rt.type,
          id: rt.id,
          quality: picked.id,
          keep_next: rt.type === 'playlist' ? undefined : keeping ? keepNext : 0,
          delete_watched: rt.type === 'playlist' ? undefined : deleteWatched,
          follow: rt.type === 'playlist' ? follow : undefined,
        });
      }
      if (!keeping && todo.length) {
        await sendWish(target.device_id, 'add', type, id, picked.id);
      }
      await queryClient.invalidateQueries({ queryKey: offlineKeys.all });
      notify(
        keeping
          ? `Smart download on for ${target.device_name}.`
          : `Sent to ${target.device_name}. It downloads the next time it's online.`,
      );
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't send that.", 'error');
    } finally {
      setBusy(false);
    }
  };

  const heading = p?.title ?? title ?? 'Download';
  const subtitle = p
    ? [
        p.subtitle,
        items.length > 1 ? countWords(items.length, kind, type) : null,
        target && items.length > 1 && todo.length < items.length
          ? `${todo.length} not on ${target.device_name}`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined;

  return (
    <Sheet
      title={heading}
      subtitle={subtitle || undefined}
      onClose={onClose}
      footer={
        <div className={d.foot}>
          {working.length ? (
            <div className={d.progressNote} role="status">
              {working[0].status === 'converting'
                ? `Converting ${working[0].title} · ${Math.round(working[0].progress * 100)}%`
                : working[0].status === 'saving'
                  ? `Saving ${working[0].title}`
                  : `Getting ${working[0].title} ready`}
              {working.length > 1 ? ` · ${working.length - 1} more after it` : ''}
              <span className={d.bar}>
                <span
                  className={d.barFill}
                  style={{ width: `${Math.round((working[0].progress || 0) * 100)}%` }}
                />
              </span>
            </div>
          ) : null}
          <button
            type="button"
            className={d.go}
            disabled={!canGo || (!target && working.length > 0)}
            onClick={() => void go()}
            data-nav
          >
            {busy
              ? 'Sending…'
              : smart && rule && !keeping && !todo.length
                ? 'Save smart download'
                : goLabel}
          </button>
          {target && free != null ? (
            <span className={d.fits} data-short={short ? 'true' : undefined}>
              {short
                ? `Needs ${bytes(short)} more than the ${bytes(free)} free on ${target.device_name}`
                : `Fits: ${bytes(free) || '0 B'} free${limit ? ` under your ${bytes(limit)} limit` : ''}`}
            </span>
          ) : !target ? (
            <span className={d.fits}>Saved by your browser, one file at a time.</span>
          ) : null}
        </div>
      }
    >
      <div className={d.section}>
        <div className={d.label}>Download to</div>
        <div className={d.devices} role="radiogroup" aria-label="Download to">
          {[
            { id: THIS, name: 'This computer', sub: 'Your browser saves it', phone: false },
            ...phones.map((x) => ({
              id: x.device_id,
              name: x.device_name,
              sub: x.free_bytes != null ? `${bytes(x.free_bytes) || '0 B'} free` : 'Phone',
              phone: true,
            })),
          ].map((x) => (
            <button
              key={x.id}
              type="button"
              role="radio"
              aria-checked={device === x.id}
              className={d.device}
              onClick={() => setDevice(x.id)}
              data-nav
            >
              <span className={d.deviceIcon}>{x.phone ? <PhoneIcon /> : <LaptopIcon />}</span>
              <span className={d.deviceText}>
                <b>{x.name}</b>
                <span>{x.sub}</span>
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className={d.section}>
        <div className={d.label}>Quality</div>
        {plan.isLoading ? (
          <div className={d.skeleton} aria-label="Loading sizes" />
        ) : plan.isError ? (
          <p className={d.error}>{plan.error.message}</p>
        ) : (
          <div className={d.options} role="radiogroup" aria-label="Quality">
            {qualities.map((q) => (
              <button
                key={q.id}
                type="button"
                role="radio"
                aria-checked={picked?.id === q.id}
                className={d.option}
                onClick={() => setQuality(q.id)}
                data-nav
              >
                <span className={d.radio} aria-hidden="true" />
                <span className={d.optionText}>
                  <b>{q.label || qualityWord(q.id)}</b>
                  {q.sub ? <span>{q.sub}</span> : null}
                </span>
                <span className={d.size}>{bytes(q.total_bytes)}</span>
              </button>
            ))}
            {p?.conversion_off ? (
              <p className={d.note}>Smaller copies are off for your profile. Ask an admin.</p>
            ) : null}
          </div>
        )}
      </div>

      {smart && rt ? (
        <div className={d.section}>
          <div className={d.label}>
            <SparkIcon /> Smart download
          </div>
          {rt.type === 'playlist' ? (
            <Toggle
              label="Keep in step with the playlist"
              help="Songs added later download, songs taken out are removed."
              on={follow}
              onChange={setFollow}
            />
          ) : (
            <>
              <Toggle
                label={
                  rt.type === 'show'
                    ? `Keep the next ${keepNext} unwatched ready`
                    : `Keep the newest ${keepNext} ready`
                }
                help={
                  rt.type === 'show'
                    ? 'As you watch, the next ones download on their own.'
                    : 'New episodes download as they come out.'
                }
                on={keepOn}
                onChange={setKeepOn}
              />
              {keepOn ? (
                <div className={d.steps} role="radiogroup" aria-label="How many">
                  {KEEP_STEPS.map((n) => (
                    <button
                      key={n}
                      type="button"
                      role="radio"
                      aria-checked={keepNext === n}
                      className={d.step}
                      onClick={() => setKeepNext(n)}
                      data-nav
                    >
                      {n}
                    </button>
                  ))}
                </div>
              ) : null}
              <Toggle
                label={
                  rt.type === 'show'
                    ? "Delete an episode once it's watched"
                    : 'Delete an episode once played'
                }
                on={deleteWatched}
                onChange={setDeleteWatched}
              />
            </>
          )}
        </div>
      ) : null}
    </Sheet>
  );
}
