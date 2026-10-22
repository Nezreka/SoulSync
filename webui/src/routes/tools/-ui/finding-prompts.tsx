/**
 * The finding fix prompts — five action choosers plus the mass-deletion gate.
 *
 * The vanilla builds each of these by hand as a detached overlay and resolves a
 * promise from the button handlers (`_promptOrphanAction` and friends,
 * `showWitnessMeDialog` in core.js). Callers `await` them inline, and both bulk
 * paths depend on that: they ask for every needed action FIRST, then run.
 *
 * `useFindingPrompts` keeps that shape. It hands back the same await-able calls
 * plus one node to render; the node is the currently-open overlay, or null.
 *
 * Each overlay sits on DialogFrame (focus trap, Escape, outside press, scroll
 * lock); Escape and outside press cancel, like the Cancel button. The overlay
 * and box chrome lives in finding-prompts.module.css. The content inside is
 * still styled inline, transcribed from the vanilla: these overlays never had
 * classes of their own beyond `modal-overlay`, so there is nothing in style.css
 * to inherit.
 *
 * NOTE for P7: `showWitnessMeDialog`, `MASS_ORPHAN_THRESHOLD` and
 * `_isMassOrphanFix` live in core.js but are called ONLY from the findings
 * region of enrichment.js (verified by grep across webui/static). They become
 * dead when that region goes, and core.js:622-702 should be deleted with it.
 */

import type { CSSProperties } from 'react';

import clsx from 'clsx';
import { useCallback, useRef, useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type {
  AcoustidFixAction,
  BackfillFixAction,
  DeadFileFixAction,
  OrphanFixAction,
  QualityFixAction,
} from '../-tools.types';

import styles from './finding-prompts.module.css';

// ── Transcribed inline styles ────────────────────────────────────────────────

/** The box's max-width per prompt, in px. */
const BOX_WIDTH = { 380: styles.box380, 420: styles.box420, 460: styles.box460 };

const TITLE: CSSProperties = {
  fontSize: '1.1em',
  fontWeight: 600,
  color: '#fff',
  marginBottom: '8px',
};

const BODY: CSSProperties = {
  fontSize: '0.88em',
  color: 'rgba(255,255,255,0.6)',
  marginBottom: '20px',
};

const ROW: CSSProperties = {
  display: 'flex',
  gap: '10px',
  justifyContent: 'center',
};
const ROW_WRAP: CSSProperties = { ...ROW, flexWrap: 'wrap' };

const HINT: CSSProperties = {
  marginTop: '12px',
  fontSize: '0.78em',
  color: 'rgba(255,255,255,0.35)',
  lineHeight: 1.4,
};

const CANCEL: CSSProperties = {
  marginTop: '12px',
  padding: '6px 16px',
  border: 'none',
  background: 'none',
  color: 'rgba(255,255,255,0.4)',
  cursor: 'pointer',
  fontSize: '0.82em',
  fontFamily: 'inherit',
};

const btn = (
  border: string,
  background: string,
  color: string,
  fontWeight: number,
): CSSProperties => ({
  padding: '10px 20px',
  borderRadius: '10px',
  border: `1px solid ${border}`,
  background,
  color,
  fontWeight,
  cursor: 'pointer',
  fontFamily: 'inherit',
});

const GREEN = btn('rgba(29,185,84,0.4)', 'rgba(29,185,84,0.15)', '#1db954', 600);
const RED = btn('rgba(239,68,68,0.4)', 'rgba(239,68,68,0.1)', '#ef4444', 500);
const INDIGO = btn('rgba(102,126,234,0.4)', 'rgba(102,126,234,0.15)', '#667eea', 600);
/** The backfill "just clear" button is the only indigo one at weight 500. */
const INDIGO_LIGHT = { ...INDIGO, fontWeight: 500 };
const AMBER = btn('rgba(245,158,11,0.4)', 'rgba(245,158,11,0.12)', '#f59e0b', 600);
const GREY = btn('rgba(255,255,255,0.18)', 'rgba(255,255,255,0.05)', 'rgba(255,255,255,0.7)', 500);

// ── The prompt shell ─────────────────────────────────────────────────────────

function PromptOverlay({
  maxWidth,
  title,
  body,
  cancelId,
  onCancel,
  children,
}: {
  maxWidth: keyof typeof BOX_WIDTH;
  title: string;
  body: string;
  cancelId: string;
  onCancel: () => void;
  children: React.ReactNode;
}) {
  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        // Only a press on the backdrop itself (or Escape) cancels — the vanilla
        // checks `e.target === overlay` for exactly this reason.
        if (!next) onCancel();
      }}
      viewportClassName={clsx('modal-overlay', styles.overlay)}
      className={clsx(styles.box, BOX_WIDTH[maxWidth])}
      aria-label={title}
    >
      <div style={TITLE}>{title}</div>
      <div style={BODY}>{body}</div>
      {children}
      <button id={cancelId} type="button" style={CANCEL} onClick={onCancel}>
        Cancel
      </button>
    </DialogFrame>
  );
}

