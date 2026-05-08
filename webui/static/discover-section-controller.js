/**
 * Discover Section Controller
 * ---------------------------
 *
 * Owns the lifecycle every discover-page section already does by hand:
 *
 *   1. show a loading spinner in the carousel container
 *   2. fetch the section's endpoint (or use pre-fetched data)
 *   3. parse the response, decide whether the data is empty
 *   4. either show the empty state, render the items, show a stale
 *      "still updating" state, or show an error
 *   5. wire any post-render handlers (download buttons, hover, etc)
 *   6. expose a refresh() method so the same lifecycle can re-fire
 *
 * Each section currently re-implements this by hand in `discover.js`
 * with subtle drift — different empty-state messages, inconsistent
 * error handling, inconsistent refresh-button feedback, no consistent
 * error toast. This controller is the "lift what's truly shared"
 * extraction: register a section once, the controller handles the
 * lifecycle, the section provides only its renderer.
 *
 * Renderers stay per-section because section data shapes legitimately
 * differ (album cards vs artist circles vs playlist tiles vs track
 * rows). The controller is the lifecycle wrapper around those
 * renderers, not a forced visual abstraction.
 *
 * USAGE:
 *
 *   const ctrl = createDiscoverSectionController({
 *       id: 'recent-releases',
 *       contentEl: '#recent-releases-carousel',
 *       fetchUrl: '/api/discover/recent-releases',
 *       extractItems: (data) => data.albums || [],
 *       renderItems: (items, data, ctx) => buildCardsHtml(items),
 *       onRendered: (ctx) => attachClickHandlers(ctx.contentEl),
 *       loadingMessage: 'Loading recent releases...',
 *       emptyMessage: 'No recent releases found',
 *       errorMessage: 'Failed to load recent releases',
 *   });
 *   ctrl.load();
 *
 * EXTENSIONS:
 *
 *   `fetchUrl` accepts a function returning a string for sections
 *   whose endpoint depends on runtime state (e.g. seasonal playlist
 *   keyed by `currentSeasonKey`).
 *
 *   `data` lets a section bypass fetch entirely — the controller still
 *   runs success / empty / render / onRendered, just without going to
 *   the network. Use when a parent already fetched and just wants the
 *   shared lifecycle. `data` may be a value or a `() => value`
 *   function. Sections must supply EITHER `fetchUrl` OR `data`, not
 *   both.
 *
 *   `beforeLoad(ctx)` runs before the spinner shows. Useful for
 *   ensuring `contentEl` exists (e.g. dynamically inserted sections)
 *   or updating sibling headers / subtitles before any visual change.
 *
 *   `onSuccess(data, ctx)` runs after the success check passes but
 *   before isEmpty / isStale checks. Cleaner home for header text
 *   updates that depend on response data (vs folding them into
 *   renderItems).
 *
 *   `isStale(items, data)` + `onStale(ctx)` give sections a third
 *   render state for "data is empty but the upstream is still
 *   discovering". Returning true from `isStale` renders the stale
 *   state (default: spinner + "Updating..." copy, override via
 *   `renderStale` or `staleMessage`) and fires `onStale` so the
 *   section can start a poller. Stale wins over empty when both apply.
 *
 *   `showErrorToast: true` opens a global `showToast(...)` on error
 *   in addition to the in-section error block. Default off — sections
 *   that have no recovery action shouldn't shout at the user.
 *
 *   If `renderItems` returns null / undefined, the controller leaves
 *   `contentEl` untouched. Lets a renderer do its own DOM manipulation
 *   (e.g. dynamic per-item child containers) without fighting the
 *   controller's `innerHTML` swap.
 */

