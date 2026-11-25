/**
 * System > Logs (settings-bic phase 6; the Logs tab until then): the live
 * log viewer. Open: the last 300 lines of the chosen log, then live lines
 * over the socket. Left: the live lines stop. Nothing here is a saved
 * setting. The controls are the parts now (a level is a segment, Auto-scroll
 * a switch); what each one reads and how is what settings.js did. The status
 * line says Live only while the socket really is subscribed.
 */

import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import type { SettingsTabProps } from '../host';

import { Btn, Card, Segmented, Select, StatusWords, Switch, TextField } from '../ui';
import s from '../ui/settings.module.css';
import {
  LOG_LEVELS,
  LOG_MAX_LINES,
  LOG_SEARCH_DELAY_MS,
  LOG_SOURCES,
  LOG_TAIL_LINES,
  type LogLiveFrame,
  classifyLogLine,
  keepLiveLines,
  logLevelClass,
} from './logs';

interface Row {
  id: number;
  text: string;
  className: string;
}

let nextRowId = 0;
const row = (text: string, className: string): Row => ({ id: nextRowId++, text, className });
const status = (text: string): Row => row(text, 'log-line log-info');
const logRow = (line: string): Row => row(line, 'log-line ' + logLevelClass(classifyLogLine(line)));

const Line = memo(function Line({ text, className }: { text: string; className: string }) {
  return <div className={className}>{text}</div>;
});