// ── The five action prompts ──────────────────────────────────────────────────

function OrphanPrompt({ resolve }: { resolve: (value: OrphanFixAction | null) => void }) {
  return (
    <PromptOverlay
      maxWidth={380}
      title="Orphan File Action"
      body="Choose how to handle orphan files. Staging is safe and reversible."
      cancelId="_orphan-cancel"
      onCancel={() => resolve(null)}
    >
      <div style={ROW}>
        <button id="_orphan-staging" type="button" style={GREEN} onClick={() => resolve('staging')}>
          Move to Staging
        </button>
        <button id="_orphan-delete" type="button" style={RED} onClick={() => resolve('delete')}>
          Delete
        </button>
      </div>
    </PromptOverlay>
  );
}

function DeadFilePrompt({ resolve }: { resolve: (value: DeadFileFixAction | null) => void }) {
  return (
    <PromptOverlay
      maxWidth={420}
      title="Dead File Action"
      body="This track's file no longer exists on disk. Choose how to handle it."
      cancelId="_dead-cancel"
      onCancel={() => resolve(null)}
    >
      <div style={ROW}>
        <button
          id="_dead-redownload"
          type="button"
          style={GREEN}
          onClick={() => resolve('redownload')}
        >
          Re-download
        </button>
        <button id="_dead-remove" type="button" style={RED} onClick={() => resolve('remove')}>
          Remove from DB
        </button>
      </div>
    </PromptOverlay>
  );
}

