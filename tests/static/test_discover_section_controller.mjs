// Tests for `createDiscoverSectionController` in
// `webui/static/discover-section-controller.js`. Run via:
//
//     node --test tests/static/
//
// Or through the Python wrapper at
// tests/test_discover_section_controller_js.py which shells out to
// `node --test` and surfaces the result inside the regular pytest run.
//
// The controller is loaded into a sandboxed `vm` context with stubbed
// `window` / `document` / `Element` / `fetch`. No DOM or network — just
// the lifecycle contract.

import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONTROLLER_PATH = resolve(__dirname, '..', '..', 'webui', 'static', 'discover-section-controller.js');

// Minimal Element stub — controller uses `instanceof Element` to tell
// strings (selectors) apart from DOM refs.
class Element {
    constructor(id) {
        this.id = id;
        this.innerHTML = '';
        this.style = { display: '' };
    }
}

// Build a fresh sandbox per test so state doesn't leak between cases.
function makeSandbox(opts = {}) {
    const elements = new Map();
    const ensureEl = (sel) => {
        if (!elements.has(sel)) elements.set(sel, new Element(sel));
        return elements.get(sel);
    };

    const sandbox = {
        Element,
        window: {},
        console: {
            // Quiet by default — turn on by passing { logCalls: true }
            debug: opts.logCalls ? console.debug : () => {},
            error: opts.logCalls ? console.error : () => {},
            log: opts.logCalls ? console.log : () => {},
        },
        document: {
            querySelector: (sel) => ensureEl(sel),
        },
        fetch: opts.fetch || (async () => {
            throw new Error('fetch not stubbed for this test');
        }),
        // Toast spy — when controller calls window.showToast, capture it
        _toasts: [],
    };
    sandbox.window.showToast = (msg, type) => sandbox._toasts.push({ msg, type });
    sandbox._elements = elements;
    return sandbox;
}

let CONTROLLER_SOURCE;
before(() => {
    CONTROLLER_SOURCE = readFileSync(CONTROLLER_PATH, 'utf8');
});

function loadController(sandbox) {
    vm.createContext(sandbox);
    vm.runInContext(CONTROLLER_SOURCE, sandbox);
    return sandbox.window.createDiscoverSectionController;
}

// =========================================================================
// Config validation
// =========================================================================

describe('config validation', () => {
    test('throws on missing id', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.throws(() => create({
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: () => [],
            renderItems: () => '',
        }), /config.id required/);
    });

    test('throws on missing contentEl', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.throws(() => create({
            id: 'x',
            fetchUrl: '/u',
            extractItems: () => [],
            renderItems: () => '',
        }), /contentEl required/);
    });

    test('throws when both fetchUrl and data provided', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.throws(() => create({
            id: 'x',
            contentEl: '#x',
            fetchUrl: '/u',
            data: { ok: true },
            extractItems: () => [],
            renderItems: () => '',
        }), /mutually exclusive/);
    });

    test('throws when neither fetchUrl nor data provided', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.throws(() => create({
            id: 'x',
            contentEl: '#x',
            extractItems: () => [],
            renderItems: () => '',
        }), /either config.fetchUrl or config.data required/);
    });

    test('throws when extractItems missing', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.throws(() => create({
            id: 'x',
            contentEl: '#x',
            fetchUrl: '/u',
            renderItems: () => '',
        }), /extractItems required/);
    });

    test('throws when renderItems missing', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.throws(() => create({
            id: 'x',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: () => [],
        }), /renderItems required/);
    });

    test('accepts function fetchUrl', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.doesNotThrow(() => create({
            id: 'x',
            contentEl: '#x',
            fetchUrl: () => '/u',
            extractItems: () => [],
            renderItems: () => '',
        }));
    });

    test('accepts data instead of fetchUrl', () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        assert.doesNotThrow(() => create({
            id: 'x',
            contentEl: '#x',
            data: { ok: true },
            extractItems: () => [],
            renderItems: () => '',
        }));
    });
});

// =========================================================================
// Happy path — fetch → render
// =========================================================================

