/**
 * Import Plex users (#2319): everyone the Plex server is shared with, and
 * whether a SoulSync profile is already them. Picked people get a profile
 * linked to their Plex account, so Sign in with Plex lands on it any time,
 * even with new Plex sign-ups turned off. A profile that may already be them
 * (the same name, or their Plex home user link) can get the link instead.
 */

import { useEffect, useMemo, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import type { Template } from './types';

import {
  type PlexImportList,
  type PlexImportResult,
  type PlexImportUser,
  getPlexImport,
  postPlexImport,
} from './api';
import css from './people.module.css';

type Load =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: PlexImportList };

const STATUS_WORD: Record<PlexImportResult['status'], string> = {
  created: 'Added',
  linked: 'Linked',
  exists: 'Already here',
  skipped: 'Skipped',
  failed: "Didn't work",
};

function subLine(u: PlexImportUser): string {
  const bits: string[] = [];
  if (u.title && u.username && u.title !== u.username) bits.push(u.title);
  if (u.email) bits.push(u.email);
  if (u.managed) bits.push('Managed home user, signs in with a password instead');
  else if (u.home) bits.push('Home user');
  return bits.join(' · ');
}

function PlexAvatar({ user }: { user: PlexImportUser }) {
  const [broken, setBroken] = useState(false);
  return (
    <span className={css.av} aria-hidden="true">
      {user.thumb && !broken ? (
        <img src={user.thumb} alt="" referrerPolicy="no-referrer" onError={() => setBroken(true)} />
      ) : (
        (user.name || '?').charAt(0).toUpperCase()
      )}
    </span>
  );
}

