/**
 * reminders: a program you asked to be told about. when it starts (or a
 * minute before), whichever screen of the profile is open says so, with
 * tune in; the browser's own notification too when it's allowed. kept on
 * the server, so a reminder set on the tv rings on the phone.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type { Reminder } from '../../-player.api';

import { clock } from '../../-lib/format';
import { reminderEdits, remindersQuery } from '../../-player.api';
import { inPlayer } from '../trailer';
import s from './playback.module.css';
import { channelByIdTune } from './tuner';

const SHOWN_KEY = 'ss-player:reminders-shown';

function shownSet(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(SHOWN_KEY) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
}

function markShown(key: string) {
  const set = shownSet();
  set.add(key);
  try {
    sessionStorage.setItem(SHOWN_KEY, JSON.stringify([...set].slice(-100)));
  } catch {
    /* fine */
  }
}

const keyOf = (r: Reminder) => `${r.channel_id}|${r.starts_at}`;

/** ask once, from a click: the browser's notifications for reminders */
export function askToNotify() {
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default')
      void Notification.requestPermission();
  } catch {
    /* no notifications here */
  }
}

/** a reminder's toggle, for a program that hasn't started */
export function useReminder(channelId: string, startsAt: number) {
  const queryClient = useQueryClient();
  const list = useQuery(remindersQuery());
  const on = !!list.data?.some((r) => r.channel_id === channelId && r.starts_at === startsAt);
  const toggle = async (
    program: { key: string; title: string; subtitle?: string },
    channelName: string,
  ) => {
    if (on) await reminderEdits.remove(channelId, startsAt);
    else {
      askToNotify();
      await reminderEdits.add({
        channel_id: channelId,
        starts_at: startsAt,
        program_key: program.key,
        title: program.title,
        subtitle: program.subtitle,
        channel_name: channelName,
      });
    }
    await queryClient.invalidateQueries({ queryKey: ['player', 'guide', 'reminders'] });
  };
  return { on, toggle };
}

/** mounted once: rings a reminder when its program starts */
export function ReminderBell() {
  const list = useQuery({ ...remindersQuery(), refetchInterval: 60_000 });
  const [ringing, setRinging] = useState<Reminder | null>(null);

  useEffect(() => {
    const check = () => {
      const now = Date.now() / 1000;
      const shown = shownSet();
      const due = (list.data ?? []).find(
        (r) => r.starts_at - 60 <= now && now < r.starts_at + 600 && !shown.has(keyOf(r)),
      );
      if (!due) return;
      markShown(keyOf(due));
      setRinging(due);
      try {
        if (
          typeof Notification !== 'undefined' &&
          Notification.permission === 'granted' &&
          document.hidden
        )
          new Notification(`${due.title} is starting`, {
            body: due.channel_name ? `On ${due.channel_name}` : undefined,
            tag: keyOf(due),
          });
      } catch {
        /* the in-player one is enough */
      }
    };
    check();
    const t = window.setInterval(check, 15_000);
    return () => window.clearInterval(t);
  }, [list.data]);

  if (!ringing) return null;
  return inPlayer(
    <div className={s.reminder} role="alert">
      <span className={s.reminderText}>
        <span className={s.bannerChannel}>
          {ringing.channel_name ? `On ${ringing.channel_name} · ` : ''}
          {clock(ringing.starts_at * 1000)}
        </span>
        <span className={s.bannerTitle}>{ringing.title} is starting</span>
        {ringing.subtitle ? <span className={s.bannerSub}>{ringing.subtitle}</span> : null}
      </span>
      <button
        type="button"
        className={s.reminderTune}
        onClick={() => {
          channelByIdTune(ringing.channel_id);
          setRinging(null);
        }}
        data-nav
      >
        Tune in
      </button>
      <button type="button" className={s.reminderClose} onClick={() => setRinging(null)} data-nav>
        Not now
      </button>
    </div>,
  );
}
