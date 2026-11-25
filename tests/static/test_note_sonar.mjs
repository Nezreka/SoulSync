// Song identification controller (`webui/static/note-sonar.js`).
//
//     node --test tests/static/test_note_sonar.mjs
//
// The browser cannot be asked to hold a permission prompt open, cancel a
// MediaRecorder and deliver a queued stop event later, so the controller is
// loaded into a VM with a small DOM/media shim. The shim runs the REAL file;
// only the browser edges are fake. The regressions pinned here:
//
//   * cancel while permission is pending: the late stream is stopped, no
//     recorder is built and nothing is uploaded;
//   * cancel while recording: the late onstop dereferences nothing and does
//     not upload after cancellation;
//   * close during an in-flight identify: the fetch is aborted and a response
//     that still arrives cannot paint the reopened sheet;
//   * a file selected or dropped during a capture takes ownership: the
//     recorder is torn down and exactly one request (the file) is sent;
//   * an upload never reopens the level meter (no analyser is running);
//   * readiness answers before getUserMedia is called, and an unavailable
//     recogniser never asks for the microphone;
//   * the third-party Shazam disclosure is in the preflight sheet, and opening
//     the sheet does not start a capture by itself.

import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'note-sonar.js'), 'utf8');
const STATUS_URL = '/api/listen/status';
const IDENTIFY_URL = '/api/listen/identify';

const flush = () => new Promise((done) => setImmediate(done));
async function settle() {
    for (let i = 0; i < 6; i++) await flush();
}

function makeClassList() {
    const values = new Set();
    return {
        add(...names) { names.forEach((name) => values.add(name)); },
        remove(...names) { names.forEach((name) => values.delete(name)); },
        contains(name) { return values.has(name); },
        toggle(name, force) {
            const on = force === undefined ? !values.has(name) : !!force;
            if (on) values.add(name); else values.delete(name);
            return on;
        },
    };
}

function makeElement(tag = 'div') {
    const element = {
        tagName: tag,
        id: '',
        className: '',
        type: '',
        title: '',
        textContent: '',
        value: '',
        files: null,
        style: {},
        children: [],
        attrs: {},
        listeners: {},
        classList: makeClassList(),
        appendChild(child) { this.children.push(child); return child; },
        insertBefore(child, before) {
            const at = this.children.indexOf(before);
            if (at === -1) this.children.push(child);
            else this.children.splice(at, 0, child);
            return child;
        },
        setAttribute(name, value) { this.attrs[name] = value; },
        addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
        click() { (this.listeners.click || []).forEach((fn) => fn({ target: this })); },
        querySelector(selector) {
            if (selector.startsWith('#')) {
                return this.children.find((child) => child.id === selector.slice(1)) || null;
            }
            return null;
        },
    };
    let innerHTML = '';
    Object.defineProperty(element, 'innerHTML', {
        get() { return innerHTML; },
        set(value) {
            innerHTML = value;
            // the real DOM drops children when innerHTML is replaced (the
            // controller clears an actions list by assigning '')
            if (value === '') this.children.length = 0;
        },
    });
    Object.defineProperty(element, 'lastElementChild', {
        get() { return this.children[this.children.length - 1] || null; },
    });
    return element;
}

function sheetElements() {
    const overlay = makeElement('div');
    overlay.classList.add('visible');
    const sheet = makeElement('div');
    const orb = makeElement('div');
    const title = makeElement('h2');
    const status = makeElement('p');
    const meter = makeElement('div');
    for (let i = 0; i < 15; i++) meter.appendChild(makeElement('b'));
    const disclosure = makeElement('p');
    disclosure.textContent = 'Your clip is sent to Shazam';
    const actions = makeElement('div');
    const note = makeElement('div');
    const file = makeElement('input');
    overlay.children.push(sheet);
    sheet.children.push(orb, title, status, meter, disclosure, actions, note, file);
    return { overlay, sheet, orb, title, status, meter, disclosure, actions, note, file };
}

