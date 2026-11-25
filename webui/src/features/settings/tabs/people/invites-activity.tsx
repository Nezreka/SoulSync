/**
 * Invites and Activity, moved here from the Profiles modal (PLAN 8.1):
 * the same routes (/api/profiles/invites, /api/profiles/audit) and the same
 * invite maker (init.js openInviteCreator). Activity is the admin log, with
 * the permission changes P0 writes to it said in words.
 */

import { useCallback, useEffect, useState } from 'react';

import type { AuditEntry, Catalogue, Invite, Person } from './types';

import { getAudit, getInvites, revokeInvite } from './api';
import { Icon } from './icons';
import { ago, auditSentence, isAccessEntry, presetLine, until } from './model';
import { onProfileLayersClosed, openInviteMaker } from './open-editor';
import css from './people.module.css';

const STATE_WORD: Record<Invite['state'], string> = {
  open: 'Open',
  used: 'Used',
  revoked: 'Revoked',
  expired: 'Run out',
};

export function InvitesScreen({
  people,
  onCount,
}: {
  people: Person[];
  onCount?: (open: number) => void;
}) {
  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await getInvites();
    if (!r.ok) {
      setError(r.message);
      return;
    }
    setError(null);
    setInvites(r.data.invites || []);
    onCount?.((r.data.invites || []).filter((i) => i.state === 'open').length);
  }, [onCount]);
  useEffect(() => {
    void load();
    // the invite maker is the old modal: list again once it closes
    return onProfileLayersClosed(() => void load());
  }, [load]);

  const revoke = async (inv: Invite) => {
    const ok = await (window.showConfirmDialog?.({
      title: 'Revoke this invite?',
      message: 'The link stops working. Anyone who already joined with it keeps their profile.',
      confirmText: 'Revoke',
      destructive: true,
    }) ?? Promise.resolve(true));
    if (!ok) return;
    const r = await revokeInvite(inv.id);
    window.showToast?.(r.ok ? 'Invite revoked' : r.message, r.ok ? 'info' : 'error');
    void load();
  };

  return (
    <>
      <div className={css.bar} style={{ marginTop: 0 }}>
        <p className={css.paneSub} style={{ margin: 0 }}>
          Send someone a link and they make their own profile. Each link works once.
        </p>
        <span className={css.spacer} />
        <button
          type="button"
          className={`${css.btn} ${css.primary}`}
          onClick={() => {
            if (!openInviteMaker())
              window.showToast?.("Invites aren't ready yet. Reload the page.", 'error');
          }}
        >
          <Icon name="plus" />
          Create invite link
        </button>
      </div>
      {error ? (
        <p className={css.error}>{error}</p>
      ) : !invites ? (
        <p className={css.empty}>Loading invites...</p>
      ) : !invites.length ? (
        <div className={css.rows}>
          <p className={css.empty}>
            No invite links yet. Make one and send it: they pick their own name and PIN, you pick
            what they can use.
          </p>
        </div>
      ) : (
        <ul className={css.rows} aria-label="Invites">
          {invites.map((inv) => {
            const state = STATE_WORD[inv.state] ? inv.state : 'expired';
            const who = people.find((p) => p.id === inv.used_by)?.name;
            const when =
              state === 'open'
                ? `runs out ${until(inv.expires_at)}`
                : state === 'used'
                  ? `${who ? `used by ${who}` : 'used'} ${ago(inv.used_at)}`
                  : state === 'revoked'
                    ? `revoked ${ago(inv.revoked_at)}`
                    : `made ${ago(inv.created_at)}`;
            return (
              <li key={inv.id} className={`${css.lrow} ${state === 'open' ? '' : css.quiet}`}>
                <span className={css.logIcon} aria-hidden="true">
                  <Icon name="link" />
                </span>
                <div className={css.lrowMain}>
                  <div className={css.t1}>{inv.note || 'Invite link'}</div>
                  <div className={css.t2}>
                    {[presetLine(inv.preset), when.trim()].filter(Boolean).join(' · ')}
                  </div>
                </div>
                <span className={`${css.state} ${state === 'open' ? css.stateOpen : ''}`}>
                  {STATE_WORD[state]}
                </span>
                {state === 'open' ? (
                  <button type="button" className={css.link} onClick={() => void revoke(inv)}>
                    Revoke
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

const PAGE = 30;

export function ActivityScreen({ catalogue }: { catalogue: Catalogue }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [onlyAccess, setOnlyAccess] = useState(false);

  const more = useCallback(async (offset: number) => {
    setLoading(true);
    const r = await getAudit(PAGE, offset);
    setLoading(false);
    if (!r.ok) {
      setError(r.message);
      return;
    }
    setError(null);
    const got = r.data.entries || [];
    setEntries((list) => (offset === 0 ? got : [...list, ...got]));
    setDone(got.length < PAGE);
  }, []);
  useEffect(() => {
    void more(0);
  }, [more]);

  const shown = onlyAccess ? entries.filter(isAccessEntry) : entries;
  return (
    <>
      <div className={css.bar} style={{ marginTop: 0 }}>
        <p className={css.paneSub} style={{ margin: 0 }}>
          Who changed what, newest first.
        </p>
        <span className={css.spacer} />
        <div className={css.inline} role="group" aria-label="Show">
          <button
            type="button"
            className={css.chip}
            aria-pressed={!onlyAccess}
            onClick={() => setOnlyAccess(false)}
          >
            Everything
          </button>
          <button
            type="button"
            className={css.chip}
            aria-pressed={onlyAccess}
            onClick={() => setOnlyAccess(true)}
          >
            Access changes
          </button>
        </div>
      </div>
      {error ? <p className={css.error}>{error}</p> : null}
      {!error && !loading && !shown.length ? (
        <div className={css.rows}>
          <p className={css.empty}>
            {onlyAccess
              ? 'No access changes yet. Policy, template and personal changes show up here.'
              : 'Nothing yet. Profile changes, access changes and invites show up here.'}
          </p>
        </div>
      ) : null}
      {shown.length ? (
        <ul className={css.rows} aria-label="Activity">
          {shown.map((e) => (
            <li key={e.id} className={css.lrow}>
              <span
                className={`${css.logIcon} ${isAccessEntry(e) ? css.logAccess : ''}`}
                aria-hidden="true"
              >
                <Icon name={isAccessEntry(e) ? 'admin' : 'clock'} />
              </span>
              <div className={css.lrowMain}>
                <div className={`${css.t1} ${css.wrap}`}>{auditSentence(e, catalogue)}</div>
              </div>
              <span className={css.t2} title={e.created_at ? `${e.created_at} UTC` : undefined}>
                {ago(e.created_at)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {!done && entries.length ? (
        <div className={css.more}>
          <button
            type="button"
            className={`${css.btn} ${css.secondary}`}
            disabled={loading}
            onClick={() => void more(entries.length)}
          >
            {loading ? 'Loading...' : 'Load more'}
          </button>
        </div>
      ) : null}
    </>
  );
}
