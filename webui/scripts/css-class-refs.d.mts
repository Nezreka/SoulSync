// Types for css-class-refs.mjs, so its vitest suite type-checks.

export interface Refs {
  tokens: Set<string>;
  prefixes: string[];
  suffixes: string[];
  contains: string[];
  starts: string[];
  ends: string[];
}

export interface RuleVerdict {
  selector: string;
  start: number;
  end: number;
  dead: boolean;
  partlyDead: boolean;
  deadClasses: string[];
}

export interface Sheet {
  name: string;
  text: string;
}

export function sourceFiles(): string[];
export function refsFromTexts(sources: Array<string | { path?: string; text: string }>): Refs;
export function collectRefs(files?: string[]): Refs;
export function isLive(cls: string, refs: Refs): boolean;
export function selectorClasses(selector: string): string[];
export function analyseRules(cssText: string, refs: Refs): RuleVerdict[];
export function legacyStylesheets(): string[];
export function removedSelectors(
  beforeSheets: Sheet[],
  afterSheets: Sheet[],
  refs: Refs,
): { gone: string[]; live: string[] };