describe('fetch + render lifecycle', () => {
    test('fetches, parses, calls renderItems, writes innerHTML', async () => {
        const sandbox = makeSandbox({
            fetch: async (url) => {
                assert.equal(url, '/api/test');
                return {
                    ok: true,
                    json: async () => ({ success: true, items: [{ id: 1 }, { id: 2 }] }),
                };
            },
        });
        const create = loadController(sandbox);
        let renderCalls = 0;
        const ctrl = create({
            id: 'test',
            contentEl: '#carousel',
            fetchUrl: '/api/test',
            extractItems: (data) => data.items,
            renderItems: (items) => {
                renderCalls++;
                return `<x>${items.length}</x>`;
            },
        });
        await ctrl.load();
        assert.equal(renderCalls, 1);
        assert.equal(sandbox._elements.get('#carousel').innerHTML, '<x>2</x>');
        assert.equal(ctrl.getState().phase, 'rendered');
    });

    test('fires onRendered hook after render', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({
                ok: true,
                json: async () => ({ success: true, items: [{ id: 1 }] }),
            }),
        });
        const create = loadController(sandbox);
        let hookCalls = 0;
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => 'rendered',
            onRendered: (ctx) => {
                hookCalls++;
                assert.ok(ctx.contentEl);
                assert.ok(ctx.items);
                assert.ok(ctx.data);
            },
        });
        await ctrl.load();
        assert.equal(hookCalls, 1);
    });

    test('fires onSuccess hook after success gate, before render', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({
                ok: true,
                json: async () => ({ success: true, items: [], stats: { count: 5 } }),
            }),
        });
        const create = loadController(sandbox);
        const order = [];
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => { order.push('render'); return ''; },
            onSuccess: (data) => { order.push(`success:${data.stats.count}`); },
        });
        await ctrl.load();
        // Empty items → no render. onSuccess still fires.
        assert.deepEqual(order, ['success:5']);
    });

    test('fires beforeLoad hook before spinner shows', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({
                ok: true,
                json: async () => ({ success: true, items: [{ id: 1 }] }),
            }),
        });
        const create = loadController(sandbox);
        const order = [];
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => { order.push('render'); return 'r'; },
            beforeLoad: () => { order.push('before'); },
        });
        await ctrl.load();
        assert.equal(order[0], 'before');
        assert.equal(order[1], 'render');
    });
});

// =========================================================================
// Empty state
// =========================================================================

describe('empty state', () => {
    test('renders empty message when items array is empty', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '<should-not-appear/>',
            emptyMessage: 'Nothing here',
        });
        await ctrl.load();
        const html = sandbox._elements.get('#x').innerHTML;
        assert.match(html, /Nothing here/);
        assert.doesNotMatch(html, /should-not-appear/);
        assert.equal(ctrl.getState().phase, 'empty');
    });

    test('hides whole section when hideWhenEmpty + empty', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            sectionEl: '#wrapper',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '',
            hideWhenEmpty: true,
        });
        await ctrl.load();
        assert.equal(sandbox._elements.get('#wrapper').style.display, 'none');
    });

    test('treats success=false as empty (default)', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: false, items: [{ id: 1 }] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '<should-not-appear/>',
            emptyMessage: 'X',
        });
        await ctrl.load();
        assert.equal(ctrl.getState().phase, 'empty');
    });

    test('custom isSuccess overrides default success-flag check', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ status: 'ok', items: [{ id: 1 }] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            isSuccess: (d) => d.status === 'ok',
            renderItems: (items) => `r:${items.length}`,
        });
        await ctrl.load();
        assert.equal(sandbox._elements.get('#x').innerHTML, 'r:1');
    });
});

// =========================================================================
// Stale state
// =========================================================================

describe('stale state', () => {
    test('renders stale UI + fires onStale when isStale returns true', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [], stale: true }) }),
        });
        const create = loadController(sandbox);
        let staleHookCalled = false;
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            isStale: (items, data) => data.stale && items.length === 0,
            renderItems: () => '<should-not-appear/>',
            staleMessage: 'Updating from upstream',
            onStale: () => { staleHookCalled = true; },
        });
        await ctrl.load();
        assert.equal(ctrl.getState().phase, 'stale');
        assert.equal(staleHookCalled, true);
        assert.match(sandbox._elements.get('#x').innerHTML, /Updating from upstream/);
    });

    test('stale wins over empty when both apply', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [], stale: true }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            isStale: () => true,
            renderItems: () => '',
            emptyMessage: 'EMPTY',
            staleMessage: 'STALE',
        });
        await ctrl.load();
        const html = sandbox._elements.get('#x').innerHTML;
        assert.match(html, /STALE/);
        assert.doesNotMatch(html, /EMPTY/);
    });

    test('custom renderStale overrides default stale UI', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [], stale: true }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            isStale: () => true,
            renderItems: () => '',
            renderStale: () => '<custom-stale/>',
        });
        await ctrl.load();
        assert.equal(sandbox._elements.get('#x').innerHTML, '<custom-stale/>');
    });
});

// =========================================================================
// Error state
// =========================================================================

describe('error state', () => {
    test('renders error block on HTTP non-ok', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: false, status: 500, json: async () => ({}) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '',
            errorMessage: 'load failed',
        });
        await ctrl.load();
        assert.equal(ctrl.getState().phase, 'error');
        assert.match(sandbox._elements.get('#x').innerHTML, /load failed/);
    });

    test('renders error block when fetch throws', async () => {
        const sandbox = makeSandbox({
            fetch: async () => { throw new Error('network down'); },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '',
            errorMessage: 'oops',
        });
        await ctrl.load();
        assert.equal(ctrl.getState().phase, 'error');
        assert.match(sandbox._elements.get('#x').innerHTML, /oops/);
    });

    test('fires showToast on error when showErrorToast: true', async () => {
        const sandbox = makeSandbox({
            fetch: async () => { throw new Error('boom'); },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '',
            errorMessage: 'load broke',
            showErrorToast: true,
        });
        await ctrl.load();
        assert.equal(sandbox._toasts.length, 1);
        assert.equal(sandbox._toasts[0].msg, 'load broke');
        assert.equal(sandbox._toasts[0].type, 'error');
    });

    test('does NOT fire toast when showErrorToast omitted', async () => {
        const sandbox = makeSandbox({
            fetch: async () => { throw new Error('boom'); },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => '',
        });
        await ctrl.load();
        assert.equal(sandbox._toasts.length, 0);
    });
});