export function PlexImportDialog({
  open,
  onClose,
  templates,
  signupTemplateId,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  templates: Template[];
  signupTemplateId: number | null;
  onImported?: () => void;
}) {
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  // per guessed match: true = link that profile, false = a new profile
  const [linkGuess, setLinkGuess] = useState<Record<string, boolean>>({});
  const [templateId, setTemplateId] = useState<number | null>(signupTemplateId);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<PlexImportResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    setLoad({ status: 'loading' });
    setPicked(new Set());
    setLinkGuess({});
    setResults(null);
    setError(null);
    setQuery('');
    getPlexImport(ctrl.signal)
      .then((r) => {
        if (ctrl.signal.aborted) return;
        if (!r.ok) {
          setLoad({ status: 'error', message: r.message });
          return;
        }
        setLoad({ status: 'ready', data: r.data });
        setTemplateId((t) => t ?? r.data.signup_template_id);
      })
      .catch(() => {
        // closed first
      });
    return () => ctrl.abort();
  }, [open]);

  useEffect(() => {
    if (open) setTemplateId(signupTemplateId);
  }, [open, signupTemplateId]);

  const users = load.status === 'ready' ? load.data.users : [];
  const importable = useMemo(() => users.filter((u) => u.match !== 'linked'), [users]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter((u) =>
      [u.name, u.title, u.email].some((s) => (s || '').toLowerCase().includes(q)),
    );
  }, [users, query]);
  const allPicked = importable.length > 0 && importable.every((u) => picked.has(u.id));

  const toggle = (id: string) =>
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () => setPicked(allPicked ? new Set() : new Set(importable.map((u) => u.id)));

  const run = async () => {
    const chosen = importable.filter((u) => picked.has(u.id));
    if (!chosen.length) return;
    setBusy(true);
    setError(null);
    const r = await postPlexImport({
      users: chosen.map((u) =>
        u.profile && u.match !== 'linked' && (linkGuess[u.id] ?? true)
          ? { id: u.id, profile_id: u.profile.id }
          : { id: u.id },
      ),
      template_id: templateId,
    });
    setBusy(false);
    if (!r.ok) {
      setError(r.message);
      return;
    }
    setResults(r.data.results);
    const added = r.data.results.filter((x) => x.status === 'created' || x.status === 'linked');
    window.showToast?.(
      added.length
        ? `${added.length} ${added.length === 1 ? 'person' : 'people'} imported from Plex`
        : 'Nobody new to import',
      added.length ? 'success' : 'info',
    );
    if (added.length) onImported?.();
  };

  const signinOff = load.status === 'ready' && !load.data.signin_enabled;
  const count = importable.filter((u) => picked.has(u.id)).length;

  return (
    <DialogFrame open={open} onOpenChange={(o) => (o ? undefined : onClose())}>
      <DialogHeader title="Import Plex users" closeLabel="Close">
        Everyone your Plex server is shared with. Each one gets a profile linked to their Plex
        account, so Sign in with Plex lands on it.
      </DialogHeader>
      <DialogBody>
        {signinOff ? (
          <p className={css.paneSub} role="note">
            Sign in with Plex is off. Turn it on under System, Security so they can sign in. New
            Plex sign-ups can stay off: imported people get in either way.
          </p>
        ) : null}
        {load.status === 'loading' ? (
          <p className={css.empty}>Asking Plex who has access...</p>
        ) : load.status === 'error' ? (
          <p className={css.error} role="alert">
            {load.message}
          </p>
        ) : results ? (
          <ul className={css.rows} aria-label="Import results">
            {results.map((x) => (
              <li key={x.plex_id} className={css.lrow}>
                <div className={css.lrowMain}>
                  <div className={css.t1}>{x.name || 'Plex user'}</div>
                  <div className={css.t2}>{x.message}</div>
                </div>
                <span
                  className={`${css.state} ${x.status === 'created' || x.status === 'linked' ? css.stateOpen : ''}`}
                >
                  {STATUS_WORD[x.status] ?? x.status}
                </span>
              </li>
            ))}
          </ul>
        ) : !users.length ? (
          <p className={css.empty}>Your Plex server isn&apos;t shared with anyone yet.</p>
        ) : (
          <>
            <div className={css.bar} style={{ marginTop: 0 }}>
              <label className={css.search}>
                <span className={css.srOnly}>Find a Plex user</span>
                <input
                  type="search"
                  placeholder="Find a Plex user"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  autoComplete="off"
                />
              </label>
              <span className={css.spacer} />
              <label className={css.t2}>
                <input
                  type="checkbox"
                  checked={allPicked}
                  disabled={!importable.length}
                  onChange={toggleAll}
                />{' '}
                Pick everyone new
              </label>
            </div>
            <ul className={css.rows} aria-label="Plex users">
              {shown.map((u) => {
                const linked = u.match === 'linked';
                const guess = !linked && u.profile;
                return (
                  <li key={u.id} className={`${css.lrow} ${linked ? css.quiet : ''}`}>
                    <input
                      type="checkbox"
                      aria-label={`Import ${u.name}`}
                      checked={linked || picked.has(u.id)}
                      disabled={linked || busy}
                      onChange={() => toggle(u.id)}
                    />
                    <PlexAvatar user={u} />
                    <div className={css.lrowMain}>
                      <div className={css.t1}>{u.name}</div>
                      <div className={css.t2}>{subLine(u)}</div>
                    </div>
                    {linked && u.profile ? (
                      <span className={css.state}>In SoulSync as {u.profile.name}</span>
                    ) : guess && u.profile ? (
                      <select
                        aria-label={`What to do with ${u.name}`}
                        value={(linkGuess[u.id] ?? true) ? 'link' : 'new'}
                        disabled={busy}
                        onChange={(e) =>
                          setLinkGuess((g) => ({ ...g, [u.id]: e.target.value === 'link' }))
                        }
                      >
                        <option value="link">Link to {u.profile.name}</option>
                        <option value="new">New profile</option>
                      </select>
                    ) : (
                      <span className={`${css.state} ${css.stateOpen}`}>New</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
        {error ? (
          <p className={css.error} role="alert">
            {error}
          </p>
        ) : null}
      </DialogBody>
      <DialogFooter>
        {results || load.status !== 'ready' ? (
          <button type="button" className={`${css.btn} ${css.secondary}`} onClick={onClose}>
            {results ? 'Done' : 'Close'}
          </button>
        ) : (
          <>
            <label className={css.t2}>
              New profiles follow{' '}
              <select
                aria-label="New profiles follow"
                value={templateId ?? ''}
                disabled={busy}
                onChange={(e) => setTemplateId(e.target.value ? Number(e.target.value) : null)}
              >
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                    {t.id === signupTemplateId ? ' (new people start as)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <span className={css.spacer} />
            <button type="button" className={`${css.btn} ${css.secondary}`} onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className={`${css.btn} ${css.primary}`}
              disabled={!count || busy}
              onClick={() => void run()}
            >
              {busy ? 'Importing...' : count ? `Import ${count}` : 'Import'}
            </button>
          </>
        )}
      </DialogFooter>
    </DialogFrame>
  );
}
