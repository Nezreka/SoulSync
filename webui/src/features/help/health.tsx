/**
 * Health (plan 4.5): every check P1 runs (GET /api/help/health), in the
 * order an admin needs them. One sentence on top, then Needs you, Worth a
 * look, Finish setting up, and the checks that passed folded away. Each
 * finding says why in a line and carries its fix buttons (fixes.ts) and a
 * Learn more into the docs article.
 *
 * Check again skips the server's 30s cache. While it runs the last result
 * stays on screen, dimmed, so nothing jumps. A fix that checks again says
 * whether it worked. Admin only: the store never pushes this view for a
 * member, and it never asks the server for one.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { HealthArea, HealthFinding, HealthFix, HealthReport } from './api';
import type { ViewProps } from './view-props';

import a from './admin.module.css';
import { getHealth, statusFromHealth } from './api';
import { runFix } from './fixes';
import { ago, countWord } from './format';
import { Glyph, type GlyphName } from './glyphs';
import h from './help.module.css';
import { Icon, type IconName } from './icons';
import { useHelpStore } from './store';
import { Label, List, Row } from './ui';

type Group = 'needs' | 'worth' | 'setup' | 'fine';

export function groupOf(f: HealthFinding): Group {
  if (f.severity === 'error') return 'needs';
  if (f.severity === 'ok') return 'fine';
  if (f.area === 'setup') return 'setup';
  return 'worth';
}

const GROUPS: { id: Exclude<Group, 'fine'>; label: string }[] = [
  { id: 'needs', label: 'Needs you' },
  { id: 'worth', label: 'Worth a look' },
  { id: 'setup', label: 'Finish setting up' },
];

const SEVERITY_WORD = { error: 'Error', warning: 'Warning', info: 'Info', ok: 'Fine' } as const;

const AREA_ICON: Record<HealthArea, { icon?: IconName; glyph?: GlyphName }> = {
  services: { glyph: 'server' },
  paths: { icon: 'folder' },
  disk: { glyph: 'disk' },
  video: { icon: 'film' },
  workers: { glyph: 'pause' },
  downloads: { icon: 'inbox' },
  automations: { icon: 'bolt' },
  setup: { icon: 'wrench' },
  updates: { icon: 'spark' },
  checks: { glyph: 'clock' },
};

function AreaIcon({ area }: { area: HealthArea }) {
  const pick = AREA_ICON[area] ?? { icon: 'pulse' };
  return pick.glyph ? (
    <Glyph name={pick.glyph} size={15} />
  ) : (
    <Icon name={pick.icon ?? 'pulse'} size={15} />
  );
}

/** the server's sentence, or one from the counts: "Two things need you. One is worth a look." */
export function summarize(report: HealthReport): string {
  if (report.summary) return report.summary;
  const n = (sev: 'error' | 'warning' | 'ok') =>
    report.counts?.[sev] ?? report.findings.filter((f) => f.severity === sev).length;
  const err = n('error');
  const warn = n('warning');
  const ok = n('ok');
  const out: string[] = [];
  if (err) out.push(`${countWord(err, true)} ${err === 1 ? 'thing needs' : 'things need'} you.`);
  if (warn) out.push(`${countWord(warn, true)} ${warn === 1 ? 'is' : 'are'} worth a look.`);
  if (!err && !warn)
    out.push(ok ? `Everything is running, all ${ok} checks.` : 'Nothing to report.');
  else if (ok) out.push(`Everything else, ${ok} ${ok === 1 ? 'check' : 'checks'}, is running.`);
  return out.join(' ');
}

/** paths and addresses in a detail read as code: http://jellyfin:8096, /downloads */
const CODE = /((?<=^|[\s(])(?:https?:\/\/[^\s,;)]*[^\s,;).]|\/[\w.-]+(?:\/[\w.-]*)*[\w-]))/;

export function withCode(text: string): ReactNode {
  const pieces = text.split(CODE);
  if (pieces.length === 1) return text;
  return pieces.map((piece, i) =>
    i % 2 ? <code key={i}>{piece}</code> : <Fragment key={i}>{piece}</Fragment>,
  );
}

interface FixState {
  /** which button is running */
  busy?: number;
  error?: string;
  note?: string;
}