function AcoustidPrompt({
  candidates,
  count,
  resolve,
}: {
  candidates?: string[];
  count?: number;
  resolve: (value: string | null) => void;
}) {
  // An ambiguous fingerprint names several possible recordings. The finding
  // carries them, so let the user PICK one — retag/relocate then act on that
  // choice ('retag:<n>') instead of refusing and pointing at manual tools.
  const ambiguous = (candidates?.length ?? 0) > 1;
  const [picked, setPicked] = useState(0);
  // #1289: Relocate is destructive (moves the file out of the library), so it
  // gets a second confirmation step. Retag/Re-download/Delete act immediately.
  const [confirmRelocate, setConfirmRelocate] = useState(false);
  const act = (base: 'retag' | 'relocate') => {
    if (base === 'relocate' && !confirmRelocate) {
      setConfirmRelocate(true);
      return;
    }
    resolve(ambiguous ? `${base}:${picked}` : base);
  };
  return (
    <PromptOverlay
      maxWidth={460}
      title={confirmRelocate ? 'Confirm Relocate' : 'AcoustID Mismatch'}
      body={
        confirmRelocate
          ? count && count > 1
            ? `This will move ${count} files out of your library into Staging for re-import, and remove their library entries. Continue?`
            : 'This will move the file out of your library into Staging for re-import, and remove its library entry. Continue?'
          : ambiguous
            ? 'The fingerprint matches several recordings. Pick the one this file really is, then choose how to fix it.'
            : "The audio fingerprint doesn't match the expected track. Choose how to fix it."
      }
      cancelId="_acid-cancel"
      onCancel={() => resolve(null)}
    >
      {confirmRelocate ? (
        <div style={ROW_WRAP}>
          <button
            id="_acid-relocate-confirm"
            type="button"
            style={AMBER}
            onClick={() => act('relocate')}
          >
            {count && count > 1 ? `Yes, relocate ${count} files` : 'Yes, relocate it'}
          </button>
          <button
            id="_acid-relocate-back"
            type="button"
            style={INDIGO}
            onClick={() => setConfirmRelocate(false)}
          >
            Back
          </button>
        </div>
      ) : (
        <>
          <div
            style={{
              fontSize: 12,
              opacity: 0.7,
              margin: '0 0 12px',
              textAlign: 'left',
            }}
          >
            Picks which catalogue recording this file really is — nothing is re-downloaded.
            <br />
            Saved as: library row + file tags (relocate also moves the file). Affects next sync:
            indirectly.
          </div>
          {ambiguous ? (
            <div
              style={{
                display: 'grid',
                gap: 6,
                margin: '0 0 12px',
                textAlign: 'left',
              }}
            >
              {candidates!.map((label, i) => (
                <label
                  key={i}
                  style={{
                    display: 'flex',
                    gap: 8,
                    alignItems: 'baseline',
                    cursor: 'pointer',
                    fontSize: 13,
                  }}
                >
                  <input
                    type="radio"
                    name="_acid-candidate"
                    checked={picked === i}
                    onChange={() => setPicked(i)}
                    data-acid-candidate={i}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          ) : null}
          <div style={ROW_WRAP}>
            <button id="_acid-retag" type="button" style={INDIGO} onClick={() => act('retag')}>
              Retag
            </button>
            <button id="_acid-relocate" type="button" style={AMBER} onClick={() => act('relocate')}>
              Relocate
            </button>
            <button
              id="_acid-redownload"
              type="button"
              style={GREEN}
              onClick={() => resolve('redownload')}
            >
              Re-download
            </button>
            <button id="_acid-delete" type="button" style={RED} onClick={() => resolve('delete')}>
              Delete
            </button>
          </div>
          <div style={HINT}>
            Retag = update metadata in place &bull; Relocate = retag + move to Staging so it&apos;s
            re-imported into the correct artist/album &bull; Re-download = add correct track to
            wishlist &amp; delete wrong file &bull; Delete = remove file and DB entry
          </div>
        </>
      )}
    </PromptOverlay>
  );
}

function QualityPrompt({ resolve }: { resolve: (value: QualityFixAction | null) => void }) {
  return (
    <PromptOverlay
      maxWidth={460}
      title="Low-Quality Track"
      body="This file is below your quality profile. Choose what to do."
      cancelId="_qual-cancel"
      onCancel={() => resolve(null)}
    >
      <div style={ROW_WRAP}>
        <button
          id="_qual-redownload"
          type="button"
          style={GREEN}
          onClick={() => resolve('redownload')}
        >
          Re-download
        </button>
        <button id="_qual-delete" type="button" style={RED} onClick={() => resolve('delete')}>
          Delete
        </button>
        <button id="_qual-ignore" type="button" style={GREY} onClick={() => resolve('ignore')}>
          Ignore
        </button>
      </div>
      <div style={HINT}>
        Re-download = add to wishlist for a better-quality copy &amp; delete this file &bull; Delete
        = remove file and DB entry &bull; Ignore = keep the file and dismiss this finding
      </div>
    </PromptOverlay>
  );
}

/**
 * `_promptDiscographyBackfillAction`. Every label switches on count <= 1, and the
 * vanilla sets them via textContent rather than interpolating into the HTML —
 * React escapes by default, so the reason for that split disappears here.
 *
 * That also drops the `_dbf-header` / `_dbf-body` ids: they existed ONLY as
 * textContent targets, nothing queries or styles them. It is the one id
 * difference an artefact diff of this file will report.
 */
function BackfillPrompt({
  count,
  resolve,
}: {
  count: number;
  resolve: (value: BackfillFixAction | null) => void;
}) {
  const isSingle = count <= 1;
  return (
    <PromptOverlay
      maxWidth={460}
      title={isSingle ? 'Missing Discography Track' : `Missing Discography Tracks (${count})`}
      body={
        isSingle
          ? 'Add this track to the wishlist for automatic download, or just clear the finding?'
          : `Add all ${count} selected tracks to the wishlist for automatic download, or just clear the findings?`
      }
      cancelId="_dbf-cancel"
      onCancel={() => resolve(null)}
    >
      <div style={ROW_WRAP}>
        <button
          id="_dbf-add"
          type="button"
          style={GREEN}
          onClick={() => resolve('add_to_wishlist')}
        >
          {isSingle ? 'Add to Wishlist' : `Add All ${count} to Wishlist`}
        </button>
        <button
          id="_dbf-dismiss"
          type="button"
          style={INDIGO_LIGHT}
          onClick={() => resolve('dismiss')}
        >
          {isSingle ? 'Just Clear Finding' : 'Just Clear Findings'}
        </button>
      </div>
    </PromptOverlay>
  );
}

// ── The mass-deletion gate ───────────────────────────────────────────────────

/** The exact phrase, compared case-insensitively after trimming. */
const WITNESS_PHRASE = 'witness me';

/**
 * `showWitnessMeDialog` (core.js). Confirm stays disabled until the phrase is
 * typed; the disabled styling is driven off the same check rather than a class,
 * matching the vanilla's inline restyling on every input event.
 */
function WitnessMeDialog({
  orphanCount,
  resolve,
}: {
  orphanCount: number;
  resolve: (value: boolean) => void;
}) {
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);
  const match = text.trim().toLowerCase() === WITNESS_PHRASE;

  // The vanilla defers focus to the input by 100ms; the dialog focuses it as
  // soon as it opens.
  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        if (!next) resolve(false);
      }}
      viewportClassName={clsx('confirm-modal-overlay', styles.witnessOverlay)}
      className={styles.witnessBox}
      initialFocus={inputRef}
      aria-label="Mass Deletion Warning"
    >
      <h3 style={{ margin: '0 0 8px', color: 'var(--danger)', fontSize: '1.2em' }}>
        Mass Deletion Warning
      </h3>
      <p style={{ margin: '0 0 12px', fontSize: '0.95em', opacity: 0.9 }}>
        You are about to <strong>permanently delete {orphanCount.toLocaleString()} files</strong>{' '}
        from your disk.
      </p>
      <p style={{ margin: '0 0 12px', fontSize: '0.9em', opacity: 0.75 }}>
        This many orphans usually means a path mismatch between your database and filesystem — not
        actual orphan files. A previous user lost their entire library this way.
      </p>
      <p style={{ margin: '0 0 6px', fontSize: '0.9em', opacity: 0.9 }}>
        To confirm you understand the risk, type{' '}
        <strong style={{ color: 'var(--danger)' }}>witness me</strong> below:
      </p>
      <input
        type="text"
        id="witness-me-input"
        ref={inputRef}
        autoComplete="off"
        spellCheck={false}
        placeholder="Type the phrase here..."
        value={text}
        onChange={(event) => setText(event.target.value)}
        style={{
          width: '100%',
          padding: '10px',
          border: '1px solid var(--white-a30)',
          borderRadius: '6px',
          background: 'var(--bg-primary)',
          color: 'var(--text-primary)',
          fontSize: '1em',
          margin: '8px 0 16px',
          boxSizing: 'border-box',
        }}
      />
      <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
        <button
          id="witness-cancel"
          type="button"
          onClick={() => resolve(false)}
          style={{
            padding: '8px 20px',
            border: '1px solid var(--white-a30)',
            borderRadius: '6px',
            background: 'transparent',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            fontSize: '0.9em',
          }}
        >
          Cancel
        </button>
        <button
          id="witness-confirm"
          type="button"
          disabled={!match}
          onClick={() => resolve(true)}
          style={{
            padding: '8px 20px',
            border: 'none',
            borderRadius: '6px',
            background: match ? 'var(--danger)' : 'var(--white-a30)',
            color: match ? 'var(--text-1)' : 'var(--text-3)',
            cursor: match ? 'pointer' : 'not-allowed',
            fontSize: '0.9em',
            fontWeight: 600,
            transition: 'all 0.2s',
          }}
        >
          Delete Files
        </button>
      </div>
    </DialogFrame>
  );
}

