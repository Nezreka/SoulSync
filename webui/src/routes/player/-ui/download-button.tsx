/**
 * the download button on every page with something to keep: it says where
 * the thing is (Download, Waiting, 60%, Downloaded, On Pixel 8) and opens
 * the download sheet. hidden for a profile whose downloads are off.
 */

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import type { DownloadItemType } from '../-lib/downloads';

import {
  devicesQuery,
  GROUP_OF,
  itemKey,
  offlineSettingsQuery,
  savedFiles,
  type Device,
  type DeviceItem,
  type GroupType,
} from '../-lib/offline';
import { useLocalJobs, type LocalJob } from '../-lib/offline-jobs';
import { DownloadSheet } from './download-sheet';
import d from './download-sheet.module.css';
import { CheckIcon, DownloadIcon } from './icons';
import { styles as s } from './kit';

/** does this device item belong to that group */
export function inGroup(it: DeviceItem, type: GroupType, id: string): boolean {
  if (it.item_type === type && it.item_id === id) return true;
  const g = it.group;
  if (!g) return false;
  if (g.type === type && g.id === id) return true;
  // a show holds its seasons
  return type === 'show' && g.type === 'season' && g.id.split(':')[0] === id;
}

export interface ButtonState {
  tone: 'idle' | 'busy' | 'on';
  label: string;
  progress?: number;
}

/** what the button says, from the devices, this computer's jobs and its saved files */
export function buttonState(
  type: GroupType,
  id: string,
  devices: Device[],
  jobs: LocalJob[],
  saved: { item_type: string; item_id: string }[],
): ButtonState {
  const key = itemKey(type, id);
  const active = jobs.filter(
    (j) => j.status !== 'done' && j.status !== 'failed' && (j.key === key || j.group === key),
  );
  if (active.length) {
    const j = active[0];
    if (j.status === 'converting')
      return { tone: 'busy', label: `${Math.round(j.progress * 100)}%`, progress: j.progress };
    return { tone: 'busy', label: j.status === 'saving' ? 'Saving' : 'Waiting' };
  }
  for (const dev of devices) {
    const mine = dev.items.filter((it) => inGroup(it, type, id));
    const going = mine.find((it) => it.state === 'downloading');
    if (going) {
      const pct = Math.round((going.progress ?? 0) * 100);
      return { tone: 'busy', label: `${pct}%`, progress: going.progress ?? 0 };
    }
    if (mine.some((it) => it.state === 'queued' || it.state === 'paused'))
      return { tone: 'busy', label: 'Waiting' };
  }
  for (const dev of devices) {
    if ((dev.wishes ?? []).some((w) => w.action === 'add' && w.type === type && w.id === id))
      return { tone: 'busy', label: `Waiting for ${dev.device_name}` };
  }
  const holders = devices.filter(
    (dev) =>
      dev.platform !== 'web' &&
      dev.items.some((it) => it.state === 'downloaded' && inGroup(it, type, id)),
  );
  if (holders.length === 1) return { tone: 'on', label: `On ${holders[0].device_name}` };
  if (holders.length > 1) return { tone: 'on', label: `On ${holders.length} devices` };
  if (saved.some((f) => f.item_type === type && f.item_id === id))
    return { tone: 'on', label: 'Downloaded' };
  return { tone: 'idle', label: 'Download' };
}

export function DownloadButton({
  type,
  id,
  title,
  compact = false,
  small = false,
  label = 'Download',
  why,
}: {
  type: GroupType;
  id: string;
  title: string;
  compact?: boolean;
  /** a pill for a section head, not a hero button */
  small?: boolean;
  label?: string;
  /** there's nothing to download yet: the button shows, greyed, saying why */
  why?: string;
}) {
  const [open, setOpen] = useState(false);
  const settings = useQuery(offlineSettingsQuery());
  const devices = useQuery(devicesQuery());
  const jobs = useLocalJobs();
  if (settings.data && !settings.data.allow_downloads && !settings.data.is_admin) return null;
  if (why) {
    return (
      <button
        type="button"
        className={`${s.btn} ${d.button}${small ? ` ${d.small}` : ''}`}
        data-state="idle"
        disabled
        title={why}
        aria-label={`${label}: ${why}`}
        data-nav
      >
        <DownloadIcon />
        {label}
      </button>
    );
  }
  const st = buttonState(type, id, devices.data ?? [], jobs, savedFiles());
  const words =
    st.tone === 'idle'
      ? label
      : st.progress != null && !compact
        ? `Downloading ${st.label}`
        : st.label;
  const icon = st.tone === 'on' ? <CheckIcon /> : <DownloadIcon />;
  const openSheet = (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen(true);
  };
  return (
    <>
      {compact ? (
        <button
          type="button"
          className={d.icon}
          data-state={st.tone}
          aria-label={`${words}: ${title}`}
          title={words}
          onClick={openSheet}
          data-nav
        >
          {st.tone === 'busy' && st.progress != null ? (
            <span className={d.pct}>{Math.round(st.progress * 100)}</span>
          ) : (
            icon
          )}
        </button>
      ) : (
        <button
          type="button"
          className={`${s.btn} ${d.button}${small ? ` ${d.small}` : ''}`}
          data-state={st.tone}
          aria-label={`${words}: ${title}`}
          onClick={openSheet}
          data-nav
        >
          {icon}
          {words}
        </button>
      )}
      {open ? (
        <DownloadSheet type={type} id={id} title={title} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

/** the older name, kept for the pages that already place it */
export function DownloadMediaButton({
  itemType,
  itemId,
  title,
  compact = false,
  label,
}: {
  itemType: DownloadItemType;
  itemId: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  directUrl?: string;
  compact?: boolean;
  label?: string;
}) {
  return (
    <DownloadButton
      type={GROUP_OF[itemType]}
      id={itemId}
      title={title}
      compact={compact}
      label={label}
    />
  );
}