export function LogsTab({ active }: SettingsTabProps) {
  const [rows, setRows] = useState<Row[]>(() => [status('Initializing log viewer...')]);
  const [count, setCount] = useState(0);
  const [level, setLevel] = useState('');
  /** the socket took the subscription: the lines are live */
  const [streaming, setStreaming] = useState(false);
  const terminal = useRef<HTMLDivElement>(null);
  const autoscroll = useRef<HTMLInputElement>(null);
  const scrollNext = useRef(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // what settings.js kept in its _logViewer* lets
  const live = useRef(false);
  const view = useRef({ source: 'app', level: '', search: '' });

  const append = useCallback((lines: string[], replace = false) => {
    scrollNext.current = true;
    setRows((current) => {
      const next = [...(replace ? [] : current), ...lines.map(logRow)];
      const trimmed = next.length > LOG_MAX_LINES ? next.slice(next.length - LOG_MAX_LINES) : next;
      setCount(trimmed.length);
      return trimmed;
    });
  }, []);

  const onLive = useCallback(
    (frame: LogLiveFrame) => {
      if (!live.current || !frame?.lines) return;
      if (frame.source !== view.current.source) return;
      const kept = keepLiveLines(frame.lines, view.current.level, view.current.search);
      if (kept.length > 0) append(kept);
    },
    [append],
  );

  const stop = useCallback(() => {
    if (!live.current) return;
    live.current = false;
    setStreaming(false);
    window.soulsyncLogStream?.unsubscribe(onLive);
  }, [onLive]);

  const start = useCallback(async () => {
    if (live.current) return;
    live.current = true;
    const { source, level: lvl, search } = view.current;
    try {
      const params = new URLSearchParams({ source, lines: String(LOG_TAIL_LINES) });
      if (lvl) params.set('level', lvl);
      if (search) params.set('search', search);
      const response = await fetch(`/api/logs/tail?${params}`);
      const data = (await response.json()) as { lines?: string[] };
      if (data.lines) append(data.lines, true);
    } catch (error) {
      console.warn('Failed to load initial logs:', error);
    }
    const took = window.soulsyncLogStream?.subscribe(view.current.source, onLive);
    if (live.current) setStreaming(took !== false && took !== undefined);
  }, [append, onLive]);

  const reload = useCallback(() => {
    stop();
    setRows([status('Loading...')]);
    void start();
  }, [start, stop]);

  // opened: start (a second start while live does nothing). left: stop
  useEffect(() => {
    if (active) void start();
    else stop();
  }, [active, start, stop]);

  useEffect(
    () => () => {
      stop();
      if (searchTimer.current) clearTimeout(searchTimer.current);
    },
    [stop],
  );

  // after lines arrive, not after Loading or Clear
  useLayoutEffect(() => {
    if (!scrollNext.current) return;
    scrollNext.current = false;
    if (autoscroll.current?.checked && terminal.current) {
      terminal.current.scrollTop = terminal.current.scrollHeight;
    }
  }, [rows]);

  const copy = () => {
    const text = rows.map((r) => r.text).join('\n');
    if (navigator.clipboard && window.isSecureContext) {
      void navigator.clipboard
        .writeText(text)
        .then(() => window.showToast?.('Logs copied', 'success'));
      return;
    }
    const area = document.createElement('textarea');
    area.value = text;
    area.style.cssText = 'position:fixed;left:-9999px';
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    document.body.removeChild(area);
    window.showToast?.('Logs copied', 'success');
  };

  // the help sheet's Logs view: each log, its backups, secrets removed. a
  // shell without the sheet gets the whole zip straight away, still redacted
  const downloadLogs = () => {
    if (window.SoulSyncHelp) {
      window.SoulSyncHelp.open('logs');
      return;
    }
    const a = document.createElement('a');
    a.href = appURL('/api/help/logs/download-all');
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const clear = () => {
    setRows([]);
    setCount(0);
  };

  return (
    <Card
      title="Live log"
      hint="Pick a log and a level, or filter for a word. Download takes every log, secrets removed."
      actions={
        <Btn
          small
          icon="dl"
          onClick={downloadLogs}
          title="Download every log and its backups, secrets removed"
        >
          Download
        </Btn>
      }
    >
      <div className={s.logBar}>
        <Select
          id="log-viewer-source"
          aria-label="Log"
          defaultValue="app"
          options={LOG_SOURCES.map((option) => ({ value: option.value, label: option.label }))}
          onChange={(event) => {
            view.current.source = event.target.value || 'app';
            reload();
          }}
        />
        <Segmented
          id="log-viewer-level"
          label="Level"
          value={level}
          itemAttr="data-level"
          options={LOG_LEVELS.map((option) => ({ value: option.level, label: option.label }))}
          onChange={(next) => {
            setLevel(next);
            view.current.level = next;
            reload();
          }}
        />
        <TextField
          id="log-viewer-search"
          className={s.logSearch}
          icon="search"
          aria-label="Filter lines"
          placeholder="Filter lines…"
          onChange={(event) => {
            const value = event.target.value;
            if (searchTimer.current) clearTimeout(searchTimer.current);
            searchTimer.current = setTimeout(() => {
              view.current.search = (value || '').trim();
              reload();
            }, LOG_SEARCH_DELAY_MS);
          }}
        />
      </div>
      <div className={s.logTerm}>
        <div className="log-viewer-terminal" id="log-viewer-terminal" ref={terminal}>
          <div className="log-viewer-lines" id="log-viewer-lines">
            {rows.map((r) => (
              <Line key={r.id} text={r.text} className={r.className} />
            ))}
          </div>
        </div>
      </div>
      <div className={s.logFoot}>
        <StatusWords id="log-viewer-live-indicator" tone={streaming ? 'ok' : 'idle'}>
          {streaming ? 'Live' : 'Not live'}
        </StatusWords>
        <span className={s.logCount} id="log-viewer-line-count">
          {count} lines
        </span>
        <label className={s.logFollow} htmlFor="log-viewer-autoscroll">
          <Switch id="log-viewer-autoscroll" defaultChecked ref={autoscroll} />
          Auto-scroll
        </label>
        <Btn small kind="ghost" onClick={copy} title="Copy the lines shown">
          Copy
        </Btn>
        <Btn small kind="ghost" onClick={clear} title="Clear the lines shown">
          Clear
        </Btn>
      </div>
    </Card>
  );
}