export function HealthView({ entry, isAdmin }: ViewProps) {
  const push = useHelpStore((st) => st.push);
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [announce, setAnnounce] = useState('');
  const [fixes, setFixes] = useState<Record<string, FixState>>({});
  const [now, setNow] = useState(() => Date.now());
  const seq = useRef(0);
  const ctrl = useRef<AbortController | null>(null);
  const focusId = entry.params?.finding;

  const load = useCallback(async (fresh: boolean, about?: HealthFinding) => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    const mine = ++seq.current;
    setChecking(true);
    let res;
    try {
      res = await getHealth(fresh, c.signal);
    } catch {
      return; // aborted: a newer check owns the screen
    }
    if (mine !== seq.current) return;
    setChecking(false);
    if (!res.ok) {
      setError(res.error);
      setAnnounce(`Couldn't check. ${res.error}`);
      return;
    }
    const data: HealthReport = {
      ...res.data,
      findings: Array.isArray(res.data.findings) ? res.data.findings : [],
    };
    setReport(data);
    setError(null);
    setNow(Date.now());
    useHelpStore.getState().setStatus(statusFromHealth(data));
    if (about) {
      const after = data.findings.find((f) => f.id === about.id);
      const fixed = !after || after.severity === 'ok';
      setAnnounce(`${about.title}: ${fixed ? 'fixed' : 'still there'}. ${summarize(data)}`);
    } else {
      setAnnounce(summarize(data));
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    void load(false);
    return () => ctrl.current?.abort();
  }, [isAdmin, load]);

  // "Checked 12 seconds ago" keeps telling the truth
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, []);

  // a finding asked for by id: bring it into view once
  const scrolled = useRef(false);
  useEffect(() => {
    if (!focusId || !report || scrolled.current) return;
    const el = document.querySelector(`[data-finding="${CSS.escape(focusId)}"]`);
    if (el) {
      scrolled.current = true;
      el.scrollIntoView?.({ block: 'center' });
    }
  }, [focusId, report]);

  const onFix = async (finding: HealthFinding, fix: HealthFix, index: number) => {
    setFixes((m) => ({ ...m, [finding.id]: { busy: index } }));
    const outcome = await runFix(fix, {
      recheck: () => load(true, finding),
      openLogs: () => push({ view: 'logs' }),
    });
    const quiet = fix.kind === 'post' && !fix.recheck;
    setFixes((m) => {
      const next = { ...m };
      if (!outcome.ok) next[finding.id] = { error: outcome.error };
      else if (quiet) next[finding.id] = { note: `${fix.label}: done.` };
      else delete next[finding.id];
      return next;
    });
    if (!outcome.ok) setAnnounce(`${fix.label} didn't work. ${outcome.error}`);
    else if (quiet) setAnnounce(`${fix.label}: done.`);
  };

  const grouped = useMemo(() => {
    const out: Record<Group, HealthFinding[]> = { needs: [], worth: [], setup: [], fine: [] };
    for (const f of report?.findings ?? []) out[groupOf(f)].push(f);
    return out;
  }, [report]);

  if (!isAdmin) return null;

  const tone = !report
    ? 'quiet'
    : grouped.needs.length
      ? 'error'
      : grouped.worth.length
        ? 'warn'
        : 'ok';
  const summary = report ? summarize(report) : '';
  const stop = summary.search(/[.!?](\s|$)/);
  const lead = stop > 0 ? summary.slice(0, stop + 1) : summary;
  const rest = stop > 0 ? summary.slice(stop + 1) : '';
  const checkedAt =
    report?.checked_at ??
    (report?.age_seconds != null ? new Date(now - report.age_seconds * 1000).toISOString() : null);
  const when = checking
    ? report
      ? 'Checking again'
      : 'Checking'
    : checkedAt
      ? `Checked ${ago(checkedAt, now)}${report?.took_ms ? `, took ${formatTook(report.took_ms)}` : ''}`
      : 'Not checked yet';

  return (
    <>
      <span className={h.srOnly} role="status" aria-live="polite" aria-atomic="true">
        {announce}
      </span>

      <section className={a.summary} data-tone={tone} aria-label="Health summary">
        {report ? (
          <p className={a.sumText}>
            <em>{lead}</em>
            {rest}
          </p>
        ) : error ? (
          <p className={a.sumText}>
            <em>Couldn't check just now.</em> {error}
          </p>
        ) : (
          <p className={a.sumText}>
            <em>Checking SoulSync.</em> Services, folders, disk, workers and downloads.
          </p>
        )}
        <div className={a.meta}>
          <Glyph name="clock" size={14} />
          <span>{when}</span>
          <button
            type="button"
            className={h.btn}
            data-kind="secondary"
            onClick={() => void load(true)}
            disabled={checking}
            aria-busy={checking || undefined}
          >
            <Icon name="refresh" size={14} className={checking ? a.spin : undefined} />
            {checking ? 'Checking' : report || !error ? 'Check again' : 'Try again'}
          </button>
        </div>
        {report && error ? (
          <p className={a.calm}>Couldn't check again: {error} This is the last result.</p>
        ) : null}
      </section>

      <div className={a.findings} data-stale={checking && report ? '' : undefined}>
        {GROUPS.map((g) =>
          grouped[g.id].length ? (
            <section key={g.id} aria-label={g.label}>
              <Label>{g.label}</Label>
              {grouped[g.id].map((f) => (
                <FindingCard
                  key={f.id}
                  finding={f}
                  state={fixes[f.id]}
                  focused={f.id === focusId}
                  onFix={onFix}
                  onDocs={(id) => push({ view: 'article', params: { id } })}
                />
              ))}
            </section>
          ) : null,
        )}

        {grouped.fine.length ? (
          <section aria-label="Running fine">
            <Label>Running fine</Label>
            <details className={a.fine}>
              <summary className={a.fineSummary}>
                <Icon name="chev" size={14} className={a.fineChev} />
                {grouped.fine.length} {grouped.fine.length === 1 ? 'check' : 'checks'} passed
                <span className={a.fineMore} aria-hidden="true">
                  show
                </span>
              </summary>
              <ul className={a.fineGrid}>
                {grouped.fine.map((f) => {
                  const text = f.detail ? `${f.title} · ${f.detail}` : f.title;
                  return (
                    <li key={f.id} title={text}>
                      <span className={a.okDot} aria-hidden="true" />
                      <span className={a.fineText}>{text}</span>
                    </li>
                  );
                })}
              </ul>
            </details>
          </section>
        ) : null}
      </div>

      <Label>More</Label>
      <List label="More">
        <Row
          icon="terminal"
          title="Server logs"
          sub="Download any log with its backups, secrets removed"
          end="chev"
          onClick={() => push({ view: 'logs' })}
        />
        <Row
          icon="bug"
          title="Report a problem"
          sub="These findings go with it, if you want"
          end="chev"
          onClick={() => push({ view: 'report' })}
        />
      </List>
    </>
  );
}

