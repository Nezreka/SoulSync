/**
 * Report a problem (plan 4.6): what happened, where, a switch per part of
 * the diagnostics with the exact text below it, then GitHub, Discord or a
 * file. The server builds every part with secrets removed
 * (POST /api/help/bundle) and returns each part's text whether it is
 * included or not, so a switch flips the preview at once by the server's
 * own join rule (api.ts joinBundle). A moment later the server builds it
 * again with the same choices and its text becomes the preview, so what
 * you read is what gets copied, byte for byte.
 *
 * Nothing leaves this server until a send button is clicked. Admin only.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { Bundle, BundlePart } from './api';
import type { ViewProps } from './view-props';

import './pages/register';
import a from './admin.module.css';
import {
  allLogsURL,
  bundleHeader,
  getBundle,
  getLogs,
  joinBundle,
  saveText,
  startDownload,
} from './api';
import { formatBytes, textBytes } from './format';
import { Glyph } from './glyphs';
import h from './help.module.css';
import { Icon } from './icons';
import { openExternal } from './links';
import { isVideoSide } from './page';
import { pageHelpFor } from './pages';
import { useHelpStore } from './store';
import { Switch } from './switch';
import { Label } from './ui';

const NOTE_MAX = 4000;
/** after a switch or a pause in typing, the server builds the bundle again */
const REFRESH_MS = 450;

type Sending = 'github' | 'discord' | 'download';

interface Answer {
  key: string;
  bundle: Bundle;
}

const keyOf = (note: string, where: string, ids: string[]) =>
  JSON.stringify([note.trim(), where, ids]);

/** "Downloads, Music": the page the sheet is about, and the side */
export function whereFor(page: string): string {
  const title = pageHelpFor(page).title || 'This page';
  return `${title}, ${isVideoSide() ? 'Video' : 'Music'}`;
}

