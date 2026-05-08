/**
 * Discover Section Controller
 * ---------------------------
 *
 * Owns the lifecycle every discover-page section already does by hand:
 *
 *   1. show a loading spinner in the carousel container
 *   2. fetch the section's endpoint
 *   3. parse the response, decide whether the data is empty
 *   4. either show the empty state, render the items, or show an error
 *   5. wire any post-render handlers (download buttons, hover, etc)
 *   6. expose a refresh() method so the same lifecycle can re-fire
 *
 * Each section currently re-implements this 30 times in `discover.js`
 * with subtle drift — different empty-state messages, inconsistent
 * error handling (some console.debug, some silently swallowed, some
 * leave the spinner spinning forever), inconsistent refresh-button
 * feedback. This controller is the "lift what's truly shared"
 * extraction: register a section once, the controller handles the
 * lifecycle, the section provides only its renderer.
 *
 * Renderers stay per-section because section data shapes legitimately
 * differ (album cards vs artist circles vs playlist tiles vs track
 * rows). The controller is the lifecycle wrapper around those
 * renderers, not a forced visual abstraction.
 *
 * MIGRATION STATUS: this is the foundation commit. Only `Recent
 * Releases` has been migrated as a proof. The other sections
 * (Your Artists, Your Albums, Seasonal, Fresh Tape, The Archives,
 * etc) still use their hand-rolled load functions in discover.js
 * and will migrate one section per commit.
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
 * Future enhancements (not in this foundation commit):
 *   - global error toast wrapper (so users see something when an
 *     endpoint fails instead of the silent-empty-state default)
 *   - registry-driven section list (so the dead-section audit
 *     becomes registry edits, not section-by-section deletions)
 *   - per-section "requires X primary source" gate
 */

(function () {
    'use strict';

    // Validate the config object up front — bad configs fail fast at
    // section-register time instead of silently breaking on load.
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
        if (typeof cfg.fetchUrl !== 'string' || !cfg.fetchUrl) {
            throw new Error(`[discover:${cfg.id}] config.fetchUrl required`);
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
            // Wrapper element to show/hide when section becomes empty.
            // Null = always visible, only the contents change.
            sectionEl: null,
            // Hide the whole section if the response is empty
            // (vs showing an empty-state message inside the carousel).
            hideWhenEmpty: false,
            // For sections that need to show data even on empty (e.g.
            // "Recent Releases" with "no recent releases" copy).
            // When true and items.length === 0, render the empty state.
            renderEmptyState: true,
            // HTTP method + options for the fetch call. Default GET, no
            // body. fetchOptions is a function so callers can compute it
            // at load time (e.g. read filter selects).
            fetchMethod: 'GET',
            fetchOptions: null,
            // Pull the items array from the response. Default looks
            // for `data.items` then `data.albums` then `data.artists`.
            extractItems: null,
            // Override the success check. Default: require data.success
            // when present, otherwise treat any 2xx as success.
            isSuccess: null,
            // Override empty-detection. Default: items.length === 0.
            isEmpty: null,
            // Post-render hook — attach event handlers, etc.
            onRendered: null,
            // Lifecycle copy. Empty string = no message.
            loadingMessage: 'Loading...',
            emptyMessage: 'Nothing to show',
            errorMessage: 'Failed to load',
            // CSS class names — let surfaces override for styling.
            loadingClass: 'discover-loading',
            emptyClass: 'discover-empty',
            errorClass: 'discover-empty',
            // When true, log full error stacks to console.error. Default
            // logs to console.debug — keeps the console quiet for users
            // while staying inspectable when devtools is open.
            verboseErrors: false,
        }, cfg);

        const state = {
            phase: 'idle',  // idle | loading | rendered | empty | error
            lastData: null,
            lastError: null,
            inFlight: null,
        };

        function _setHtml(el, html) {
            if (el) el.innerHTML = html;
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
        }

        function _showSection() {
            const sectionEl = _resolveEl(config.sectionEl);
            if (sectionEl) sectionEl.style.display = '';
        }

        function _extractItems(data) {
            if (config.extractItems) return config.extractItems(data) || [];
            // Sensible defaults for the most common response shapes.
            if (Array.isArray(data?.items)) return data.items;
            if (Array.isArray(data?.albums)) return data.albums;
            if (Array.isArray(data?.artists)) return data.artists;
            if (Array.isArray(data?.tracks)) return data.tracks;
            if (Array.isArray(data?.results)) return data.results;
            return [];
        }

        function _isSuccess(data) {
            if (config.isSuccess) return config.isSuccess(data);
            // If `success` is present, require it to be truthy. Otherwise
            // a 2xx response with parseable JSON counts as success.
            if (data && Object.prototype.hasOwnProperty.call(data, 'success')) {
                return Boolean(data.success);
            }
            return true;
        }

        function _isEmpty(items, data) {
            if (config.isEmpty) return config.isEmpty(items, data);
            return !Array.isArray(items) || items.length === 0;
        }

        async function load() {
            // Coalesce concurrent loads — if a fetch is already in flight,
            // return the same promise rather than firing a second call.
            if (state.inFlight) return state.inFlight;

            const contentEl = _resolveEl(config.contentEl);
            if (!contentEl) {
                console.debug(`[discover:${config.id}] contentEl not found, skipping load`);
                return Promise.resolve();
            }

            _showLoading();

            const fetchOpts = (typeof config.fetchOptions === 'function')
                ? (config.fetchOptions() || {})
                : {};
            const init = Object.assign(
                { method: config.fetchMethod },
                fetchOpts,
            );

            const promise = (async () => {
                try {
                    const resp = await fetch(config.fetchUrl, init);
                    if (!resp.ok) {
                        throw new Error(`HTTP ${resp.status}`);
                    }
                    const data = await resp.json();
                    state.lastData = data;

                    if (!_isSuccess(data)) {
                        // Treat success=false as empty rather than error so
                        // the user sees the "nothing here" copy. Endpoints
                        // returning success=false with a network/auth reason
                        // can opt into error treatment via isSuccess.
                        _showEmpty();
                        return;
                    }

                    const items = _extractItems(data);
                    if (_isEmpty(items, data)) {
                        _showEmpty();
                        return;
                    }

                    _showSection();
                    const html = config.renderItems(items, data, { contentEl, config });
                    _setHtml(contentEl, html || '');
                    state.phase = 'rendered';

                    if (typeof config.onRendered === 'function') {
                        try {
                            config.onRendered({ contentEl, items, data, config });
                        } catch (hookErr) {
                            // Don't let a renderer hook error rip down the
                            // controller — log + continue.
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
            // Clear in-flight first so refresh() always re-fires the
            // network call (load() coalesces, refresh() bypasses).
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
            // Expose a copy so callers can inspect without holding onto
            // mutable internal state.
            return {
                phase: state.phase,
                hasData: state.lastData !== null,
                error: state.lastError,
            };
        }

        return { load, refresh, destroy, getState };
    }

    // Expose globally — the discover page is one big shared script
    // surface, no module system in play.
    window.createDiscoverSectionController = createDiscoverSectionController;
})();
