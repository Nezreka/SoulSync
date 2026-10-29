// Live folder checks for Settings > Folders. Any input marked
// data-folder-check="<group>" gets a status line under it: ready and how much
// room is left, will be created, can't write there, a Windows path typed into
// Docker, the same folder as another row, a folder nested in a download
// folder, or (Docker) a folder on no mapped volume. Rows of one group are
// checked together so duplicates and nesting show up. The server side is
// core/onboarding/paths.py, shared with first-run setup.
(function () {
    'use strict';

    var CHECK_URL = '/api/onboarding/check-paths';
    var ENV_URL = '/api/onboarding/environment';
    var timers = {};
    var seq = {};
    var envPromise = null;

    function environment() {
        if (!envPromise) {
            envPromise = fetch(ENV_URL, { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : {}; })
                .catch(function () { return {}; });
        }
        return envPromise;
    }

    function inputs(group) {
        return Array.from(document.querySelectorAll('[data-folder-check="' + group + '"]'));
    }

    function label(input) {
        return input.getAttribute('data-folder-label') || 'another folder';
    }

    function freeSpace(bytes) {
        if (bytes == null || bytes < 0) return '';
        var gb = bytes / 1024 / 1024 / 1024;
        if (gb >= 1024) return (gb / 1024).toFixed(gb >= 10240 ? 0 : 1) + ' TB free';
        return (gb >= 100 ? gb.toFixed(0) : gb.toFixed(1)) + ' GB free';
    }

    function statusEl(input) {
        // after the input's group or row, never inside a flex row beside its button
        var anchor = input.closest('.path-input-group, [data-folder-row]') || input;
        var next = anchor.nextElementSibling;
        if (next && next.classList.contains('folder-check')) return next;
        var el = document.createElement('div');
        el.className = 'folder-check';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        anchor.insertAdjacentElement('afterend', el);
        return el;
    }

    function show(input, tone, text) {
        var el = statusEl(input);
        el.dataset.tone = tone || '';
        el.textContent = text || '';
        el.hidden = !text;
    }

    function render(input, result, byId) {
        if (!result) { show(input, '', ''); return; }
        var room = freeSpace(result.free_bytes);
        if (result.state === 'problem') {
            show(input, 'bad', result.message);
            return;
        }
        // a note outranks the plain ready line: these are the quiet mistakes
        var parent = result.inside ? byId[result.inside] : null;
        var nestedInInput = parent && (parent.getAttribute('data-folder-role') === 'input' ||
                                       input.getAttribute('data-folder-role') === 'input');
        if (nestedInInput) {
            show(input, 'warn', 'Inside your ' + label(parent).toLowerCase() +
                 '. Keep downloads and libraries side by side, not one inside the other');
            return;
        }
        if (result.unmapped) {
            show(input, 'warn', 'Not on a mapped volume, so files here live inside the container ' +
                 'and are lost when it is recreated. Map it in your compose file');
            return;
        }
        // an extra folder is only ever read: missing means unplugged or mistyped
        if (result.state === 'create' && input.getAttribute('data-folder-role') === 'existing') {
            show(input, 'warn', 'Not found. Check the drive is plugged in and the path is right');
            return;
        }
        if (result.state === 'create') {
            show(input, 'info', 'Doesn\'t exist yet. SoulSync creates it on first use' +
                 (room ? ' · ' + room : ''));
            return;
        }
        show(input, 'ok', 'Ready' + (room ? ' · ' + room : ''));
    }

    function check(group) {
        // a switched-off optional row (a 4K folder) is not checked
        var rows = inputs(group).filter(function (input) {
            return !input.closest('[data-folder-optional][hidden]');
        });
        var paths = {};
        var byId = {};
        rows.forEach(function (input, i) {
            var key = input.id || (group + '-' + i);
            byId[key] = input;
            if (input.value.trim()) paths[key] = input.value.trim();
            else show(input, '', '');
        });
        // rows that dropped out (switched off, removed) lose their line
        inputs(group).forEach(function (input) {
            if (rows.indexOf(input) === -1) show(input, '', '');
        });
        if (!Object.keys(paths).length) return Promise.resolve();
        var mine = seq[group] = (seq[group] || 0) + 1;
        return fetch(CHECK_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ paths: paths })
        })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || mine !== seq[group]) return;   // a newer check is on its way
                var results = d.results || {};
                Object.keys(paths).forEach(function (key) { render(byId[key], results[key], byId); });
            })
            .catch(function () { /* the check is a nicety, saving works without it */ });
    }

    function schedule(group, delay) {
        clearTimeout(timers[group]);
        timers[group] = setTimeout(function () { check(group); }, delay == null ? 500 : delay);
    }

    document.addEventListener('input', function (event) {
        var group = event.target && event.target.getAttribute && event.target.getAttribute('data-folder-check');
        if (group) schedule(group);
    });
    document.addEventListener('change', function (event) {
        var group = event.target && event.target.getAttribute && event.target.getAttribute('data-folder-check');
        if (group) schedule(group, 0);
    });

    // first check when a group comes into view: its values are loaded by then
    var seen = {};
    var observer = 'IntersectionObserver' in window ? new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            var group = entry.target.getAttribute('data-folder-check');
            if (group && !seen[group]) {
                seen[group] = true;
                schedule(group, 0);
            }
        });
    }) : null;

    function observe() {
        if (!observer) return;
        document.querySelectorAll('[data-folder-check]').forEach(function (input) {
            if (!input.dataset.folderObserved) {
                input.dataset.folderObserved = '1';
                observer.observe(input);
            }
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', observe);
    else observe();

    window.SoulSyncFolderCheck = {
        /** recheck a group now, e.g. after its values were loaded or a row added */
        refresh: function (group) { observe(); schedule(group, 0); },
        environment: environment
    };
})();
