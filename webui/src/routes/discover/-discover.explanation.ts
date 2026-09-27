/**
 * Why a recommendation is here, as one line.
 *
 * The server writes one shape wherever it makes a recommendation
 * (core/discovery/explain.py): `{ kind, seeds: [{ name, id, source }],
 * confidence }` under `explanation`. The screen only words it. It used to
 * rebuild the reason itself, differently per shelf, from whatever list of
 * names that shelf happened to carry.
 */

export type ExplanationKind = 'similar_to' | 'listened' | 'genre' | 'new_release' | 'trending';

export interface ExplanationSeed {
  name: string;
  id?: string | null;
  source?: string | null;
}

export interface Explanation {
  /** An ExplanationKind; typed wide because it arrives as JSON, and an unknown
   * kind renders nothing rather than guessing. */
  kind: string;
  seeds?: ExplanationSeed[];
  confidence?: number | null;
  /** Per-seed shares of the score (name → 0..1), when the server sends them. */
  components?: Record<string, number>;
  /** Where the tracks came from (source → share), when the server sends it. */
  source_mix?: Record<string, number>;
}

const LEAD: Record<ExplanationKind, string> = {
  similar_to: 'Because you have',
  listened: 'Because you listen to',
  genre: 'Because you like',
  new_release: 'New from',
  trending: 'Trending from',
};

/** When no seed could be named, say only what the kind truthfully is. */
const NO_SEEDS: Record<ExplanationKind, string> = {
  similar_to: 'Similar to your library',
  listened: 'From artists you play often',
  genre: 'From your genres',
  new_release: 'A new release',
  trending: 'Trending now',
};

const TITLE_LEAD: Record<ExplanationKind, string> = {
  similar_to: 'In your library',
  listened: 'You listen to',
  genre: 'Your genres',
  new_release: 'New from',
  trending: 'Trending from',
};

function known(kind: string): kind is ExplanationKind {
  return Object.prototype.hasOwnProperty.call(LEAD, kind);
}

function seedNames(explanation: Explanation): string[] {
  return (explanation.seeds ?? []).map((s) => s?.name ?? '').filter((n) => n !== '');
}

/** `A`, `A & B`, `A, B +2 more` - the visible line stays short. */
export function nameList(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  if (names.length === 2) return `${names[0]} & ${names[1]}`;
  return `${names.slice(0, 2).join(', ')} +${names.length - 2} more`;
}

/** The one line. Empty when there is nothing to explain. */
export function explanationLine(explanation: Explanation | null | undefined): string {
  if (!explanation || !known(explanation.kind)) return '';
  const names = seedNames(explanation);
  if (names.length === 0) return NO_SEEDS[explanation.kind];
  return `${LEAD[explanation.kind]} ${nameList(names)}`;
}

/** Every seed, for the tooltip the truncated line hides — with each seed's
 * share of the score when the server sent components. */
export function explanationTitle(explanation: Explanation | null | undefined): string {
  if (!explanation || !known(explanation.kind)) return '';
  const names = seedNames(explanation);
  if (!names.length) return '';
  const shares = explanation.components;
  const shown = names.map((n) => {
    const share = shares?.[n];
    return typeof share === 'number' && Number.isFinite(share)
      ? `${n} (${Math.round(share * 100)}%)`
      : n;
  });
  return `${TITLE_LEAD[explanation.kind]}: ${shown.join(', ')}`;
}

const SOURCE_LABEL: Record<string, string> = {
  library: 'your library',
  discovery: 'discovery',
  trending: "what's trending",
  direct: 'similar artists',
  genre: 'the genre',
  owned: 'artists you own',
};

/** Where most of it came from, when the server says: 'More from your library'. */
export function sourceMixLine(explanation: Explanation | null | undefined): string {
  const mix = explanation?.source_mix;
  if (!mix) return '';
  let top = '';
  let share = 0;
  for (const [source, value] of Object.entries(mix)) {
    if (typeof value === 'number' && Number.isFinite(value) && value > share) {
      top = source;
      share = value;
    }
  }
  if (!top) return '';
  const label = SOURCE_LABEL[top] ?? top;
  return share >= 0.5 ? `More from ${label}` : `Some from ${label}`;
}
