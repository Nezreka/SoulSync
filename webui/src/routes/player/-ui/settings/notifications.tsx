/**
 * settings, you, notifications: what this profile hears about, when, and
 * whether this browser shows them. the switches are the profile's (every
 * screen and the phone app read the same ones); browser permission is this
 * browser's.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';

import {
  notificationPrefsQuery,
  notificationsQuery,
  saveNotificationPrefs,
  sendTestNotification,
  type NotificationKindInfo,
  type NotificationPrefs,
  type NotificationPrefsChange,
} from '../../-player.api';
import { notifyError } from '../notice';
import { Card, Row, SaveError, Switch, useSaved } from './kit';
import p from './settings.module.css';

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function browserZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

type Permission = NotificationPermission | 'unsupported';

function readPermission(): Permission {
  return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
}

const PERMISSION_HELP: Record<Permission, string> = {
  granted: 'On. New ones pop up while the player is in a background tab.',
  default: 'Pop up new ones while the player is in a background tab.',
  denied: "Blocked for this site. Allow notifications in the browser's site settings.",
  unsupported: "This browser can't show notifications. The bell still keeps them.",
};

function TimeField({
  label,
  value,
  disabled,
  onSave,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onSave: (v: string) => void;
}) {
  return (
    <input
      type="time"
      className={p.time}
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(e) => {
        if (HHMM.test(e.target.value) && e.target.value !== value) onSave(e.target.value);
      }}
      data-nav
    />
  );
}

function BrowserCard() {
  const queryClient = useQueryClient();
  const [permission, setPermission] = useState<Permission>(readPermission);
  const [tested, setTested] = useState(false);
  const test = useMutation({
    mutationFn: sendTestNotification,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationsQuery().queryKey });
      setTested(true);
      if (permission === 'granted') {
        try {
          new Notification('Notifications are working', {
            body: 'This is what a SoulSync notification looks like.',
            tag: 'ss-test',
          });
        } catch {
          /* the bell has it */
        }
      }
    },
    onError: notifyError("Couldn't send a test"),
  });
  const ask = async () => {
    if (typeof Notification === 'undefined') return;
    try {
      setPermission(await Notification.requestPermission());
    } catch {
      setPermission(readPermission());
    }
  };
  return (
    <Card label="This browser">
      <Row label="Browser notifications" help={PERMISSION_HELP[permission]}>
        {permission === 'default' ? (
          <button type="button" className={p.pill} data-tone="go" onClick={ask} data-nav>
            Turn on
          </button>
        ) : (
          <span className={p.state} data-on={permission === 'granted' ? 'true' : undefined}>
            {permission === 'granted' ? 'On' : 'Off'}
          </span>
        )}
      </Row>
      <Row
        label="Send a test"
        help={
          tested
            ? 'Sent. It is in the bell at the top.'
            : 'See what one looks like, here and on your phone.'
        }
      >
        <button
          type="button"
          className={p.pill}
          disabled={test.isPending}
          onClick={() => test.mutate()}
          data-nav
        >
          {test.isPending ? 'Sending…' : 'Send test'}
        </button>
      </Row>
    </Card>
  );
}

function kindHelp(k: NotificationKindInfo): string {
  if (k.device === 'phone') return `${k.help} Each phone can turn it off for itself in the app.`;
  return k.help;
}

export function NotificationsSection() {
  const queryClient = useQueryClient();
  const onSaved = useSaved();
  const q = useQuery(notificationPrefsQuery());
  const save = useMutation({
    mutationFn: (changes: NotificationPrefsChange) =>
      saveNotificationPrefs({ ...changes, timezone: browserZone() ?? '' }),
    onSuccess: (next) => {
      queryClient.setQueryData(notificationPrefsQuery().queryKey, next);
      onSaved();
    },
    onError: notifyError("Couldn't save that setting"),
  });
  const prefs: NotificationPrefs | undefined = q.data?.prefs;
  const kinds = q.data?.kinds ?? [];
  const busy = !prefs || save.isPending;
  const on = (id: string) => !!prefs?.kinds[id];

  if (q.isError && !q.data) {
    return (
      <Card>
        <div className={p.saveError} role="alert">
          Couldn't load your notification settings. Try again in a moment.
        </div>
      </Card>
    );
  }

  return (
    <>
      <BrowserCard />

      <h3 className={p.subhead}>What you hear about</h3>
      <Card label="What you hear about">
        {kinds.map((k) => (
          <Fragment key={k.id}>
            <Row label={k.label} help={kindHelp(k)}>
              <Switch
                label={k.label}
                on={k.locked ? true : on(k.id)}
                busy={busy || k.locked}
                onChange={(v) => save.mutate({ kinds: { [k.id]: v } })}
              />
            </Row>
            {k.id === 'featured_movie' && on(k.id) ? (
              <Row
                label="Also films not in your library"
                help="Well-reviewed films just out in cinemas, even before the server has them."
              >
                <Switch
                  label="Also films not in your library"
                  on={!!prefs?.featured_not_in_library}
                  busy={busy}
                  onChange={(v) => save.mutate({ featured_not_in_library: v })}
                />
              </Row>
            ) : null}
            {k.id === 'home_memories' && on(k.id) ? (
              <Row label="Memories arrive at" help="Once a day, only when there are some.">
                <TimeField
                  label="Memories arrive at"
                  value={prefs?.memories_time ?? '09:00'}
                  disabled={busy}
                  onSave={(v) => save.mutate({ memories_time: v })}
                />
              </Row>
            ) : null}
          </Fragment>
        ))}
        <SaveError show={save.isError} />
      </Card>

      <h3 className={p.subhead}>When</h3>
      <Card label="When">
        <Row
          label="Quiet hours"
          help="Hold notifications overnight and send them when quiet hours end. Admin messages still come through."
        >
          <Switch
            label="Quiet hours"
            on={!!prefs?.quiet_hours}
            busy={busy}
            onChange={(v) => save.mutate({ quiet_hours: v })}
          />
        </Row>
        {prefs?.quiet_hours ? (
          <Row stacked label="Quiet from" help="On your own clock.">
            <span className={p.times}>
              <TimeField
                label="Quiet hours start"
                value={prefs.quiet_start}
                disabled={busy}
                onSave={(v) => save.mutate({ quiet_start: v })}
              />
              <span className={p.timesTo}>to</span>
              <TimeField
                label="Quiet hours end"
                value={prefs.quiet_end}
                disabled={busy}
                onSave={(v) => save.mutate({ quiet_end: v })}
              />
            </span>
          </Row>
        ) : null}
        <Row
          label="Daily digest"
          help="One notification a day with everything new, instead of one each. Invites and admin messages still come straight away."
        >
          <Switch
            label="Daily digest"
            on={!!prefs?.digest}
            busy={busy}
            onChange={(v) => save.mutate({ digest: v })}
          />
        </Row>
        {prefs?.digest ? (
          <Row label="Send the digest at">
            <TimeField
              label="Send the digest at"
              value={prefs.digest_time}
              disabled={busy}
              onSave={(v) => save.mutate({ digest_time: v })}
            />
          </Row>
        ) : null}
      </Card>
    </>
  );
}
