// a stub 2d context for tests: every method is a no-op that records its
// call, and any NaN / Infinity handed to a drawing call is collected.

export interface StubCtx {
  ctx: CanvasRenderingContext2D;
  calls: { name: string; args: unknown[] }[];
  bad: string[];
}

function badValue(v: unknown): boolean {
  if (typeof v === 'number') return !Number.isFinite(v);
  if (typeof v === 'string') return /NaN|Infinity|undefined/.test(v);
  return false;
}

export function stubCtx(): StubCtx {
  const calls: { name: string; args: unknown[] }[] = [];
  const bad: string[] = [];
  const check = (name: string, args: unknown[]) => {
    args.forEach((a, i) => {
      if (badValue(a)) bad.push(`${name}[${i}]=${String(a)}`);
    });
  };
  const gradient = {
    addColorStop: (...args: unknown[]) => check('addColorStop', args),
  };
  const props: Record<string, unknown> = {};
  const target = {};
  const ctx = new Proxy(target, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined;
      if (p in props) return props[p];
      if (p === 'createLinearGradient' || p === 'createRadialGradient') {
        return (...args: unknown[]) => {
          check(p, args);
          calls.push({ name: p, args });
          return gradient;
        };
      }
      return (...args: unknown[]) => {
        check(p, args);
        calls.push({ name: p, args });
      };
    },
    set(_t, p, v) {
      if (typeof p === 'string') {
        if (badValue(v)) bad.push(`${p}=${String(v)}`);
        props[p] = v;
      }
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, calls, bad };
}
