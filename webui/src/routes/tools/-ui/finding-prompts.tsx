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
 * Styling is transcribed inline from the vanilla rather than moved to CSS. These
 * overlays never had classes of their own beyond `modal-overlay`, so there is
 * nothing in style.css to inherit — writing new classes would be inventing a
 * look rather than porting one.
 *
 * NOTE for P7: `showWitnessMeDialog`, `MASS_ORPHAN_THRESHOLD` and
 * `_isMassOrphanFix` live in core.js but are called ONLY from the findings
 * region of enrichment.js (verified by grep across webui/static). They become
 * dead when that region goes, and core.js:622-702 should be deleted with it.
 */

import type { CSSProperties } from 'react';

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  AcoustidFixAction,
  BackfillFixAction,
  DeadFileFixAction,
  OrphanFixAction,
  QualityFixAction,
  RetagFixAction,
} from '../-tools.types';

// ── Transcribed inline styles ────────────────────────────────────────────────

const OVERLAY: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 10000,
};

const box = (maxWidth: number): CSSProperties => ({
  background: '#1e1e2e',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: '16px',
  padding: '28px',
  maxWidth: `${maxWidth}px`,
  width: '90%',
  textAlign: 'center',
});

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

const ROW: CSSProperties = { display: 'flex', gap: '10px', justifyContent: 'center' };
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
  maxWidth: number;
  title: string;
  body: string;
  cancelId: string;
  onCancel: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="modal-overlay"
      style={OVERLAY}
      onClick={(event) => {
        // Only a click on the backdrop itself cancels — the vanilla checks
        // `e.target === overlay` for exactly this reason.
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div style={box(maxWidth)}>
        <div style={TITLE}>{title}</div>
        <div style={BODY}>{body}</div>
        {children}
        <button id={cancelId} type="button" style={CANCEL} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
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
  resolve,
}: {
  candidates?: string[];
  resolve: (value: string | null) => void;
}) {
  // An ambiguous fingerprint names several possible recordings. The finding
  // carries them, so let the user PICK one — retag/relocate then act on that
  // choice ('retag:<n>') instead of refusing and pointing at manual tools.
  const ambiguous = (candidates?.length ?? 0) > 1;
  const [picked, setPicked] = useState(0);
  const act = (base: 'retag' | 'relocate') => resolve(ambiguous ? `${base}:${picked}` : base);
  return (
    <PromptOverlay
      maxWidth={460}
      title="AcoustID Mismatch"
      body={
        ambiguous
          ? 'The fingerprint matches several recordings. Pick the one this file really is, then choose how to fix it.'
          : "The audio fingerprint doesn't match the expected track. Choose how to fix it."
      }
      cancelId="_acid-cancel"
      onCancel={() => resolve(null)}
    >
      {ambiguous ? (
        <div style={{ display: 'grid', gap: 6, margin: '0 0 12px', textAlign: 'left' }}>
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
        re-imported into the correct artist/album &bull; Re-download = add correct track to wishlist
        &amp; delete wrong file &bull; Delete = remove file and DB entry
      </div>
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

/** Two requests wear one button here: write the library's values into the
 *  files, and write them EVEN OVER the fields this user edited by hand.
 *
 *  lib2 keeps a per-field override layer and a re-tag respects it, so the
 *  normal apply leaves those alone. That is the right default and the wrong
 *  thing to make silent — someone who fixed a title six months ago and has
 *  since fixed the catalogue too needs a way to say "the catalogue wins now".
 *  The count is what makes the choice informed rather than a coin toss. */
function RetagPrompt({
  count,
  manualCount,
  resolve,
}: {
  count: number;
  manualCount: number;
  resolve: (value: RetagFixAction | null) => void;
}) {
  const safeCount = Math.max(count - manualCount, 0);
  return (
    <PromptOverlay
      maxWidth={480}
      title={`Apply Tags (${count})`}
      body={
        manualCount > 0
          ? `${count} finding(s), ${manualCount} of them holding a field you set by hand. Applying keeps your value unless you say otherwise.`
          : `Write the library's metadata into ${count} file(s)? Nothing here was edited by hand.`
      }
      cancelId="_retag-cancel"
      onCancel={() => resolve(null)}
    >
      <div style={ROW_WRAP}>
        <button id="_retag-safe" type="button" style={GREEN} onClick={() => resolve('safe')}>
          {manualCount > 0 ? `Keep My Edits (${safeCount} changed)` : `Apply All ${count}`}
        </button>
        {manualCount > 0 ? (
          <button
            id="_retag-overwrite"
            type="button"
            style={INDIGO_LIGHT}
            onClick={() => resolve('overwrite_manual')}
          >
            Overwrite My Edits Too ({count})
          </button>
        ) : null}
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

  useEffect(() => {
    // The vanilla defers focus by 100ms; React has already committed the node by
    // the time this effect runs, so it can focus immediately.
    inputRef.current?.focus();
  }, []);

  return (
    <div
      className="confirm-modal-overlay"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.7)',
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) resolve(false);
      }}
    >
      <div
        style={{
          background: 'var(--bg-secondary, #1e1e2e)',
          border: '2px solid #e74c3c',
          borderRadius: '12px',
          padding: '28px',
          maxWidth: '480px',
          width: '90%',
          color: 'var(--text-primary, #fff)',
          fontFamily: 'inherit',
        }}
      >
        <h3 style={{ margin: '0 0 8px', color: '#e74c3c', fontSize: '1.2em' }}>
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
          <strong style={{ color: '#e74c3c' }}>witness me</strong> below:
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
            border: '1px solid #555',
            borderRadius: '6px',
            background: 'var(--bg-primary, #111)',
            color: 'var(--text-primary, #fff)',
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
              border: '1px solid #555',
              borderRadius: '6px',
              background: 'transparent',
              color: 'var(--text-primary, #fff)',
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
              background: match ? '#e74c3c' : '#555',
              color: match ? '#fff' : '#888',
              cursor: match ? 'pointer' : 'not-allowed',
              fontSize: '0.9em',
              fontWeight: 600,
              transition: 'all 0.2s',
            }}
          >
            Delete Files
          </button>
        </div>
      </div>
    </div>
  );
}