export function ReportView({ isAdmin, page }: ViewProps) {
  const update = useHelpStore((st) => st.update);
  const where = useMemo(() => whereFor(page), [page]);
  const [note, setNote] = useState('');
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [included, setIncluded] = useState<Record<string, boolean>>({});
  const [loadError, setLoadError] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [attachLogs, setAttachLogs] = useState(false);
  const [logsSize, setLogsSize] = useState<number | null>(null);
  const [sending, setSending] = useState<Sending | null>(null);
  const [sent, setSent] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null);
  const [manualCopy, setManualCopy] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const parts: BundlePart[] = answer?.bundle.parts ?? [];
  const ids = parts.filter((p) => included[p.id]).map((p) => p.id);
  const key = keyOf(note, where, ids);
  const current = !!answer && answer.key === key;

  const preview = useMemo(() => {
    if (!answer) return '';
    if (answer.key === key) return answer.bundle.text;
    return joinBundle(
      bundleHeader(answer.bundle.text),
      note.trim(),
      where,
      parts.map((p) => ({ included: !!included[p.id], text: p.text })),
    );
  }, [answer, key, note, where, parts, included]);
  const size = useMemo(() => textBytes(preview), [preview]);

  const fetchBundle = useCallback(
    async (req: { note: string; where: string; ids?: string[] }): Promise<Bundle | null> => {
      ctrl.current?.abort();
      const c = new AbortController();
      ctrl.current = c;
      let res;
      try {
        res = await getBundle(
          { note: req.note.trim(), where: req.where, ...(req.ids ? { parts: req.ids } : {}) },
          c.signal,
        );
      } catch {
        return null;
      }
      if (c.signal.aborted) return null;
      if (!res.ok) {
        setRefreshError(res.error);
        return null;
      }
      const bundle = res.data;
      const got = bundle.parts.filter((p) => p.included).map((p) => p.id);
      setAnswer({ key: keyOf(req.note, req.where, got), bundle });
      setRefreshError(null);
      return bundle;
    },
    [],
  );

  // first build: the server's default parts
  useEffect(() => {
    if (!isAdmin) return;
    let live = true;
    void (async () => {
      const bundle = await fetchBundle({ note: '', where });
      if (!live) return;
      if (!bundle) {
        setLoadError(true);
        return;
      }
      setLoadError(false);
      setIncluded(Object.fromEntries(bundle.parts.map((p) => [p.id, p.included])));
    })();
    return () => {
      live = false;
      ctrl.current?.abort();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [isAdmin, where, fetchBundle]);

  // the choices moved: build again after a short pause
  useEffect(() => {
    // a build queued for the choices before this one is stale either way
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (!answer || current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void fetchBundle({ note, where, ids });
    }, REFRESH_MS);
    // key is note, where and the included ids: what the server is asked
  }, [key]);

  /** the server's answer for what's on screen now, building it if it's behind */
  const settle = async (): Promise<Bundle | null> => {
    if (answer && current) return answer.bundle;
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    return fetchBundle({ note, where, ids });
  };

  const retry = async () => {
    setLoadError(false);
    const bundle = await fetchBundle({ note, where });
    if (!bundle) {
      setLoadError(true);
      return;
    }
    setIncluded(Object.fromEntries(bundle.parts.map((p) => [p.id, p.included])));
  };

  const toggleAttach = (on: boolean) => {
    setAttachLogs(on);
    if (on && logsSize == null) {
      void getLogs().then((res) => {
        if (res.ok) setLogsSize(res.data.total_bytes);
      });
    }
  };

  const downloads = (bundle: Bundle) => {
    saveText(bundle.text, bundle.filename);
    if (attachLogs) startDownload(allLogsURL(false));
  };

  const sendGithub = async () => {
    setSending('github');
    setSent(null);
    // a tab opened after a wait is a popup to the browser: open it now, fill it after
    const early = current ? null : window.open('', '_blank');
    const bundle = await settle();
    setSending(null);
    if (!bundle) {
      early?.close();
      setSent({ tone: 'err', text: "Couldn't build the report. Nothing was sent." });
      return;
    }
    if (early) {
      early.opener = null;
      early.location.href = bundle.github.url;
    } else {
      openExternal(bundle.github.url);
    }
    downloads(bundle);
    setSent({
      tone: 'ok',
      text: `GitHub is open in a new tab. Drag ${bundle.filename}${attachLogs ? ' and the logs zip' : ''} from your downloads into the issue.`,
    });
  };

  const sendDiscord = async () => {
    setSending('discord');
    setSent(null);
    setManualCopy(null);
    const bundle = await settle();
    setSending(null);
    if (!bundle) {
      setSent({ tone: 'err', text: "Couldn't build the summary. Nothing was copied." });
      return;
    }
    if (await copyText(bundle.discord)) {
      setSent({
        tone: 'ok',
        text: 'Copied. It fits one Discord message; if someone asks for more, send the bundle file.',
      });
    } else {
      setManualCopy(bundle.discord);
      setSent({ tone: 'err', text: "Your browser didn't allow the copy. Select it below." });
    }
  };

  const sendFile = async () => {
    setSending('download');
    setSent(null);
    const bundle = await settle();
    setSending(null);
    if (!bundle) {
      setSent({ tone: 'err', text: "Couldn't build the bundle. Try again." });
      return;
    }
    downloads(bundle);
    setSent({
      tone: 'ok',
      text: `Saved ${bundle.filename}${attachLogs ? ' and the logs zip' : ''}.`,
    });
  };

  if (!isAdmin) return null;

  const version = update?.current_version || answer?.bundle.version || null;
  const platform = update ? (update.is_docker ? 'Docker' : null) : null;
  const ready = !!answer && !loadError;

  return (
    <>
      <Label>What happened?</Label>
      <textarea
        className={a.textarea}
        aria-label="What happened"
        placeholder="What you did, what you expected, and what happened instead."
        value={note}
        maxLength={NOTE_MAX}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className={a.where}>
        <span>Where</span>
        <span className={a.tag}>
          <Icon name={pageHelpFor(page).icon} size={12} />
          {where}
        </span>
        {version ? (
          <span className={a.tag}>{[version, platform].filter(Boolean).join(' · ')}</span>
        ) : null}
        {note.length > NOTE_MAX - 400 ? (
          <span className={a.counter}>
            {note.length} of {NOTE_MAX}
          </span>
        ) : null}
      </div>

      <Label>What's included</Label>
      {loadError ? (
        <div className={a.calmCard} role="alert">
          <p>
            <b>Couldn't build the diagnostics.</b> {refreshError ?? "The server didn't answer."}{' '}
            Nothing was sent.
          </p>
          <div className={h.acts}>
            <button type="button" className={h.btn} data-kind="secondary" onClick={retry}>
              <Icon name="refresh" size={14} />
              Try again
            </button>
          </div>
        </div>
      ) : !answer ? (
        <div className={a.inc} aria-busy="true">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className={a.incRow} data-skeleton>
              <span className={a.skel} />
            </div>
          ))}
        </div>
      ) : (
        <div className={a.inc} role="group" aria-label="What's included">
          {parts.map((p) => (
            <PartSwitch
              key={p.id}
              part={p}
              on={!!included[p.id]}
              onChange={(on) => setIncluded((m) => ({ ...m, [p.id]: on }))}
            />
          ))}
          <div className={a.incRow} data-extra>
            <span className={a.incText}>
              <span className={a.incTitle}>Attach full logs (zip)</span>
              <span className={a.incHint}>
                Every log with its backups, secrets removed. Saved beside the bundle.
              </span>
            </span>
            {attachLogs && logsSize != null ? (
              <span className={a.incSize}>{formatBytes(logsSize)}</span>
            ) : null}
            <Switch label="Attach full logs (zip)" on={attachLogs} onChange={toggleAttach} />
          </div>
        </div>
      )}

      {ready ? (
        <>
          <h3 className={`${h.label} ${a.labelRow}`}>
            <span>Exactly what gets shared</span>
            <span className={a.labelEnd} aria-live="polite">
              {formatBytes(size)}
            </span>
          </h3>
          <Preview text={preview} />
          {refreshError ? (
            <p className={a.calm}>
              The server didn't build it again ({refreshError}). This is the last version it sent.
            </p>
          ) : null}
        </>
      ) : null}

      <Label>Send it</Label>
      <div className={a.send}>
        <SendButton
          primary
          icon={<Icon name="git" size={16} />}
          title="Open a GitHub issue"
          sub="Prefilled with your note, version and platform; the bundle downloads to drop in"
          busy={sending === 'github'}
          disabled={!ready || !!sending}
          onClick={sendGithub}
        />
        <SendButton
          icon={<Glyph name="copy" size={16} />}
          title="Copy for Discord"
          sub="A short summary that fits one message"
          busy={sending === 'discord'}
          disabled={!ready || !!sending}
          onClick={sendDiscord}
        />
        <SendButton
          icon={<Icon name="file" size={16} />}
          title="Download the bundle"
          sub={answer?.bundle.filename ?? 'soulsync-diagnostics.txt'}
          busy={sending === 'download'}
          disabled={!ready || !!sending}
          onClick={sendFile}
        />
      </div>
      <p className={a.sent} role="status" data-tone={sent?.tone}>
        {sent ? (
          <>
            {sent.tone === 'ok' ? <Icon name="check" size={14} /> : <Glyph name="info" size={14} />}
            {sent.text}
          </>
        ) : null}
      </p>
      {manualCopy ? (
        <textarea
          className={a.textarea}
          data-copy
          readOnly
          aria-label="Summary for Discord"
          value={manualCopy}
          ref={(el) => el?.select()}
        />
      ) : null}
      <p className={a.privacy}>
        <Glyph name="shield" size={15} />
        <span>
          Nothing leaves this server until you click one of these. What you see above is exactly
          what gets copied.
        </span>
      </p>
    </>
  );
}

