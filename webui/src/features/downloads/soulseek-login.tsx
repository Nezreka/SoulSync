/**
 * slskd up but logged out of the Soulseek server (a server ban, a
 * reconnect). The /status payload carries it on `soulseek.logged_out`
 * (core/soulseek_session.py); Soulseek downloads wait for the login
 * instead of failing. The Downloads page shows this banner, the
 * dashboard's alerts band shows the same words.
 */

import { useCallback, useEffect, useState } from 'react';

import { useShellEvent } from '@/platform/shell/use-shell-event';

export interface SoulseekLoginNotice {
  title: string;
  detail: string;
}

/** the banner's words, or null while logged in (or unknown) */
export function soulseekLoginNotice(
  status: { soulseek?: Record<string, unknown> } | null | undefined,
  now: number = Date.now(),
): SoulseekLoginNotice | null {
  const slsk = status?.soulseek;
  if (!slsk || slsk.logged_out !== true) return null;
  const since = typeof slsk.logged_out_since === 'number' ? slsk.logged_out_since : null;
  const wait = typeof slsk.login_wait_minutes === 'number' ? slsk.login_wait_minutes : 45;
  const mins = since ? Math.max(0, Math.round((now / 1000 - since) / 60)) : null;
  const forHow = mins == null ? '' : mins < 1 ? ' just now' : ` ${mins} min ago`;
  return {
    title: `Soulseek logged out${forHow ? ` (${forHow.trim()})` : ''}`,
    detail:
      wait > 0
        ? `slskd lost its Soulseek login. Soulseek downloads wait for it without using up their retries, and pick up where they were once it's back. After ${wait} min they move on to your next source.`
        : 'slskd lost its Soulseek login. Soulseek downloads move straight on to your next source.',
  };
}

/** the /status payload, kept fresh by the shell's status push */
function useStatusPayload() {
  const [status, setStatus] = useState<{ soulseek?: Record<string, unknown> } | null>(null);
  useShellEvent(
    'ss:service-status',
    useCallback((frame: { soulseek?: Record<string, unknown> }) => setStatus(frame), []),
  );
  useEffect(() => {
    let cancelled = false;
    void fetch('/status')
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => {
        if (!cancelled && p) setStatus(p as { soulseek?: Record<string, unknown> });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return status;
}

export function SoulseekLoginBanner() {
  const notice = soulseekLoginNotice(useStatusPayload());
  if (!notice) return null;
  return (
    <div className="adl-batch-filter-banner" id="adl-soulseek-login-banner" role="status">
      <span
        className="adl-filter-banner-dot"
        style={{ background: 'rgb(var(--warning-rgb))' }}
        aria-hidden="true"
      />
      <strong>{notice.title}.</strong> {notice.detail}
    </div>
  );
}
