import { afterEach, beforeEach, expect } from 'vitest';

const unexpected: string[] = [];

/** Record before throwing: production request handlers may catch the rejection. */
export function unexpectedRequest(input: unknown): never {
  const message = `Unexpected request: ${String(input)}`;
  unexpected.push(message);
  throw new Error(message);
}

beforeEach(() => unexpected.splice(0));
afterEach(() => {
  expect(unexpected, 'All review API calls must have explicit test responses').toEqual([]);
});
