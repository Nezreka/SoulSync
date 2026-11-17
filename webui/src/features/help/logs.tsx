/**
 * Logs (plan 9.1): every log SoulSync writes, each with its backups, how
 * big and when it was last written. Download one file, one log with its
 * backups, or everything as a zip (GET /api/help/logs*). The server streams
 * them and removes secrets line by line; Remove secrets is on, and turning
 * it off asks once first. Admin only.
 */

import { useEffect, useRef, useState } from 'react';

import type { LogFile, LogGroup, LogsList } from './api';
import type { ViewProps } from './view-props';

import a from './admin.module.css';
import { allLogsURL, getLogs, logDownloadURL, startDownload } from './api';
import { ago, formatBytes } from './format';
import { Glyph } from './glyphs';
import h from './help.module.css';
import { Icon } from './icons';
import { openSettingsTab } from './links';
import { Switch } from './switch';
import { Label } from './ui';

/** asked once per visit to the app: after a yes, raw stays a plain switch */
let rawConfirmed = false;

/** tests start each case with the question unasked */
export function resetRawConfirm(): void {
  rawConfirmed = false;
}

export function LogsView({ isAdmin }: ViewProps) {
  const [list, setList] = useState<LogsList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [redact, setRedact] = useState(true);
  const [asking, setAsking] = useState(false);
  const [announce, setAnnounce] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const confirmRef = useRef<HTMLButtonElement>(null);

  const load = async (signal?: AbortSignal) => {
    setError(null);
    let res;
    try {
      res = await getLogs(signal);
    } catch {
      return;
    }
    if (signal?.aborted) return;
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setList({ ...res.data, logs: Array.isArray(res.data.logs) ? res.data.logs : [] });
    setNow(Date.now());
  };

  useEffect(() => {
    if (!isAdmin) return;
    const c = new AbortController();
    void load(c.signal);
    return () => c.abort();
  }, [isAdmin]);

  useEffect(() => {
    if (asking) confirmRef.current?.focus();
  }, [asking]);

  if (!isAdmin) return null;

  const raw = !redact;
  const fetchIt = (url: string, what: string) => {
    startDownload(url);
    setAnnounce(`Downloading ${what}${raw ? ', with secrets left in' : ''}.`);
  };

  const onRedact = (on: boolean) => {
    if (on) {
      setRedact(true);
      setAsking(false);
      return;
    }
    if (rawConfirmed) setRedact(false);
    else setAsking(true);
  };

  return (
    <>
      <span className={h.srOnly} role="status" aria-live="polite" aria-atomic="true">
        {announce}
      </span>

      {error ? (
        <div className={a.calmCard} role="alert">
          <p>
            <b>Couldn't list the logs.</b> {error}
          </p>
          <div className={h.acts}>
            <button
              type="button"
              className={h.btn}
              data-kind="secondary"
              onClick={() => void load()}
            >
              <Icon name="refresh" size={14} />
              Try again
            </button>
            <button
              type="button"
              className={h.btn}
              data-kind="ghost"
              onClick={() => openSettingsTab('logs')}
            >
              Open the live log
            </button>
          </div>
        </div>
      ) : null}

      <section className={a.logsHead} aria-label="Every log">
        <div className={a.logsHeadTop}>
          <span className={a.logsHeadIcon} aria-hidden="true">
            <Glyph name="zip" size={18} />
          </span>
          <div className={a.logsHeadText}>
            <p className={a.logsHeadTitle}>
              {list
                ? `${list.logs.length} ${list.logs.length === 1 ? 'log' : 'logs'}, ${formatBytes(list.total_bytes)} in all`
                : error
                  ? 'Server logs'
                  : 'Reading the log folder'}
            </p>
            {list ? (
              <p className={a.logsHeadSub}>
                In <code>{list.log_dir}</code>
              </p>
            ) : null}
          </div>
        </div>
        <button
          type="button"
          className={h.btn}
          data-kind="primary"
          disabled={!list || asking}
          onClick={() => list && fetchIt(allLogsURL(raw), list.zip_name)}
        >
          <Icon name="down" size={14} />
          Download everything
        </button>
        {list ? (
          <p className={a.logsHeadZip}>{list.zip_name}, with a README of what's inside</p>
        ) : null}
      </section>

      <div className={a.redactRow} data-raw={raw ? '' : undefined}>
        <span className={a.incText}>
          <span className={a.incTitle}>Remove secrets</span>
          <span className={a.incHint}>
            {raw
              ? 'Off: the files go as written, keys and tokens included'
              : 'API keys, tokens and passwords show as ***'}
          </span>
        </span>
        <Switch label="Remove secrets" on={redact} onChange={onRedact} />
      </div>

      {asking ? (
        <div
          className={a.confirm}
          role="alertdialog"
          aria-labelledby="raw-q"
          aria-describedby="raw-d"
        >
          <p id="raw-q" className={a.confirmTitle}>
            <Glyph name="unlock" size={15} />
            Raw logs can contain API keys and tokens
          </p>
          <p id="raw-d" className={a.confirmText}>
            Send them only to someone you trust, and never post them in public. Turned off, every
            download below goes as written.
          </p>
          <div className={h.acts}>
            <button
              ref={confirmRef}
              type="button"
              className={h.btn}
              data-kind="secondary"
              onClick={() => setAsking(false)}
            >
              Keep secrets removed
            </button>
            <button
              type="button"
              className={`${h.btn} ${a.danger}`}
              data-kind="secondary"
              onClick={() => {
                rawConfirmed = true;
                setAsking(false);
                setRedact(false);
                setAnnounce('Secrets stay in the downloads now.');
              }}
            >
              Leave secrets in
            </button>
          </div>
        </div>
      ) : null}

      {list ? (
        list.logs.length ? (
          <>
            <Label>Each log</Label>
            {list.logs.map((g) => (
              <LogCard
                key={g.name}
                group={g}
                now={now}
                raw={raw}
                disabled={asking}
                onGet={fetchIt}
              />
            ))}
          </>
        ) : (
          <p className={a.calm}>
            No logs in that folder yet. SoulSync writes app.log once it starts.
          </p>
        )
      ) : !error ? (
        <div className={a.logCard} aria-busy="true">
          <span className={a.skel} />
        </div>
      ) : null}
    </>
  );
}

