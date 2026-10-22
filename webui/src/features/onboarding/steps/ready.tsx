/**
 * Step: ready. Saves the draft and checks every piece live, a row at a time.
 * An amber row links back to the step that fixes it. "Open SoulSync" locks
 * the install the way step 1 asked (last, so nothing above is refused for
 * want of a login) and hands over to the app.
 */

import { useEffect, useState } from 'react';

import { appURL } from '@/platform/url-base';

import { applyDraft, previewEffects, realEffects, SAVE_FAILED, type ReadyRow } from '../apply';
import { stashSetupPlaylistLink } from '../handoff';
import { forgetSavedDraft, neighbours, useOnboarding } from '../store';
import { ArrowIcon, CheckIcon, s, Spinner, WarnIcon } from '../ui';

export function ReadyStep({ onFinished }: { onFinished: () => void }) {
  const draft = useOnboarding((st) => st.draft);
  const preview = useOnboarding((st) => st.preview);
  const session = useOnboarding((st) => st.session);
  const rows = useOnboarding((st) => st.rows);
  const applying = useOnboarding((st) => st.applying);
  const setRows = useOnboarding((st) => st.setRows);
  const go = useOnboarding((st) => st.go);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState('');

  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const live: Record<string, ReadyRow> = {};
    const order: string[] = [];
    setRows([], 'running');
    const fx = preview ? previewEffects() : realEffects;
    void applyDraft(useOnboarding.getState().draft, fx, (row) => {
      if (!live[row.id]) order.push(row.id);
      live[row.id] = row;
      setRows(
        order.map((id) => live[id]),
        'running',
      );
    }).then((final) => setRows(final.length ? final : order.map((id) => live[id]), 'done'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, attempt]);

  async function finish() {
    setFinishing(true);
    setError('');
    try {
      if (!preview) {
        await realEffects.lock(draft.account);
        await realEffects.complete(draft.purposes);
        forgetSavedDraft();
        stashSetupPlaylistLink(draft.playlistUrl);
      }
      onFinished();
      if (!preview) {
        // a fresh load picks up the new server, sources and lock in one go
        window.location.assign(appURL(draft.playlistUrl.trim() ? '/sync' : '/'));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't finish setup");
      setFinishing(false);
    }
  }

  const back = neighbours('ready', draft).prev ?? 'account';
  const warned = rows.filter((r) => r.state === 'warn').length;
  const running = applying !== 'done';
  const failed = !running && rows.some((r) => r.id === SAVE_FAILED);

  return (
    <div className={s.center}>
      <div className={s.glow} aria-hidden="true" />
      <div className={s.readyWrap}>
        <header
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 16,
            textAlign: 'center',
          }}
        >
          <div className={s.readyIcon} style={failed ? { background: '#f59e0b' } : undefined}>
            {running ? (
              <Spinner label="Saving" />
            ) : failed ? (
              <WarnIcon />
            ) : (
              <CheckIcon size={28} stroke={2.8} />
            )}
          </div>
          <h1 className={s.heroTitle} style={{ fontSize: 40 }}>
            {running ? 'Putting it all together' : failed ? 'Not saved yet' : 'SoulSync is ready'}
          </h1>
          <p className={s.heroLead} style={{ fontSize: 16, maxWidth: 540 }}>
            {running
              ? 'Saving your choices and checking each one for real.'
              : failed
                ? "SoulSync couldn't save your choices, so nothing has changed yet. Try again in a moment."
                : preview
                  ? `This was the preview, nothing was saved.${warned ? ` In a real setup ${warned === 1 ? 'one thing' : `${warned} things`} would need a look.` : ''}`
                  : warned
                    ? `Everything is saved. ${warned === 1 ? 'One thing needs' : `${warned} things need`} a look, fix ${warned === 1 ? 'it' : 'them'} now or from the dashboard later.`
                    : 'Everything checked out. Your first library scan starts as soon as you open SoulSync.'}
          </p>
        </header>

        <section className={s.list} aria-label="Setup check" aria-live="polite">
          {rows.map((r) => (
            <div key={r.id} className={s.readyRow}>
              {r.state === 'checking' ? (
                <Spinner label="Checking" />
              ) : r.state === 'ok' ? (
                <span className={s.okIcon} role="img" aria-label="Working">
                  <CheckIcon size={18} stroke={2.6} />
                </span>
              ) : (
                <span className={s.warnIcon} role="img" aria-label="Needs a look">
                  <WarnIcon />
                </span>
              )}
              <b>{r.name}</b>
              <span className={s.readyDetail}>{r.detail}</span>
              {r.state === 'warn' && r.fix ? (
                <button type="button" className={s.fix} onClick={() => go(r.fix!)}>
                  Fix
                </button>
              ) : (
                <i />
              )}
            </div>
          ))}
        </section>

        {error ? (
          <p className={s.error} role="alert">
            {error}
          </p>
        ) : null}

        <div className={s.readyActions}>
          <button type="button" className={s.quiet} onClick={() => go(back)} disabled={running}>
            Back
          </button>
          {failed ? (
            <button
              type="button"
              className={`${s.primary} ${s.big}`}
              onClick={() => setAttempt((n) => n + 1)}
            >
              Try again
            </button>
          ) : (
            <button
              type="button"
              className={`${s.primary} ${s.big}`}
              onClick={finish}
              disabled={running || finishing}
            >
              {finishing ? <Spinner /> : null}
              {preview ? 'Close preview' : 'Open SoulSync'}
              <ArrowIcon />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