// =========================================================================
// No-fetch data: mode
// =========================================================================

describe('no-fetch data mode', () => {
    test('renders provided data without calling fetch', async () => {
        let fetchCalled = false;
        const sandbox = makeSandbox({
            fetch: async () => { fetchCalled = true; throw new Error('should not fetch'); },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            data: { success: true, items: [{ id: 1 }, { id: 2 }] },
            extractItems: (d) => d.items,
            renderItems: (items) => `n:${items.length}`,
        });
        await ctrl.load();
        assert.equal(fetchCalled, false);
        assert.equal(sandbox._elements.get('#x').innerHTML, 'n:2');
    });

    test('accepts data as a function', async () => {
        const sandbox = makeSandbox();
        const create = loadController(sandbox);
        let dataCalls = 0;
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            data: () => { dataCalls++; return { success: true, items: [{ id: 9 }] }; },
            extractItems: (d) => d.items,
            renderItems: (items) => `f:${items[0].id}`,
        });
        await ctrl.load();
        assert.equal(dataCalls, 1);
        assert.equal(sandbox._elements.get('#x').innerHTML, 'f:9');
    });
});

// =========================================================================
// manualDom mode
// =========================================================================

describe('manualDom mode', () => {
    test('does NOT write renderItems return into contentEl', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            manualDom: true,
            extractItems: (d) => d.items,
            renderItems: () => '<should-not-appear/>',
        });
        await ctrl.load();
        const html = sandbox._elements.get('#x').innerHTML;
        // Spinner from _showLoading was the last write; manualDom mode
        // didn't replace it. The renderer gets called for side-effects
        // (which the test doesn't exercise here) but innerHTML stays
        // whatever the loading spinner left.
        assert.doesNotMatch(html, /should-not-appear/);
        assert.equal(ctrl.getState().phase, 'rendered');
    });

    test('still fires renderItems for side-effects', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) }),
        });
        const create = loadController(sandbox);
        let renderCalled = false;
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            manualDom: true,
            extractItems: (d) => d.items,
            renderItems: () => { renderCalled = true; },
        });
        await ctrl.load();
        assert.equal(renderCalled, true);
    });
});

// =========================================================================
// Fetch URL forms
// =========================================================================

describe('fetchUrl forms', () => {
    test('callable fetchUrl is invoked at load time', async () => {
        let urlCalls = 0;
        const sandbox = makeSandbox({
            fetch: async (url) => {
                assert.equal(url, '/u/computed');
                return { ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) };
            },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: () => { urlCalls++; return '/u/computed'; },
            extractItems: (d) => d.items,
            renderItems: () => 'r',
        });
        await ctrl.load();
        assert.equal(urlCalls, 1);
        // Calling refresh re-resolves the URL — important for sections
        // whose URL depends on runtime state (e.g. season key).
        await ctrl.refresh();
        assert.equal(urlCalls, 2);
    });
});

// =========================================================================
// Coalescing + refresh
// =========================================================================

describe('load coalescing and refresh', () => {
    test('two concurrent load() calls share one fetch', async () => {
        let fetchCalls = 0;
        const sandbox = makeSandbox({
            fetch: async () => {
                fetchCalls++;
                // Yield once so both load() calls land on the same in-flight promise.
                await new Promise((r) => setImmediate(r));
                return { ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) };
            },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => 'r',
        });
        await Promise.all([ctrl.load(), ctrl.load(), ctrl.load()]);
        assert.equal(fetchCalls, 1);
    });

    test('refresh() bypasses the coalesce and re-fetches', async () => {
        let fetchCalls = 0;
        const sandbox = makeSandbox({
            fetch: async () => {
                fetchCalls++;
                return { ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) };
            },
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => 'r',
        });
        await ctrl.load();
        await ctrl.refresh();
        await ctrl.refresh();
        assert.equal(fetchCalls, 3);
    });
});

// =========================================================================
// Hook error containment
// =========================================================================

describe('hook error containment', () => {
    test('throwing renderer hook does not crash the controller', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => 'r',
            onRendered: () => { throw new Error('hook boom'); },
        });
        // Test passes if this doesn't throw out of the await.
        await ctrl.load();
        assert.equal(ctrl.getState().phase, 'rendered');
    });

    test('throwing onSuccess hook does not block the render', async () => {
        const sandbox = makeSandbox({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [{ id: 1 }] }) }),
        });
        const create = loadController(sandbox);
        const ctrl = create({
            id: 'test',
            contentEl: '#x',
            fetchUrl: '/u',
            extractItems: (d) => d.items,
            renderItems: () => 'rendered-anyway',
            onSuccess: () => { throw new Error('boom'); },
        });
        await ctrl.load();
        assert.equal(sandbox._elements.get('#x').innerHTML, 'rendered-anyway');
    });
});
