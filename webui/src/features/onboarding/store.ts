/**
 * One store for the open setup flow. The draft survives a closed tab (minus
 * every secret) so "Finish later" picks up where it stopped. The preview
 * never reads or writes that copy.
 */

import { create } from 'zustand';

import type { ReadyRow } from './apply';
import type { Draft, Environment, OpenOptions, StepId } from './types';

import { emptyDraft, visibleSteps } from './plan';

const DRAFT_KEY = 'soulsync:onboarding-draft';

type Pretend = NonNullable<OpenOptions['pretend']>;

export interface OnboardingState {
  open: boolean;
  preview: boolean;
  pretend: Pretend;
  step: StepId;
  env: Environment | null;
  /** service -> url, from the discovery the welcome screen kicks off */
  found: Record<string, string>;
  discovering: boolean;
  draft: Draft;
  rows: ReadyRow[];
  applying: 'idle' | 'running' | 'done';
  /** bumps on every open so screens reset their local state */
  session: number;
  begin: (options: OpenOptions) => void;
  close: () => void;
  go: (step: StepId) => void;
  next: () => void;
  back: () => void;
  update: (fn: (d: Draft) => void) => void;
  setEnv: (env: Environment) => void;
  setFound: (found: Record<string, string>, discovering: boolean) => void;
  setRows: (rows: ReadyRow[], applying: OnboardingState['applying']) => void;
}

/** a copy with every secret blanked, the only thing that touches storage */
export function stripSecrets(d: Draft): Draft {
  return {
    ...d,
    account: { ...d.account, pin: '', password: '' },
    server: { ...d.server, token: '', apiKey: '', password: '', connected: false },
    slskd: { ...d.slskd, apiKey: '', connected: false },
    deezerArl: '',
    spotify: { clientId: d.spotify.clientId, clientSecret: '' },
    listenbrainz: { token: '', user: '' },
    lastfm: { ...d.lastfm, apiKey: '' },
    video: {
      tmdbKey: '',
      prowlarr: { ...d.video.prowlarr, apiKey: '' },
      client: { ...d.video.client, password: '', apiKey: '' },
    },
  };
}

function loadSaved(): { draft: Draft; step: StepId } | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as { draft?: Draft; step?: StepId };
    if (!saved?.draft) return null;
    // a draft from an older build may miss fields, lay it over a fresh one
    const base = emptyDraft();
    const draft = { ...base, ...saved.draft } as Draft;
    for (const key of Object.keys(base) as (keyof Draft)[]) {
      const b = base[key];
      if (b && typeof b === 'object' && !Array.isArray(b))
        (draft as unknown as Record<string, unknown>)[key] = {
          ...(b as object),
          ...(saved.draft as unknown as Record<string, object | undefined>)[key],
        };
    }
    return { draft, step: saved.step ?? 'welcome' };
  } catch {
    return null;
  }
}

function persist(state: Pick<OnboardingState, 'preview' | 'draft' | 'step'>) {
  if (state.preview) return;
  try {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({ draft: stripSecrets(state.draft), step: state.step }),
    );
  } catch {
    // private window, the flow still works without the copy
  }
}

export function forgetSavedDraft() {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // nothing saved to forget
  }
}

/** the steps around this one, skipping those the purposes rule out */
export function neighbours(step: StepId, draft: Draft): { prev?: StepId; next?: StepId } {
  const steps: StepId[] = ['welcome', ...visibleSteps(draft.purposes)];
  const i = steps.indexOf(step);
  return { prev: i > 0 ? steps[i - 1] : undefined, next: i >= 0 ? steps[i + 1] : undefined };
}

export const useOnboarding = create<OnboardingState>((set, get) => ({
  open: false,
  preview: false,
  pretend: 'real',
  step: 'welcome',
  env: null,
  found: {},
  discovering: false,
  draft: emptyDraft(),
  rows: [],
  applying: 'idle',
  session: 0,
  begin: (options) => {
    const preview = !!options.preview;
    const saved = preview ? null : loadSaved();
    const draft = saved?.draft ?? emptyDraft();
    // the preview can start on movies and tv, which only shows when it's picked
    if (options.startAt === 'video' && !draft.purposes.includes('video'))
      draft.purposes.push('video');
    set({
      open: true,
      preview,
      pretend: options.pretend ?? 'real',
      step: options.startAt ?? saved?.step ?? 'welcome',
      draft,
      env: null,
      found: {},
      discovering: false,
      rows: [],
      applying: 'idle',
      session: get().session + 1,
    });
  },
  close: () => {
    set({ open: false });
  },
  go: (step) => {
    set({ step });
    persist(get());
  },
  next: () => {
    const { next } = neighbours(get().step, get().draft);
    if (next) get().go(next);
  },
  back: () => {
    const { prev } = neighbours(get().step, get().draft);
    if (prev) get().go(prev);
  },
  update: (fn) => {
    const draft = structuredClone(get().draft);
    fn(draft);
    set({ draft });
    persist(get());
  },
  setEnv: (env) => {
    set({ env });
  },
  setFound: (found, discovering) => {
    set({ found, discovering });
  },
  setRows: (rows, applying) => {
    set({ rows, applying });
  },
}));