(function () {
    'use strict';

    function _validateConfig(cfg) {
        if (!cfg || typeof cfg !== 'object') {
            throw new Error('createDiscoverSectionController: config required');
        }
        if (typeof cfg.id !== 'string' || !cfg.id) {
            throw new Error('createDiscoverSectionController: config.id required (string)');
        }
        if (typeof cfg.contentEl !== 'string' && !(cfg.contentEl instanceof Element)) {
            throw new Error(`[discover:${cfg.id}] config.contentEl required (selector or Element)`);
        }
        const hasFetch = (typeof cfg.fetchUrl === 'string' && cfg.fetchUrl) || typeof cfg.fetchUrl === 'function';
        const hasData = cfg.data !== undefined;
        if (!hasFetch && !hasData) {
            throw new Error(`[discover:${cfg.id}] either config.fetchUrl or config.data required`);
        }
        if (hasFetch && hasData) {
            throw new Error(`[discover:${cfg.id}] config.fetchUrl and config.data are mutually exclusive`);
        }
        if (typeof cfg.renderItems !== 'function') {
            throw new Error(`[discover:${cfg.id}] config.renderItems required (function)`);
        }
    }

    function _resolveEl(el) {
        if (el instanceof Element) return el;
        if (typeof el === 'string') return document.querySelector(el);
        return null;
    }

    /**
     * @param {Object} cfg - Section config (see file header for shape)
     * @returns {Object} Public API: { load, refresh, destroy, getState }
     */
    function createDiscoverSectionController(cfg) {
        _validateConfig(cfg);

        const config = Object.assign({
            sectionEl: null,
            hideWhenEmpty: false,
            renderEmptyState: true,
            fetchMethod: 'GET',
            fetchOptions: null,
            // Either fetchUrl (string or () => string) or data
            // (value or () => value). Validated mutually exclusive above.
            extractItems: null,
            isSuccess: null,
            isEmpty: null,
            // Stale = data is empty but upstream is still discovering.
            // Returning true here renders the stale state instead of
            // empty, and fires onStale so the section can poll.
            isStale: null,
            renderStale: null,
            staleMessage: 'Updating...',
            // Hooks
            beforeLoad: null,    // (ctx) => void   — before spinner shows
            onSuccess: null,     // (data, ctx) => void — after success gate
            onStale: null,       // (ctx) => void   — when stale state renders
            onRendered: null,    // (ctx) => void   — after content renders
            // UX copy
            loadingMessage: 'Loading...',
            emptyMessage: 'Nothing to show',
            errorMessage: 'Failed to load',
            loadingClass: 'discover-loading',
            emptyClass: 'discover-empty',
            errorClass: 'discover-empty',
            staleClass: 'discover-loading',
            // Errors
            verboseErrors: false,
            showErrorToast: false,  // also fire window.showToast on error
        }, cfg);

        const state = {
            phase: 'idle',  // idle | loading | rendered | empty | stale | error
            lastData: null,
            lastError: null,
            inFlight: null,
        };

        function _setHtml(el, html) {
            if (el) el.innerHTML = html;
        }

        function _ctx(extra) {
            return Object.assign(
                { contentEl: _resolveEl(config.contentEl), config },
                extra || {},
            );
        }

        function _showLoading() {
            const contentEl = _resolveEl(config.contentEl);
            if (!contentEl) return;
            const msg = config.loadingMessage
                ? `<p>${config.loadingMessage}</p>`
                : '';
            _setHtml(contentEl, `
                <div class="${config.loadingClass}">
                    <div class="loading-spinner"></div>
                    ${msg}
                </div>
            `);
            state.phase = 'loading';
        }

        function _showEmpty() {
            const contentEl = _resolveEl(config.contentEl);
            if (!contentEl) return;
            if (config.hideWhenEmpty) {
                const sectionEl = _resolveEl(config.sectionEl);
                if (sectionEl) sectionEl.style.display = 'none';
                state.phase = 'empty';
                return;
            }
            if (config.renderEmptyState) {
                _setHtml(contentEl, `
                    <div class="${config.emptyClass}">
                        <p>${config.emptyMessage}</p>
                    </div>
                `);
            } else {
                _setHtml(contentEl, '');
            }
            state.phase = 'empty';
        }

        function _showStale(items, data) {
            const contentEl = _resolveEl(config.contentEl);
            if (!contentEl) return;
            _showSection();
            // Custom renderStale wins. Otherwise default spinner + copy.
            let html;
            if (typeof config.renderStale === 'function') {
                try {
                    html = config.renderStale(items, data, _ctx({ items, data }));
                } catch (err) {
                    console.debug(`[discover:${config.id}] renderStale threw:`, err);
                    html = null;
                }
            }
            if (html === null || html === undefined) {
                html = `
                    <div class="${config.staleClass}">
                        <div class="loading-spinner"></div>
                        <p>${config.staleMessage}</p>
                    </div>
                `;
            }
            _setHtml(contentEl, html);
            state.phase = 'stale';

            if (typeof config.onStale === 'function') {
                try {
                    config.onStale(_ctx({ items, data }));
                } catch (err) {
                    console.debug(`[discover:${config.id}] onStale hook threw:`, err);
                }
            }
        }

        function _showError(error) {
            const contentEl = _resolveEl(config.contentEl);
            if (!contentEl) return;
            _setHtml(contentEl, `
                <div class="${config.errorClass}">
                    <p>${config.errorMessage}</p>
                </div>
            `);
            state.phase = 'error';
            state.lastError = error;
            const log = config.verboseErrors ? console.error : console.debug;
            log(`[discover:${config.id}]`, error);
            if (config.showErrorToast && typeof window.showToast === 'function') {
                try {
                    window.showToast(config.errorMessage, 'error');
                } catch (toastErr) {
                    console.debug(`[discover:${config.id}] toast failed:`, toastErr);
                }
            }
        }

        function _showSection() {
            const sectionEl = _resolveEl(config.sectionEl);
            if (sectionEl) sectionEl.style.display = '';
        }

        function _extractItems(data) {
            if (config.extractItems) return config.extractItems(data) || [];
            if (Array.isArray(data?.items)) return data.items;
            if (Array.isArray(data?.albums)) return data.albums;
            if (Array.isArray(data?.artists)) return data.artists;
            if (Array.isArray(data?.tracks)) return data.tracks;
            if (Array.isArray(data?.results)) return data.results;
            return [];
        }

        function _isSuccess(data) {
            if (config.isSuccess) return config.isSuccess(data);
            if (data && Object.prototype.hasOwnProperty.call(data, 'success')) {
                return Boolean(data.success);
            }
            return true;
        }

        function _isEmpty(items, data) {
            if (config.isEmpty) return config.isEmpty(items, data);
            return !Array.isArray(items) || items.length === 0;
        }

        function _isStale(items, data) {
            if (typeof config.isStale !== 'function') return false;
            try {
                return Boolean(config.isStale(items, data));
            } catch (err) {
                console.debug(`[discover:${config.id}] isStale threw:`, err);
                return false;
            }
        }

        function _resolveFetchUrl() {
            if (typeof config.fetchUrl === 'function') return config.fetchUrl();
            return config.fetchUrl;
        }

        function _resolveStaticData() {
            if (typeof config.data === 'function') return config.data();
            return config.data;
        }

        async function load() {
            // Coalesce concurrent loads — refresh() bypasses the coalesce.
            if (state.inFlight) return state.inFlight;

            // Run beforeLoad first so it can set up `contentEl` (dynamic
            // section creation) before the visibility check below.
            if (typeof config.beforeLoad === 'function') {
                try {
                    config.beforeLoad(_ctx());
                } catch (err) {
                    console.debug(`[discover:${config.id}] beforeLoad hook threw:`, err);
                }
            }

            const contentEl = _resolveEl(config.contentEl);
            if (!contentEl) {
                console.debug(`[discover:${config.id}] contentEl not found, skipping load`);
                return Promise.resolve();
            }

            _showLoading();

            const promise = (async () => {
                try {
                    let data;
                    if (config.data !== undefined) {
                        // No-fetch mode — parent already has the data.
                        data = _resolveStaticData();
                    } else {
                        const fetchOpts = (typeof config.fetchOptions === 'function')
                            ? (config.fetchOptions() || {})
                            : {};
                        const init = Object.assign(
                            { method: config.fetchMethod },
                            fetchOpts,
                        );
                        const url = _resolveFetchUrl();
                        const resp = await fetch(url, init);
                        if (!resp.ok) {
                            throw new Error(`HTTP ${resp.status}`);
                        }
                        data = await resp.json();
                    }
                    state.lastData = data;

                    if (!_isSuccess(data)) {
                        _showEmpty();
                        return;
                    }

                    if (typeof config.onSuccess === 'function') {
                        try {
                            config.onSuccess(data, _ctx({ data }));
                        } catch (err) {
                            console.debug(`[discover:${config.id}] onSuccess hook threw:`, err);
                        }
                    }

                    const items = _extractItems(data);

                    // Stale wins over empty — section is empty *now* but
                    // upstream is still discovering, so show updating UI
                    // rather than the bare "nothing here" copy.
                    if (_isStale(items, data)) {
                        _showStale(items, data);
                        return;
                    }

                    if (_isEmpty(items, data)) {
                        _showEmpty();
                        return;
                    }

                    _showSection();
                    const html = config.renderItems(items, data, _ctx({ items, data }));
                    // null / undefined return = renderer is doing its own
                    // DOM work, leave the container alone.
                    if (html !== null && html !== undefined) {
                        _setHtml(contentEl, html);
                    }
                    state.phase = 'rendered';

                    if (typeof config.onRendered === 'function') {
                        try {
                            config.onRendered(_ctx({ items, data }));
                        } catch (hookErr) {
                            console.debug(`[discover:${config.id}] onRendered hook threw:`, hookErr);
                        }
                    }
                } catch (err) {
                    _showError(err);
                } finally {
                    state.inFlight = null;
                }
            })();

            state.inFlight = promise;
            return promise;
        }

        async function refresh() {
            state.inFlight = null;
            return load();
        }

        function destroy() {
            state.inFlight = null;
            state.lastData = null;
            state.lastError = null;
            state.phase = 'idle';
        }

        function getState() {
            return {
                phase: state.phase,
                hasData: state.lastData !== null,
                error: state.lastError,
            };
        }

        return { load, refresh, destroy, getState };
    }

    window.createDiscoverSectionController = createDiscoverSectionController;
})();