function PartSwitch({
  part,
  on,
  onChange,
}: {
  part: BundlePart;
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className={a.incRow} data-off={on ? undefined : ''}>
      <span className={a.incText}>
        <span className={a.incTitle}>{part.label}</span>
        {part.hint ? <span className={a.incHint}>{part.hint}</span> : null}
      </span>
      <span className={a.incSize}>{formatBytes(part.bytes)}</span>
      <Switch label={part.label} on={on} onChange={onChange} />
    </div>
  );
}

function SendButton({
  primary,
  icon,
  title,
  sub,
  busy,
  disabled,
  onClick,
}: {
  primary?: boolean;
  icon: ReactNode;
  title: string;
  sub: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={a.sendB}
      data-primary={primary ? '' : undefined}
      disabled={disabled}
      aria-busy={busy || undefined}
      onClick={onClick}
    >
      <span className={a.sendIcon} aria-hidden="true">
        {busy ? <Icon name="refresh" size={16} className={a.spin} /> : icon}
      </span>
      <span className={a.sendText}>
        <span className={a.sendTitle}>{title}</span>
        <span className={a.sendSub}>{sub}</span>
      </span>
    </button>
  );
}

/** the text as it will be shared: headings tinted, every *** marked so the redaction shows */
function Preview({ text }: { text: string }) {
  const lines = text.replace(/\n$/, '').split('\n');
  return (
    // a scroll box that holds no controls still has to be reachable by keyboard
    <pre
      className={a.pre}
      tabIndex={0}
      aria-label="Exactly what gets shared"
      data-testid="bundle-preview"
    >
      {lines.map((line, i) => {
        const head = i === 0 || /^== .* ==$/.test(line) || line === 'What happened';
        const pieces = line.split('***');
        return (
          <span key={i} className={head ? a.preHead : undefined}>
            {pieces.map((piece, j) => (
              <span key={j}>
                {j > 0 ? <mark className={a.preSecret}>***</mark> : null}
                {piece}
              </span>
            ))}
            {i < lines.length - 1 ? '\n' : ''}
          </span>
        );
      })}
    </pre>
  );
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the old way */
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}