// ── The hook ─────────────────────────────────────────────────────────────────

type PendingPrompt =
  | { kind: 'orphan'; resolve: (value: OrphanFixAction | null) => void }
  | { kind: 'dead'; resolve: (value: DeadFileFixAction | null) => void }
  | { kind: 'acoustid'; candidates?: string[]; resolve: (value: string | null) => void }
  | { kind: 'quality'; resolve: (value: QualityFixAction | null) => void }
  | { kind: 'backfill'; count: number; resolve: (value: BackfillFixAction | null) => void }
  | {
      kind: 'retag';
      count: number;
      manualCount: number;
      resolve: (value: RetagFixAction | null) => void;
    }
  | { kind: 'witness'; count: number; resolve: (value: boolean) => void };

export interface FindingPrompts {
  promptOrphan: () => Promise<OrphanFixAction | null>;
  promptDeadFile: () => Promise<DeadFileFixAction | null>;
  promptAcoustid: (candidates?: string[]) => Promise<string | null>;
  promptQuality: () => Promise<QualityFixAction | null>;
  promptBackfill: (count: number) => Promise<BackfillFixAction | null>;
  promptRetag: (count: number, manualCount: number) => Promise<RetagFixAction | null>;
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
    (candidates?: string[]) =>
      new Promise<string | null>((resolve) => {
        setPending({ kind: 'acoustid', candidates, resolve });
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
  const promptRetag = useCallback(
    (count: number, manualCount: number) =>
      new Promise<RetagFixAction | null>((resolve) => {
        setPending({ kind: 'retag', count, manualCount, resolve });
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
        resolve={(v) => settle(pending.resolve as never, v)}
      />
    );
  } else if (pending?.kind === 'quality') {
    promptNode = <QualityPrompt resolve={(v) => settle(pending.resolve as never, v)} />;
  } else if (pending?.kind === 'backfill') {
    promptNode = (
      <BackfillPrompt count={pending.count} resolve={(v) => settle(pending.resolve as never, v)} />
    );
  } else if (pending?.kind === 'retag') {
    promptNode = (
      <RetagPrompt
        count={pending.count}
        manualCount={pending.manualCount}
        resolve={(v) => settle(pending.resolve as never, v)}
      />
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
    promptRetag,
    promptWitnessMe,
    promptNode,
  };
}
