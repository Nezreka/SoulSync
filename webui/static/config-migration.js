/*
 * Config Migration modal (Kazimir's "checkout" export menu).
 *
 * One JSON bundle for BOTH sides (music config + video settings). Reuses the
 * arec-card modal look. Export tab: Copy JSON / Save .json, plus an "Include
 * credentials" toggle (off by default, warned — the file becomes plaintext
 * secrets). Import tab: paste or pick a .json and apply it to this install.
 */
(function () {
    'use strict';

    var _bundle = null;          // the currently-loaded export bundle
    var _withSecrets = false;

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type || 'info'); }

    window.openConfigExportModal = function () {
        var overlay = document.createElement('div');
        overlay.id = 'cfgx-overlay';
        overlay.className = 'arec-overlay';
        overlay.innerHTML =
            '<div class="arec-card" role="dialog" aria-label="Config migration">' +
                '<div class="arec-header">' +
                    '<div class="arec-title-wrap">' +
                        '<div class="arec-title"><span class="arec-dot"></span>Config Migration</div>' +
                        '<div class="arec-sub">Export every setting for both sides, or import a bundle</div>' +
                    '</div>' +
                    '<button class="arec-close" id="cfgx-close" title="Close (Esc)">&times;</button>' +
                '</div>' +
                '<div class="arec-toolbar">' +
                    '<div class="arec-tabs">' +
                        '<button class="arec-tab active" data-cfgx-tab="export">Export</button>' +
                        '<button class="arec-tab" data-cfgx-tab="import">Import</button>' +
                    '</div>' +
                    '<div class="arec-actions" data-cfgx-export-actions>' +
                        '<label class="cfgx-secrets" title="Embed real API keys/tokens/passwords. The file becomes plaintext credentials — keep it private.">' +
                            '<input type="checkbox" id="cfgx-secrets"> Include credentials</label>' +
                        '<button class="arec-btn" id="cfgx-copy">Copy JSON</button>' +
                        '<button class="arec-btn" id="cfgx-save">Save .json</button>' +
                    '</div>' +
                '</div>' +
                '<div class="arec-body" id="cfgx-body"><div class="arec-loading">Loading config…</div></div>' +
                '<div class="arec-footer" id="cfgx-footer"></div>' +
            '</div>';
        document.body.appendChild(overlay);
        requestAnimationFrame(function () { overlay.classList.add('visible'); });

        function close() {
            overlay.classList.remove('visible');
            document.removeEventListener('keydown', onKey);
            setTimeout(function () { overlay.remove(); }, 220);
        }
        function onKey(e) { if (e.key === 'Escape') close(); }
        document.addEventListener('keydown', onKey);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
        overlay.querySelector('#cfgx-close').onclick = close;

        var tab = 'export';
        overlay.querySelectorAll('[data-cfgx-tab]').forEach(function (b) {
            b.onclick = function () {
                tab = b.getAttribute('data-cfgx-tab');
                overlay.querySelectorAll('[data-cfgx-tab]').forEach(function (x) {
                    x.classList.toggle('active', x === b);
                });
                overlay.querySelector('[data-cfgx-export-actions]').style.display =
                    tab === 'export' ? '' : 'none';
                if (tab === 'export') loadExport(); else showImport();
            };
        });

        overlay.querySelector('#cfgx-secrets').onchange = function () {
            _withSecrets = this.checked;
            if (_withSecrets && !window.confirm(
                'Include credentials?\n\nThe exported file will contain your real API keys, ' +
                'tokens and passwords in plain text. Only do this for a private migration, ' +
                'and delete the file afterward.')) {
                this.checked = false; _withSecrets = false; return;
            }
            loadExport();
        };
        overlay.querySelector('#cfgx-copy').onclick = function () {
            if (!_bundle) return;
            var text = JSON.stringify(_bundle, null, 2);
            (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
                .then(function () { toast('Config copied as JSON', 'success'); })
                .catch(function () { toast('Copy failed', 'error'); });
        };
        overlay.querySelector('#cfgx-save').onclick = function () {
            if (!_bundle) return;
            var blob = new Blob([JSON.stringify(_bundle, null, 2)], { type: 'application/json' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            var stamp = (_bundle.exported_at || '').replace(/[:T]/g, '-').replace(/Z$/, '') || 'export';
            a.href = url;
            a.download = 'soulsync-config-' + stamp + (_withSecrets ? '-with-secrets' : '') + '.json';
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        };

        function loadExport() {
            var body = overlay.querySelector('#cfgx-body');
            var foot = overlay.querySelector('#cfgx-footer');
            body.innerHTML = '<div class="arec-loading">Loading config…</div>';
            fetch('/api/config/export?secrets=' + (_withSecrets ? '1' : '0'))
                .then(function (r) {
                    return r.json().then(function (j) {
                        if (!r.ok) throw new Error(j && j.error);
                        return j;
                    });
                })
                .then(function (b) {
                    _bundle = b;
                    body.innerHTML = '<pre class="cfgx-json">' + esc(JSON.stringify(b, null, 2)) + '</pre>';
                    var mk = b.music ? Object.keys(b.music).length : 0;
                    var vk = b.video ? Object.keys(b.video).length : 0;
                    foot.innerHTML =
                        '<span><b>' + mk + '</b> music sections</span>' +
                        '<span><b>' + vk + '</b> video settings</span>' +
                        '<span>' + (b.includes_secrets
                            ? '<b style="color:#f1c40f">credentials included</b>'
                            : 'credentials redacted') + '</span>';
                })
                .catch(function (err) {
                    // e.g. the login-mode gate on a credentials export — surface it
                    // and revert the toggle so the safe redacted view comes back.
                    var msg = (err && err.message) || 'Could not load config.';
                    body.innerHTML = '<div class="arec-error">' + esc(msg) + '</div>';
                    if (_withSecrets) {
                        _withSecrets = false;
                        var cb = overlay.querySelector('#cfgx-secrets');
                        if (cb) cb.checked = false;
                        toast(msg, 'error');
                    }
                });
        }

        function showImport() {
            _bundle = null;
            overlay.querySelector('#cfgx-footer').innerHTML = '';
            overlay.querySelector('#cfgx-body').innerHTML =
                '<div class="cfgx-import">' +
                    '<p class="cfgx-import-hint">Paste a config bundle exported from another install, or pick the .json file. Your existing credentials are never overwritten by a redacted export.</p>' +
                    '<input type="file" id="cfgx-file" accept="application/json,.json" class="cfgx-file">' +
                    '<textarea id="cfgx-paste" class="cfgx-paste" placeholder="…or paste the JSON here" spellcheck="false"></textarea>' +
                    '<button class="arec-btn cfgx-apply" id="cfgx-apply">Import this config</button>' +
                '</div>';
            overlay.querySelector('#cfgx-file').onchange = function (e) {
                var f = e.target.files && e.target.files[0];
                if (!f) return;
                var rd = new FileReader();
                rd.onload = function () { overlay.querySelector('#cfgx-paste').value = rd.result; };
                rd.readAsText(f);
            };
            overlay.querySelector('#cfgx-apply').onclick = function () {
                var raw = overlay.querySelector('#cfgx-paste').value.trim();
                if (!raw) { toast('Paste or choose a config file first', 'error'); return; }
                var data;
                try { data = JSON.parse(raw); }
                catch (err) { toast('That isn\'t valid JSON', 'error'); return; }
                var go = function () {
                    fetch('/api/config/import', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data) })
                        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                        .then(function (res) {
                            if (res.ok && res.j.success) {
                                toast('Config imported — restart SoulSync to apply everywhere', 'success');
                                close();
                            } else {
                                toast((res.j && res.j.error) || 'Import failed', 'error');
                            }
                        })
                        .catch(function () { toast('Import failed', 'error'); });
                };
                if (typeof showConfirmDialog === 'function') {
                    showConfirmDialog({
                        title: 'Import this config?',
                        message: 'This overwrites your current settings for both sides with the imported ones. A restart is recommended afterward.',
                        confirmText: 'Import', destructive: true,
                    }).then(function (ok) { if (ok) go(); });
                } else if (window.confirm('Overwrite current settings with the imported config?')) { go(); }
            };
        }

        loadExport();
    };
})();
