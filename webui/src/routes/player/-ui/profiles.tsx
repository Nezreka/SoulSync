/**
 * who's watching, and switching profile from the chip.
 *
 * both use the manager's own switch: POST /api/profiles/select, which checks
 * the pin or password itself, then a full reload, exactly as the manager's
 * picker does. nothing here decides who may open a profile; the server does.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { appURL } from '@/platform/url-base';

import type { PlayerProfile } from '../-player.types';

import { profilesQuery, selectProfile } from '../-player.api';
import { ExitIcon, LockIcon } from './icons';
import s from './player.module.css';

/** the manager's "always open as me on this device" choice */
const OPEN_AS_KEY = 'ss_open_as_profile';
const PASSED_KEY = 'ss-player-who';
const PICKED_AT_KEY = 'ss-profile-picked-at';
/** a pick in the manager's picker this recently counts as answering the gate */
const FRESH_PICK_MS = 2 * 60 * 1000;

function readStore(store: Storage | undefined, key: string): string | null {
  try {
    return store?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeStore(store: Storage | undefined, key: string, value: string) {
  try {
    store?.setItem(key, value);
  } catch {
    /* private mode: the gate just asks again */
  }
}

export function offeredProfiles(profiles: readonly PlayerProfile[]): PlayerProfile[] {
  return profiles.filter((p) => !(p.disabled && !p.is_admin));
}

/** should the gate show for this profile on entering /player? */
export function gateNeeded(
  profiles: readonly PlayerProfile[],
  currentId: number | null,
  now = Date.now(),
  session: Storage | undefined = globalThis.sessionStorage,
  local: Storage | undefined = globalThis.localStorage,
): boolean {
  if (offeredProfiles(profiles).length <= 1 || currentId === null) return false;
  if (readStore(session, PASSED_KEY) === String(currentId)) return false;
  const openAs = Number(readStore(local, OPEN_AS_KEY));
  if (openAs && openAs === currentId) return false;
  const pickedAt = Number(readStore(session, PICKED_AT_KEY));
  if (pickedAt && now - pickedAt < FRESH_PICK_MS) return false;
  return true;
}

export function markGatePassed(profileId: number) {
  writeStore(globalThis.sessionStorage, PASSED_KEY, String(profileId));
}

export function Avatar({ profile, className }: { profile: PlayerProfile; className?: string }) {
  const [imgOk, setImgOk] = useState(true);
  const src = profile.avatar_url ? appURL(profile.avatar_url) : null;
  return (
    <span
      className={className ?? s.avatar}
      style={
        profile.avatar_color
          ? ({ '--avatar-bg': profile.avatar_color } as React.CSSProperties)
          : undefined
      }
      aria-hidden="true"
    >
      {src && imgOk ? (
        <img src={src} alt="" onError={() => setImgOk(false)} />
      ) : (
        (profile.name || '?').trim().charAt(0).toUpperCase()
      )}
    </span>
  );
}

/** pick a profile; asks for its pin or password when the server wants one */
function usePicker(currentId: number | null, onStay: () => void) {
  const [asking, setAsking] = useState<{
    profile: PlayerProfile;
    needs: 'pin' | 'password';
  } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function pick(profile: PlayerProfile, secret?: { pin?: string; password?: string }) {
    if (profile.id === currentId && !secret) {
      markGatePassed(profile.id);
      onStay();
      return;
    }
    setBusy(true);
    setError('');
    const result = await selectProfile(profile.id, secret);
    setBusy(false);
    if (result.ok) {
      markGatePassed(profile.id);
      window.location.reload();
      return;
    }
    if (result.needs) {
      setAsking({ profile, needs: result.needs });
      setError(result.error ?? '');
      return;
    }
    if (asking) setError(result.error);
    else setError(`${profile.name}: ${result.error}`);
  }

  return { asking, setAsking, error, setError, busy, pick };
}

function SecretForm({
  profile,
  needs,
  error,
  busy,
  onSubmit,
  onCancel,
}: {
  profile: PlayerProfile;
  needs: 'pin' | 'password';
  error: string;
  busy: boolean;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value) onSubmit(value);
  };
  return (
    <form className={s.secretForm} onSubmit={submit}>
      <Avatar profile={profile} className={s.gateAvatar} />
      <div>{needs === 'pin' ? `Enter ${profile.name}'s PIN` : `${profile.name}'s password`}</div>
      <input
        ref={inputRef}
        className={s.secretInput}
        type="password"
        inputMode={needs === 'pin' ? 'numeric' : undefined}
        autoComplete={needs === 'pin' ? 'off' : 'current-password'}
        aria-label={needs === 'pin' ? 'PIN' : 'Password'}
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className={s.secretError} role="alert">
        {error}
      </div>
      <div className={s.heroActions}>
        <button type="button" className={s.btn} onClick={onCancel} data-nav>
          Back
        </button>
        <button type="submit" className={s.btnPrimary} disabled={busy || !value} data-nav>
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </div>
    </form>
  );
}

/** the full-screen who's-watching picker */
export function WhoGate({
  profiles,
  currentId,
  onDone,
}: {
  profiles: PlayerProfile[];
  currentId: number | null;
  onDone: () => void;
}) {
  const offered = offeredProfiles(profiles);
  const { asking, setAsking, error, setError, busy, pick } = usePicker(currentId, onDone);
  const firstRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!asking) firstRef.current?.focus();
  }, [asking]);

  return (
    <div className={s.gate} role="dialog" aria-modal="true" aria-label="Who's watching?">
      <div className={s.gateInner}>
        {asking ? (
          <SecretForm
            profile={asking.profile}
            needs={asking.needs}
            error={error}
            busy={busy}
            onSubmit={(value) =>
              void pick(
                asking.profile,
                asking.needs === 'pin' ? { pin: value } : { password: value },
              )
            }
            onCancel={() => {
              setAsking(null);
              setError('');
            }}
          />
        ) : (
          <>
            <h1 className={s.gateTitle}>Who's watching?</h1>
            <div className={s.gateGrid}>
              {offered.map((profile, index) => (
                <button
                  key={profile.id}
                  ref={index === 0 ? firstRef : undefined}
                  type="button"
                  className={s.gateProfile}
                  onClick={() => void pick(profile)}
                  disabled={busy}
                  data-nav
                >
                  <Avatar profile={profile} className={s.gateAvatar} />
                  {profile.name}
                  {profile.has_pin || profile.has_password ? (
                    <span className={s.gateLock}>
                      <LockIcon /> locked
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
            <div className={s.secretError} role="alert">
              {error}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** the header chip: who you are, and the switch */
export function ProfileChip({ currentId }: { currentId: number | null }) {
  const { data: profiles = [] } = useQuery(profilesQuery());
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const me = profiles.find((p) => p.id === currentId);
  const others = offeredProfiles(profiles).filter((p) => p.id !== currentId);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const esc = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);

  return (
    <div className={s.chip} ref={ref}>
      <button
        type="button"
        className={s.chipButton}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-nav
      >
        {me ? <Avatar profile={me} /> : <span className={s.avatar} />}
        <span>{me?.name ?? 'Profile'}</span>
      </button>
      {open ? (
        <div className={s.menu} role="menu">
          {others.length ? (
            <>
              <div className={s.menuLabel}>Switch profile</div>
              <button
                type="button"
                role="menuitem"
                className={s.menuItem}
                onClick={() => {
                  setOpen(false);
                  setSwitching(true);
                }}
                data-nav
              >
                Who's watching…
              </button>
              <div className={s.menuSep} />
            </>
          ) : null}
          <a role="menuitem" className={s.menuItem} href={appURL('/')} data-nav>
            <ExitIcon /> Back to SoulSync
          </a>
        </div>
      ) : null}
      {switching ? (
        <WhoGate profiles={profiles} currentId={currentId} onDone={() => setSwitching(false)} />
      ) : null}
    </div>
  );
}