function LogCard({
  group,
  now,
  raw,
  disabled,
  onGet,
}: {
  group: LogGroup;
  now: number;
  raw: boolean;
  disabled: boolean;
  onGet: (url: string, what: string) => void;
}) {
  const files = group.files ?? [];
  const current = files.find((f) => !f.backup) ?? null;
  const backups = files.filter((f) => f.backup);
  const titleId = `log-${group.name.replace(/[^\w-]/g, '-')}`;
  const when = ago(group.last_written, now);
  return (
    <article className={a.logCard} aria-labelledby={titleId}>
      <div className={a.logTop}>
        <span className={a.fIcon} data-tone="log" aria-hidden="true">
          <Icon name="terminal" size={15} />
        </span>
        <div className={a.fBody}>
          <h4 className={a.fTitle} id={titleId}>
            <span>{group.label}</span>
            {current ? <code className={a.fileName}>{current.name}</code> : null}
          </h4>
          {group.description ? <p className={a.fDetail}>{group.description}</p> : null}
          <p className={a.logMeta}>
            {formatBytes(group.bytes)}
            {backups.length
              ? `, ${backups.length} ${backups.length === 1 ? 'backup' : 'backups'}`
              : ''}
            {when ? ` · last written ${when}` : ''}
          </p>
          <div className={h.acts}>
            {current ? (
              <button
                type="button"
                className={h.btn}
                data-kind="secondary"
                disabled={disabled}
                onClick={() => onGet(logDownloadURL(current.name, { raw }), current.name)}
              >
                <Icon name="down" size={14} />
                {current.name}
                <span className={a.btnSize}>{formatBytes(current.bytes)}</span>
              </button>
            ) : null}
            {backups.length ? (
              <button
                type="button"
                className={h.btn}
                data-kind="secondary"
                disabled={disabled}
                onClick={() =>
                  onGet(
                    logDownloadURL(group.name, { withBackups: true, raw }),
                    `${group.label} with its backups`,
                  )
                }
              >
                <Glyph name="zip" size={14} />
                With backups
                <span className={a.btnSize}>{formatBytes(group.bytes)}</span>
              </button>
            ) : null}
          </div>
        </div>
      </div>
      {backups.length ? (
        <details className={a.backups}>
          <summary className={a.fineSummary}>
            <Icon name="chev" size={14} className={a.fineChev} />
            {backups.length === 1 ? 'The backup' : `The ${backups.length} backups`}
            <span className={a.fineMore} aria-hidden="true">
              one at a time
            </span>
          </summary>
          <ul className={a.fileList}>
            {backups.map((f) => (
              <FileRow
                key={f.name}
                file={f}
                now={now}
                raw={raw}
                disabled={disabled}
                onGet={onGet}
              />
            ))}
          </ul>
        </details>
      ) : null}
    </article>
  );
}

function FileRow({
  file,
  now,
  raw,
  disabled,
  onGet,
}: {
  file: LogFile;
  now: number;
  raw: boolean;
  disabled: boolean;
  onGet: (url: string, what: string) => void;
}) {
  const when = ago(file.last_written, now);
  return (
    <li className={a.fileRow}>
      <code className={a.fileName}>{file.name}</code>
      <span className={a.fileMeta}>
        {formatBytes(file.bytes)}
        {when ? ` · ${when}` : ''}
      </span>
      <button
        type="button"
        className={a.iconBtn}
        aria-label={`Download ${file.name}`}
        disabled={disabled}
        onClick={() => onGet(logDownloadURL(file.name, { raw }), file.name)}
      >
        <Icon name="down" size={15} />
      </button>
    </li>
  );
}
