import type { WebGraph } from '@/routes/discover/-discover.artist-web';

/**
 * A minimal in-memory graphology stand-in implementing the WebGraph surface
 * the port consumes. Extracted from the artist-web differential test so the
 * orchestrator tests build REAL lens graphs without the CDN bundle.
 */
type Attrs = Record<string, unknown>;

export class FakeGraph implements WebGraph {
  nodes = new Map<string, Attrs>();
  edges = new Map<string, { source: string; target: string; attrs: Attrs }>();
  undirected: boolean;

  constructor(opts?: { type?: string }) {
    this.undirected = opts?.type === 'undirected';
  }

  get order() {
    return this.nodes.size;
  }
  get size() {
    return this.edges.size;
  }
  private key(s: string, t: string) {
    return this.undirected && s > t ? `${t}|${s}` : `${s}|${t}`;
  }
  addNode(key: string, attrs: Attrs) {
    if (this.nodes.has(key)) throw new Error(`duplicate node ${key}`);
    this.nodes.set(key, { ...attrs });
  }
  addEdge(source: string, target: string, attrs: Attrs) {
    this.edges.set(this.key(source, target), { source, target, attrs: { ...attrs } });
  }
  hasNode(key: string) {
    return this.nodes.has(key);
  }
  hasEdge(source: string, target: string) {
    return this.edges.has(this.key(source, target)) || this.edges.has(this.key(target, source));
  }
  degree(key: string) {
    let d = 0;
    this.edges.forEach((e) => {
      if (e.source === key) d++;
      if (e.target === key) d++;
    });
    return d;
  }
  source(edge: string) {
    return this.edges.get(edge)!.source;
  }
  target(edge: string) {
    return this.edges.get(edge)!.target;
  }
  getNodeAttribute(key: string, name: string) {
    return this.nodes.get(key)?.[name];
  }
  getNodeAttributes(key: string) {
    return this.nodes.get(key) as Attrs;
  }
  setNodeAttribute(key: string, name: string, value: unknown) {
    (this.nodes.get(key) as Attrs)[name] = value;
  }
  mergeNodeAttributes(key: string, attrs: Attrs) {
    Object.assign(this.nodes.get(key) as Attrs, attrs);
  }
  mergeEdgeAttributes(edge: string, attrs: Attrs) {
    Object.assign(this.edges.get(edge)!.attrs, attrs);
  }
  forEachNode(cb: (key: string, attrs: Attrs) => void) {
    [...this.nodes.entries()].forEach(([k, a]) => cb(k, a));
  }
  forEachEdge(
    a: string | ((e: string, attrs: Attrs, s: string) => void),
    b?: (e: string, attrs: Attrs, s: string) => void,
  ) {
    const node = typeof a === 'string' ? a : null;
    const cb = (typeof a === 'string' ? b : a) as (e: string, attrs: Attrs, s: string) => void;
    [...this.edges.entries()].forEach(([k, e]) => {
      if (node && e.source !== node && e.target !== node) return;
      cb(k, e.attrs, e.source);
    });
  }
  forEachNeighbor(key: string, cb: (nb: string, attrs: Attrs) => void) {
    const seen = new Set<string>();
    this.edges.forEach((e) => {
      const other = e.source === key ? e.target : e.target === key ? e.source : null;
      if (other && !seen.has(other)) {
        seen.add(other);
        cb(other, this.nodes.get(other) as Attrs);
      }
    });
  }
}