function response(body, { ok = true, status = 200 } = {}) {
    return { ok, status, json: async () => body };
}

function load() {
    const timers = new Map();
    let nextTimer = 1;
    const toasts = [];
    const fetches = [];
    const mediaRecorders = [];

    class FakeMediaRecorder {
        static isTypeSupported() { return true; }
        constructor(stream, options = {}) {
            this.stream = stream;
            this.mimeType = options.mimeType || 'audio/webm';
            this.state = 'inactive';
            this.ondataavailable = null;
            this.onstop = null;
            this.onerror = null;
            this.startCalls = 0;
            this.stopCalls = 0;
            mediaRecorders.push(this);
        }
        start() { this.startCalls += 1; this.state = 'recording'; }
        stop() {
            this.stopCalls += 1;
            this.state = 'inactive';
            if (this.onstop) this.onstop();
        }
    }

    class FakeAudioContext {
        constructor() { this.closed = false; }
        createMediaStreamSource() { return { connect() {} }; }
        createAnalyser() {
            return {
                fftSize: 0,
                frequencyBinCount: 4,
                connect() {},
                getByteTimeDomainData(data) { data.fill(128); },
            };
        }
        close() { this.closed = true; return Promise.resolve(); }
    }

    class FakeFormData {
        constructor() { this.entries = []; }
        append(name, value, filename) { this.entries.push({ name, value, filename }); }
    }

    class FakeBlob {
        constructor(parts, options = {}) {
            this.parts = parts;
            this.type = options.type || '';
            this.size = parts.reduce((total, part) => total + (part.size || part.length || 0), 0);
        }
    }

    const body = makeElement('body');
    const document = {
        readyState: 'loading',
        body,
        createElement: (tag) => makeElement(tag),
        getElementById: () => null,
        addEventListener() {},
    };

    const window = {
        __SOULSYNC_LISTEN_TEST__: true,
        isSecureContext: true,
        MediaRecorder: FakeMediaRecorder,
        AudioContext: FakeAudioContext,
        setTimeout(fn, ms) { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
        clearTimeout(id) { timers.delete(id); },
        setInterval() { return 0; },
        requestAnimationFrame() { return 0; },
        cancelAnimationFrame() {},
        showToast(message) { toasts.push(message); },
    };

    const ctx = {
        window,
        document,
        navigator: { mediaDevices: { getUserMedia: null } },
        MediaRecorder: FakeMediaRecorder,
        Blob: FakeBlob,
        FormData: FakeFormData,
        AbortController,
        console: { log() {}, debug() {}, error() {}, warn() {} },
        fetch: async () => response({ success: true, available: true, reason: '' }),
    };
    vm.createContext(ctx);
    vm.runInContext(SOURCE, ctx, { filename: 'note-sonar.js' });
    const api = window.__soulsyncListenTest;
    assert.ok(api, 'the test hook must exist under __SOULSYNC_LISTEN_TEST__');

    return {
        ctx, api, window, document, body, toasts, fetches, mediaRecorders,
        statusOk: (available = true, reason = '') => response({
            success: true, available, reason,
        }),
        identifyPending() {
            let settleResponse;
            const promise = new Promise((done) => { settleResponse = done; });
            return { promise, resolve: settleResponse };
        },
        track() {
            return { stopped: false, stop() { this.stopped = true; } };
        },
        stream(track) {
            return { getTracks: () => [track] };
        },
    };
}

function mount(harness) {
    Object.assign(harness.api.el, sheetElements());
}

describe('note-sonar controller', () => {
    let h;
    beforeEach(() => {
        h = load();
        mount(h);
    });

    test('cancel while recording: a late onstop uploads nothing and dereferences nothing', async () => {
        h.api.state.open = true;
        h.api.state.op = 4;
        h.api.state.phase = 'listening';
        const track = h.track();
        const recorder = {
            state: 'recording',
            mimeType: 'audio/webm',
            ondataavailable() {},
            onstop() {},
            stopCalls: 0,
            stop() { this.stopCalls += 1; this.state = 'inactive'; },
        };
        h.api.state.recorder = recorder;
        h.api.state.stream = h.stream(track);
        const lateStop = recorder.onstop;

        h.api.close();

        assert.equal(track.stopped, true, 'the microphone track was left open');
        assert.equal(h.api.state.recorder, null);
        assert.equal(h.fetches.length, 0);

        lateStop();
        await settle();
        assert.equal(h.fetches.length, 0, 'a cancelled recording uploaded its clip');
        assert.equal(h.api.state.phase, 'idle');
    });

    test('cancel while the permission prompt is pending stops the late stream', async () => {
        let resolvePermission;
        h.ctx.navigator.mediaDevices.getUserMedia = () => new Promise((done) => {
            resolvePermission = done;
        });
        h.ctx.fetch = async (url) => {
            h.fetches.push(url);
            return h.statusOk();
        };
        h.api.state.open = true;
        h.api.state.phase = 'preflight';

        h.api.startListening();
        await settle();
        assert.equal(h.fetches[0], STATUS_URL);
        assert.equal(typeof resolvePermission, 'function');

        h.api.close();
        const track = h.track();
        resolvePermission(h.stream(track));
        await settle();

        assert.equal(track.stopped, true, 'the cancelled operation kept the microphone');
        assert.equal(h.mediaRecorders.length, 0, 'a recorder was built for a cancelled operation');
        assert.equal(h.fetches.filter((url) => url === IDENTIFY_URL).length, 0);
    });

    test('close during an in-flight identify aborts it and ignores the late answer', async () => {
        const pending = h.identifyPending();
        let init;
        h.ctx.fetch = (url, options) => {
            h.fetches.push(url);
            init = options;
            return pending.promise;
        };
        h.api.state.open = true;
        h.api.state.phase = 'preflight';

        h.api.uploadFile({ name: 'clip.mp3' });
        await settle();
        assert.equal(h.fetches.filter((url) => url === IDENTIFY_URL).length, 1);
        assert.ok(init.signal, 'the identify request was not abortable');

        h.api.close();
        assert.equal(init.signal.aborted, true, 'dismissing did not abort the request');

        pending.resolve(response({
            success: true,
            identification: { title: 'Nightcall', artist: 'Kavinsky' },
            match: null,
            query: 'Kavinsky Nightcall',
        }));
        await settle();

        assert.equal(h.toasts.length, 0, 'a dismissed request still handed the song over');
        assert.equal(h.api.state.phase, 'idle');
    });

    test('a file chosen during a capture takes ownership: one request, no late clip', async () => {
        const track = h.track();
        const recorder = {
            state: 'recording',
            mimeType: 'audio/webm',
            ondataavailable() {},
            onstop() {},
            stopCalls: 0,
            stop() { this.stopCalls += 1; this.state = 'inactive'; },
        };
        h.api.state.open = true;
        h.api.state.op = 9;
        h.api.state.phase = 'listening';
        h.api.state.recorder = recorder;
        h.api.state.stream = h.stream(track);
        const lateStop = recorder.onstop;
        h.ctx.fetch = async (url) => {
            h.fetches.push(url);
            return new Promise(() => {});
        };

        h.api.uploadFile({ name: 'clip.mp3' });
        await settle();

        assert.equal(recorder.stopCalls, 1, 'the recorder was not torn down');
        assert.equal(recorder.state, 'inactive');
        assert.equal(track.stopped, true, 'the microphone was left running behind the upload');
        assert.equal(h.fetches.filter((url) => url === IDENTIFY_URL).length, 1);
        assert.equal(h.api.el.overlay.classList.contains('listening'), false);
        assert.equal(h.api.state.analyser, null);

        lateStop();
        await settle();
        assert.equal(h.fetches.filter((url) => url === IDENTIFY_URL).length, 1,
            'the recorder clip was submitted on top of the file');
    });

    test('an upload does not restart the dead level meter', async () => {
        h.api.state.open = true;
        h.ctx.fetch = () => new Promise(() => {});

        h.api.uploadFile({ name: 'clip.mp3' });
        await settle();

        assert.equal(h.api.el.overlay.classList.contains('listening'), false);
        assert.equal(h.api.el.overlay.classList.contains('working'), true);
        assert.equal(h.api.state.analyser, null);
        assert.equal(h.api.state.audioCtx, null);
    });

    test('opening the sheet checks readiness and waits for an explicit start', async () => {
        let permissionCalls = 0;
        h.ctx.navigator.mediaDevices.getUserMedia = () => {
            permissionCalls += 1;
            return new Promise(() => {});
        };
        h.ctx.fetch = async (url) => {
            h.fetches.push(url);
            return h.statusOk(true);
        };

        h.api.open();
        await settle();

        assert.deepEqual(h.fetches, [STATUS_URL]);
        assert.equal(permissionCalls, 0, 'opening the sheet started the microphone');
        assert.equal(h.api.el.overlay.classList.contains('listening'), false);
        const labels = h.api.el.actions.children.map((button) => button.textContent);
        assert.deepEqual(labels, ['Start listening', 'Upload a clip']);
    });

    test('readiness is checked before the microphone is requested', async () => {
        let permissionCalls = 0;
        h.ctx.navigator.mediaDevices.getUserMedia = () => {
            permissionCalls += 1;
            return new Promise(() => {});
        };
        h.ctx.fetch = async (url) => {
            h.fetches.push(url);
            return h.statusOk(true);
        };
        h.api.state.open = true;
        h.api.state.phase = 'preflight';

        h.api.startListening();
        await settle();

        assert.deepEqual(h.fetches, [STATUS_URL]);
        assert.equal(permissionCalls, 1);
    });

    test('an unavailable recogniser never asks for the microphone', async () => {
        let permissionCalls = 0;
        h.ctx.navigator.mediaDevices.getUserMedia = () => {
            permissionCalls += 1;
            return new Promise(() => {});
        };
        h.ctx.fetch = async (url) => {
            h.fetches.push(url);
            return h.statusOk(false, 'shazamio is not installed');
        };
        h.api.state.open = true;
        h.api.state.phase = 'preflight';

        h.api.startListening();
        await settle();

        assert.deepEqual(h.fetches, [STATUS_URL]);
        assert.equal(permissionCalls, 0);
        assert.equal(h.api.state.phase, 'failed');
        assert.match(h.api.el.status.textContent, /shazamio is not installed/);
    });
});

describe('note-sonar sheet and field integration', () => {
    test('the disclosure and the explicit start button exist; open does not auto-capture', () => {
        assert.match(SOURCE, /Your clip is sent to Shazam/);
        assert.match(SOURCE, /data-listen-disclosure/);
        assert.match(SOURCE, /label: 'Start listening'/);
        // the old build called listen() directly from open(); that would start
        // the microphone before a user could read the disclosure.
        assert.doesNotMatch(SOURCE, /function open\(\)\s*\{[^}]*?\blisten\(\)/);
    });

    test('the file picker and the drop target share one ownership path', () => {
        assert.match(SOURCE, /el\.file\.addEventListener\('change'[\s\S]*?uploadFile\(file\)/);
        assert.match(SOURCE, /addEventListener\('drop'[\s\S]*?uploadFile\(file\)/);
    });

    test('the mic button is inserted beside the source picker, not over the clear button', () => {
        const h = load();
        const bar = makeElement('div');
        const input = makeElement('input');
        const clear = makeElement('button');
        clear.id = 'enhanced-cancel-btn';
        const picker = makeElement('button');
        picker.id = 'source-picker';
        bar.appendChild(input);
        bar.appendChild(clear);
        bar.appendChild(picker);
        h.document.getElementById = (id) => (id === 'enhanced-search-bar' ? bar : null);

        h.api.ensureMic();

        const ids = bar.children.map((child) => child.id);
        assert.deepEqual(ids, ['', 'enhanced-cancel-btn', 'listen-mic-btn', 'source-picker']);
    });
});
