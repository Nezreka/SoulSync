/**
 * The pieces every setup screen is built from: the step frame (rail, header,
 * footer with the one primary action) and small controls. Kept here so the
 * screens read as content, not markup.
 */

import type { InputHTMLAttributes, ReactNode } from 'react';

import { appURL } from '@/platform/url-base';

import type { StepId } from './types';

import s from './onboarding.module.css';
import { canContinue, STEP_LABELS, visibleSteps } from './plan';
import { neighbours, useOnboarding } from './store';

export const LOGO_SRC = appURL('/static/trans2.png');

export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(' ');
}

export function CheckIcon({ size = 12, stroke = 3.2 }: { size?: number; stroke?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

export function ArrowIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function WarnIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function Spinner({ label = 'Working' }: { label?: string }) {
  return <span className={s.spinner} role="status" aria-label={label} />;
}

/** a status line with a dot: ok (accent), warn (amber), busy, or neutral */
export function Chip({ tone, children }: { tone?: 'ok' | 'warn' | 'busy'; children: ReactNode }) {
  return <span className={cx(s.chip, tone && s[tone])}>{children}</span>;
}

export function Field({
  id,
  label,
  hideLabel,
  ...input
}: {
  id: string;
  label: string;
  hideLabel?: boolean;
} & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className={s.field}>
      <label htmlFor={id} className={hideLabel ? s.srOnly : s.label}>
        {label}
      </label>
      <input id={id} {...input} className={cx(s.input, input.className)} />
    </div>
  );
}

/** one radio-like card in a group of choices */
export function Option({
  title,
  desc,
  checked,
  onSelect,
}: {
  title: string;
  desc: string;
  checked: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      className={s.option}
      onClick={onSelect}
    >
      <span className={s.optionHead}>
        <span className={s.optionTitle}>{title}</span>
        <span className={s.radio} aria-hidden="true" />
      </span>
      <span className={s.optionDesc}>{desc}</span>
    </button>
  );
}

function Rail({ step }: { step: StepId }) {
  const draft = useOnboarding((st) => st.draft);
  const go = useOnboarding((st) => st.go);
  const close = useOnboarding((st) => st.close);
  const preview = useOnboarding((st) => st.preview);
  const steps = visibleSteps(draft.purposes);
  const current = steps.indexOf(step);
  const left = Math.max(1, Math.round(((steps.length - 1 - current) * 40) / 60));

  return (
    <nav className={s.rail} aria-label="Setup steps">
      <div className={s.brand}>
        <img src={LOGO_SRC} alt="" />
        SoulSync setup
      </div>
      <ol className={s.steps}>
        {steps.map((id, i) => {
          const done = i < current;
          const inner = (
            <>
              <span className={s.badge}>{done ? <CheckIcon /> : i + 1}</span>
              <span className={s.stepName}>{STEP_LABELS[id]}</span>
            </>
          );
          return (
            <li key={id}>
              {done ? (
                <button type="button" className={cx(s.stepItem, s.done)} onClick={() => go(id)}>
                  {inner}
                </button>
              ) : (
                <span className={s.stepItem} aria-current={i === current ? 'step' : undefined}>
                  {inner}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <div className={s.railFoot}>
        <span>
          {step === 'ready' ? 'All done' : `About ${left} minute${left === 1 ? '' : 's'} left`}
        </span>
        <button
          type="button"
          className={s.quiet}
          style={{ padding: 0, textAlign: 'left' }}
          onClick={close}
        >
          {preview ? 'Close preview' : 'Finish later'}
        </button>
      </div>
    </nav>
  );
}

/**
 * A setup screen. `onContinue` replaces the default "go to the next step"
 * for screens that need to do something first; `skip` adds a quiet
 * "Do this later" that moves on without the step's gate.
 */
export function StepFrame({
  step,
  title,
  lead,
  children,
  wide,
  skip,
  continueLabel = 'Continue',
  onContinue,
  busy,
}: {
  step: StepId;
  title: string;
  lead?: ReactNode;
  children: ReactNode;
  wide?: boolean;
  skip?: boolean;
  continueLabel?: string;
  onContinue?: () => void;
  busy?: boolean;
}) {
  const draft = useOnboarding((st) => st.draft);
  const next = useOnboarding((st) => st.next);
  const back = useOnboarding((st) => st.back);
  const steps = visibleSteps(draft.purposes);
  const gate = canContinue(step, draft);
  const { prev } = neighbours(step, draft);

  return (
    <>
      <Rail step={step} />
      <main className={s.main}>
        <div className={s.scroll}>
          <div className={cx(s.content, wide && s.wide)} key={step}>
            <header className={s.header}>
              <span className={s.eyebrow}>
                Step {steps.indexOf(step) + 1} of {steps.length}
              </span>
              <h1 className={s.title}>{title}</h1>
              {lead ? <p className={s.lead}>{lead}</p> : null}
            </header>
            {children}
          </div>
        </div>
        <footer className={s.footer}>
          {prev ? (
            <button type="button" className={s.quiet} onClick={back}>
              Back
            </button>
          ) : (
            <span />
          )}
          <div className={s.footerRight}>
            {!gate.ok && gate.reason ? <span className={s.gate}>{gate.reason}</span> : null}
            {skip ? (
              <button type="button" className={s.quiet} onClick={next}>
                Do this later
              </button>
            ) : null}
            <button
              type="button"
              className={s.primary}
              disabled={!gate.ok || busy}
              onClick={onContinue ?? next}
            >
              {busy ? <Spinner /> : null}
              {continueLabel}
            </button>
          </div>
        </footer>
      </main>
    </>
  );
}

export { s };
