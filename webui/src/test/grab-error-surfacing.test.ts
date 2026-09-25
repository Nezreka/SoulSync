import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * A refused grab has to say WHY.
 *
 * Boulder clicked Get and saw "grab failed". The server had actually answered
 * 507 with "Only N GB free on the temporary/working drive (...) - under your
 * X GB minimum" - a complete, actionable explanation. postJSON threw it away:
 *
 *     .then(function (r) { return r.ok ? r.json() : null; })
 *
 * so `res` was null and the caller fell through to its generic fallback. The
 * grab endpoint has FIVE distinct refusals - unsupported source, missing slskd
 * info, missing download URL, no disk room, no library folder - and every one
 * of them arrived as the same four words.
 *
 * The callers were never the problem; they already read res.error. They were
 * just never given one.
 */

const VIEW = readFileSync(
  resolve(process.cwd(), 'static/video/video-download-view.js'),
  'utf8',
);

const POST = VIEW.slice(VIEW.indexOf('function postJSON'), VIEW.indexOf('function contentHTML'));
/** The same slice with comment lines dropped. The fix's own comment quotes the
 *  old broken expression, so a bare "must not contain" matches the explanation
 *  rather than any live code. */
const POST_CODE = POST.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

describe('postJSON on a failure', () => {
  it('no longer discards the body on a non-2xx', () => {
    expect(POST_CODE).not.toContain('r.ok ? r.json() : null');
  });

  it('parses the body whether or not the request succeeded', () => {
    expect(POST).toContain('r.json().then(');
    expect(POST).toContain('if (r.ok) return d;');
  });

  it('keeps the server message rather than overwriting it', () => {
    // Object.assign puts the defaults FIRST so a real `error` from the server
    // wins. Reversed, every message would become "Request failed (HTTP 507)".
    const assign = POST.slice(POST.indexOf('Object.assign('));
    const defaultsAt = assign.indexOf("error: 'Request failed");
    const serverAt = assign.indexOf('d || {}');
    expect(defaultsAt).toBeGreaterThan(-1);
    expect(serverAt).toBeGreaterThan(defaultsAt);
  });

  it('still says something when the body is not JSON at all', () => {
    // a proxy error page or an empty 502 has no {error} to read
    expect(POST).toContain('.catch(function () {');
    expect(POST).toContain("return r.ok ? null : { ok: false, error: 'Request failed (HTTP ' + r.status + ')' };");
  });

  it('marks a failed response as not ok, so callers take the failure branch', () => {
    expect(POST).toContain('ok: false');
  });

  it('records the disk-guard case that exposed this', () => {
    expect(POST).toContain('temporary/working drive');
  });
});

describe('the grab button', () => {
  it('shows the server reason at every call site, not just one', () => {
    // There are five sendGrab call sites. Fixing the toast at one of them
    // would have left the other four saying nothing useful - which is why the
    // fix went into postJSON instead, where all five are served at once.
    const sites = [...VIEW.matchAll(/sendGrab\(buildGrabPayload\([^)]*\)\)/g)];
    expect(sites.length).toBeGreaterThanOrEqual(5);

    // every site that toasts a failure reads the server's message first
    const toasts = [...VIEW.matchAll(/toast\(\(res && res\.error\) \|\| /g)];
    expect(toasts.length).toBeGreaterThanOrEqual(3);
  });
});
