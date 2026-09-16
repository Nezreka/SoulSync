/** Deployment URL boundary for the React app and remaining classic scripts.
 * Loaded synchronously before either app. Empty base is deliberately a no-op.
 * Internal route IDs stay /discover; only browser-facing URLs receive the mount.
 */
(function () {
    'use strict';
    const base = document.querySelector('meta[name="soulsync-url-base"]')?.content || '';
    function strip(path) {
        return base && (path === base || path.startsWith(base + '/'))
            ? path.slice(base.length) || '/' : path;
    }
    function resolve(value) {
        if (typeof value !== 'string' || !base) return value;
        if (!value.startsWith('/') || value.startsWith('//')) return value;
        if (value === base || value.startsWith(base + '/') || value.startsWith(base + '?') || value.startsWith(base + '#')) return value;
        return base + value;
    }
    function transport(value) {
        const text = value instanceof URL ? value.href : value;
        if (typeof text !== 'string') return text;
        // Request and URL instances make root-relative API paths absolute.
        try {
            const url = new URL(text, location.href);
            if (url.origin === location.origin && /^\/(api(?:\/|$)|static\/|stream\/|auth\/|socket\.io(?:\/|$)|status$)/.test(url.pathname)) {
                return url.origin + resolve(url.pathname + url.search + url.hash);
            }
        } catch (_) { /* Preserve native browser error handling. */ }
        return resolve(text);
    }
    function markup(html) {
        if (typeof html !== 'string' || !base) return html;
        return html.replace(/((?:href|src|action|poster)\s*=\s*)(["'])(\/(?!\/)[^"']*)/gi,
            (_, attribute, quote, path) => attribute + quote + resolve(path))
            .replace(/(url\(\s*["']?)(\/(?!\/)[^\s)'";]+)/gi,
                (_, prefix, path) => prefix + resolve(path));
    }
    window.SoulSyncURL = {base, resolve, strip, markup};
    if (!base) return;

    const rawFetch = window.fetch;
    window.fetch = function (input, init) {
        if (input instanceof Request) {
            const url = transport(input.url);
            if (url !== input.url) input = new Request(url, input);
        } else input = transport(input);
        return rawFetch.call(this, input, init);
    };
    const rawOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...args) {
        return rawOpen.call(this, method, transport(url), ...args);
    };
    if (window.EventSource) {
        const NativeEventSource = window.EventSource;
        window.EventSource = class extends NativeEventSource {
            constructor(url, options) { super(transport(url), options); }
        };
    }
    for (const name of ['pushState', 'replaceState']) {
        const original = history[name];
        history[name] = function (state, unused, url) {
            return original.call(this, state, unused, url == null ? url : resolve(String(url)));
        };
    }
    const openWindow = window.open;
    window.open = function (url, ...args) { return openWindow.call(this, resolve(url), ...args); };

    // React sets URL attributes/properties; classic pages build HTML strings.
    // Rewrite BEFORE assignment so images/audio never first request the host root.
    const setAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
        if (/^(src|href|action|poster)$/i.test(name)) value = resolve(value);
        else if (name.toLowerCase() === 'style') value = markup(value);
        return setAttribute.call(this, name, value);
    };
    for (const [prototype, names] of [
        [HTMLImageElement.prototype, ['src']], [HTMLMediaElement.prototype, ['src']],
        [HTMLSourceElement.prototype, ['src']], [HTMLVideoElement.prototype, ['poster']],
        [HTMLAnchorElement.prototype, ['href']], [HTMLLinkElement.prototype, ['href']],
        [HTMLScriptElement.prototype, ['src']], [HTMLFormElement.prototype, ['action']],
    ]) {
        for (const name of names) {
            const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
            if (!descriptor?.set || !descriptor.configurable) continue;
            Object.defineProperty(prototype, name, {...descriptor,
                set(value) { descriptor.set.call(this, resolve(value)); }});
        }
    }
    const setCSSProperty = CSSStyleDeclaration.prototype.setProperty;
    CSSStyleDeclaration.prototype.setProperty = function (name, value, priority) {
        return setCSSProperty.call(this, name, markup(value), priority);
    };
    const htmlDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
    Object.defineProperty(Element.prototype, 'innerHTML', {...htmlDescriptor,
        set(value) { htmlDescriptor.set.call(this, markup(value)); }});
    const insertHTML = Element.prototype.insertAdjacentHTML;
    Element.prototype.insertAdjacentHTML = function (position, html) {
        return insertHTML.call(this, position, markup(html));
    };
})();