function formatTook(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1).replace(/\.0$/, '')} s`;
}

function FindingCard({
  finding,
  state,
  focused,
  onFix,
  onDocs,
}: {
  finding: HealthFinding;
  state?: FixState;
  focused: boolean;
  onFix: (f: HealthFinding, fix: HealthFix, index: number) => void;
  onDocs: (id: string) => void;
}) {
  const fixes = (finding.fix ?? []).filter((f) => f && typeof f.label === 'string');
  const titleId = `hf-${finding.id.replace(/[^\w-]/g, '-')}`;
  const sev = finding.severity;
  const busy = state?.busy != null;
  const word = finding.area === 'setup' && sev === 'info' ? 'Setup' : SEVERITY_WORD[sev];
  return (
    <article
      className={a.find}
      data-sev={sev}
      data-focus={focused ? '' : undefined}
      data-finding={finding.id}
      aria-labelledby={titleId}
      aria-busy={busy || undefined}
    >
      <span className={a.fIcon} aria-hidden="true">
        <AreaIcon area={finding.area} />
      </span>
      <div className={a.fBody}>
        <h4 className={a.fTitle} id={titleId}>
          <span>{finding.title}</span>
          <span className={a.sev}>{word}</span>
        </h4>
        {finding.detail ? <p className={a.fDetail}>{withCode(finding.detail)}</p> : null}
        {fixes.length || finding.docs ? (
          <div className={h.acts}>
            {fixes.map((fix, i) => {
              const running = state?.busy === i;
              return (
                <button
                  key={`${fix.kind}-${i}`}
                  type="button"
                  className={h.btn}
                  data-kind={i === 0 && sev === 'error' ? 'primary' : 'secondary'}
                  disabled={busy}
                  aria-busy={running || undefined}
                  onClick={() => onFix(finding, fix, i)}
                >
                  <FixIcon fix={fix} running={running} />
                  {running ? busyLabel(fix) : fix.label}
                </button>
              );
            })}
            {finding.docs ? (
              <button
                type="button"
                className={h.btn}
                data-kind="ghost"
                onClick={() => onDocs(finding.docs as string)}
              >
                Learn more
              </button>
            ) : null}
          </div>
        ) : null}
        {state?.error ? (
          <p className={a.fixErr} role="alert">
            <Glyph name="info" size={13} />
            {state.error}
          </p>
        ) : state?.note ? (
          <p className={a.fixNote}>
            <Icon name="check" size={13} />
            {state.note}
          </p>
        ) : null}
      </div>
    </article>
  );
}

function busyLabel(fix: HealthFix): string {
  if (fix.kind === 'recheck') return 'Checking';
  if (fix.kind === 'post') return fix.recheck ? 'Working, then checking' : 'Working';
  return fix.label;
}

function FixIcon({ fix, running }: { fix: HealthFix; running: boolean }) {
  if (running) return <Icon name="refresh" size={14} className={a.spin} />;
  if (fix.kind === 'recheck' || (fix.kind === 'post' && /again/i.test(fix.label)))
    return <Icon name="refresh" size={14} />;
  if (fix.kind === 'post' && /resume/i.test(fix.label)) return <Icon name="play" size={14} />;
  if (fix.kind === 'external') return <Icon name="external" size={13} />;
  if (fix.kind === 'logs') return <Icon name="terminal" size={14} />;
  return null;
}
