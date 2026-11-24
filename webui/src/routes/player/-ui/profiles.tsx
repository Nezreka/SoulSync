/**
 * who's watching, and switching profile from the chip.
 *
 * both use the manager's own switch: POST /api/profiles/select, which checks
 * the pin or password itself, then a full reload, exactly as the manager's
 * picker does. nothing here decides who may open a profile; the server does.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { CardSizeSlider, type CardSizeRange } from '@/components/card-size';
import { appURL } from '@/platform/url-base';

import type { PlayerProfile } from '../-player.types';

import { currentProfileQuery, profilesQuery, selectProfile } from '../-player.api';
import { ExitIcon, GearIcon, LockIcon } from './icons';
import { getEngine } from './playback/use-player';
import s from './player.module.css';
import { inPlayer } from './trailer';

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
    // what's playing is this profile's: save it now, while the session is
    // still theirs. the reload after the switch saved it into the new one
    getEngine().saveNow();
    const result = await selectProfile(profile.id, secret);
    setBusy(false);
    if (result.ok) {
      markGatePassed(profile.id);
      // nothing more of the old profile's playback may save as the new one
      getEngine().stop({ save: false });
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
  initial,
}: {
  profiles: PlayerProfile[];
  currentId: number | null;
  onDone: () => void;
  /** switch straight to this profile (the menu's avatars): only its pin or password shows */
  initial?: PlayerProfile;
}) {
  const offered = offeredProfiles(profiles);
  const { asking, setAsking, error, setError, busy, pick } = usePicker(currentId, onDone);
  const firstRef = useRef<HTMLButtonElement>(null);
  const started = useRef(false);
  useEffect(() => {
    if (initial && !started.current) {
      started.current = true;
      void pick(initial);
    }
  }, [initial]);
  useEffect(() => {
    if (!asking && !initial) firstRef.current?.focus();
  }, [asking, initial]);

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
              if (initial) {
                onDone();
                return;
              }
              setAsking(null);
              setError('');
            }}
          />
        ) : initial ? (
          // the menu's quick switch: no picker, just where it's going
          <div className={s.gateSwitching} role="status">
            <Avatar profile={initial} className={s.gateAvatar} />
            <span>{error || `Switching to ${initial.name}…`}</span>
            {error ? (
              <button type="button" className={s.gateBack} onClick={onDone} data-nav>
                Back
              </button>
            ) : null}
          </div>
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

/** the header chip and its menu: who you are, who else, and this device's settings */
export function ProfileChip({
  currentId,
  cardSize,
  cardRange,
  onCardSize,
}: {
  currentId: number | null;
  cardSize: number | null;
  cardRange: CardSizeRange;
  onCardSize: (value: number | null) => void;
}) {
  const { data: profiles = [] } = useQuery(profilesQuery());
  const current = useQuery(currentProfileQuery());
  const [open, setOpen] = useState(false);
  const [switchTo, setSwitchTo] = useState<PlayerProfile | 'pick' | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const me = profiles.find((p) => p.id === currentId);
  const others = offeredProfiles(profiles).filter((p) => p.id !== currentId);
  const loginMode = !!current.data?.login_mode;

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

  const signOut = async () => {
    try {
      await fetch(appURL('/api/auth/logout'), { method: 'POST' });
    } catch {
      /* reload anyway */
    }
    window.location.reload();
  };

  return (
    <div className={s.chip} ref={ref}>
      <button
        type="button"
        className={s.chipButton}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={me?.name ?? 'Profile'}
        title={me?.name ?? 'Profile'}
        onClick={() => setOpen((v) => !v)}
        data-nav
        style={{ padding: 3 }}
      >
        {me ? <Avatar profile={me} /> : <span className={s.avatar} />}
      </button>
      {open ? (
        <div className={s.profileMenu} role="menu" aria-label="Profile">
          <div className={s.menuWho}>
            {me ? <Avatar profile={me} className={`${s.avatar} ${s.menuAvatar}`} /> : null}
            <div>
              <div className={s.menuName}>{me?.name ?? 'Profile'}</div>
              <div className={s.menuRole}>{me?.is_admin ? 'Admin' : 'Member'}</div>
            </div>
          </div>

          {others.length ? (
            <div className={s.menuSection}>
              <div className={s.menuLabel}>Switch profile</div>
              <div className={s.menuProfiles}>
                {others.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    role="menuitem"
                    className={s.menuProfile}
                    onClick={() => {
                      setOpen(false);
                      setSwitchTo(p);
                    }}
                    title={p.has_pin || p.has_password ? `${p.name} (locked)` : p.name}
                    data-nav
                  >
                    <Avatar profile={p} />
                    <span className={s.menuProfileName}>{p.name}</span>
                    {p.has_pin || p.has_password ? (
                      <span className={s.menuLock} aria-label="locked">
                        <LockIcon />
                      </span>
                    ) : null}
                  </button>
                ))}
              </div>
              <button
                type="button"
                role="menuitem"
                className={s.menuItem}
                onClick={() => {
                  setOpen(false);
                  setSwitchTo('pick');
                }}
                data-nav
              >
                Who's watching…
              </button>
            </div>
          ) : null}

          <div className={s.menuSection}>
            <div className={s.menuLabel}>Display</div>
            <div className={s.menuRow}>
              <span className={s.menuToggleTitle}>Card size</span>
              <CardSizeSlider
                label="Card size"
                size={cardSize}
                range={cardRange}
                onChange={onCardSize}
              />
            </div>
          </div>

          <div className={s.menuSection}>
            <Link
              to="/player/settings"
              role="menuitem"
              className={s.menuItem}
              onClick={() => setOpen(false)}
              data-nav
            >
              <GearIcon /> Player settings
            </Link>
            <a role="menuitem" className={s.menuItem} href={appURL('/')} data-nav>
              <ExitIcon /> Back to SoulSync
            </a>
            {loginMode ? (
              <button
                type="button"
                role="menuitem"
                className={s.menuItem}
                onClick={() => void signOut()}
                data-nav
              >
                Sign out
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {switchTo
        ? inPlayer(
            <WhoGate
              profiles={profiles}
              currentId={currentId}
              initial={switchTo === 'pick' ? undefined : switchTo}
              onDone={() => setSwitchTo(null)}
            />,
          )
        : null}
    </div>
  );
}