// ── The hook ─────────────────────────────────────────────────────────────────

type PendingPrompt =
  | { kind: 'orphan'; resolve: (value: OrphanFixAction | null) => void }
  | { kind: 'dead'; resolve: (value: DeadFileFixAction | null) => void }
  | {
      kind: 'acoustid';
      candidates?: string[];
      count?: number;
      resolve: (value: string | null) => void;
    }
  | { kind: 'quality'; resolve: (value: QualityFixAction | null) => void }
  | {
      kind: 'backfill';
      count: number;
      resolve: (value: BackfillFixAction | null) => void;
    }
  | { kind: 'witness'; count: number; resolve: (value: boolean) => void };

export interface FindingPrompts {
  promptOrphan: () => Promise<OrphanFixAction | null>;
  promptDeadFile: () => Promise<DeadFileFixAction | null>;
  promptAcoustid: (candidates?: string[], count?: number) => Promise<string | null>;
  promptQuality: () => Promise<QualityFixAction | null>;
  promptBackfill: (count: number) => Promise<BackfillFixAction | null>;
  promptWitnessMe: (count: number) => Promise<boolean>;
  /** Render this somewhere inside the findings tab. */
  promptNode: React.ReactNode;
}

export function useFindingPrompts(): FindingPrompts {
  const [pending, setPending] = useState<PendingPrompt | null>(null);

  /** Resolve and tear down in one step so a prompt can never answer twice. */
  const settle = useCallback((resolve: (value: never) => void, value: unknown) => {
    setPending(null);
    (resolve as (v: unknown) => void)(value);
  }, []);

  const promptOrphan = useCallback(
    () =>
      new Promise<OrphanFixAction | null>((resolve) => {
        setPending({ kind: 'orphan', resolve });
      }),
    [],
  );
  const promptDeadFile = useCallback(
    () =>
      new Promise<DeadFileFixAction | null>((resolve) => {
        setPending({ kind: 'dead', resolve });
      }),
    [],
  );
  const promptAcoustid = useCallback(
    (candidates?: string[], count?: number) =>
      new Promise<string | null>((resolve) => {
        setPending({ kind: 'acoustid', candidates, count, resolve });
      }),
    [],
  );
  const promptQuality = useCallback(
    () =>
      new Promise<QualityFixAction | null>((resolve) => {
        setPending({ kind: 'quality', resolve });
      }),
    [],
  );
  const promptBackfill = useCallback(
    (count: number) =>
      new Promise<BackfillFixAction | null>((resolve) => {
        setPending({ kind: 'backfill', count, resolve });
      }),
    [],
  );
  const promptWitnessMe = useCallback(
    (count: number) =>
      new Promise<boolean>((resolve) => {
        setPending({ kind: 'witness', count, resolve });
      }),
    [],
  );

  let promptNode: React.ReactNode = null;
  if (pending?.kind === 'orphan') {
    promptNode = <OrphanPrompt resolve={(v) => settle(pending.resolve as never, v)} />;
  } else if (pending?.kind === 'dead') {
    promptNode = <DeadFilePrompt resolve={(v) => settle(pending.resolve as never, v)} />;
  } else if (pending?.kind === 'acoustid') {
    promptNode = (
      <AcoustidPrompt
        candidates={pending.candidates}
        count={pending.count}
        resolve={(v) => settle(pending.resolve as never, v)}
      />
    );
  } else if (pending?.kind === 'quality') {
    promptNode = <QualityPrompt resolve={(v) => settle(pending.resolve as never, v)} />;
  } else if (pending?.kind === 'backfill') {
    promptNode = (
      <BackfillPrompt count={pending.count} resolve={(v) => settle(pending.resolve as never, v)} />
    );
  } else if (pending?.kind === 'witness') {
    promptNode = (
      <WitnessMeDialog
        orphanCount={pending.count}
        resolve={(v) => settle(pending.resolve as never, v)}
      />
    );
  }

  return {
    promptOrphan,
    promptDeadFile,
    promptAcoustid,
    promptQuality,
    promptBackfill,
    promptWitnessMe,
    promptNode,
  };
}
