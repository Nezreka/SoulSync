/*
 * SoulSync — Video enrichment dashboard buttons (isolated).
 *
 * Polls /api/video/enrichment/<svc>/status and reflects it on the dashboard
 * TMDB/TVDB buttons (spin while running, tooltip status). Click toggles
 * pause/resume. The Manage Workers button fires 'soulsync:video-open-workers'
 * for the (isolated) video enrichment modal. Music code is never touched.
 *
 * Self-contained IIFE, no globals, no inline handlers. Only polls on the video
 * side so the video engine isn't spun up while the user is on the music side.
 */
(function () {
    'use strict';

    var SERVICES = ['tmdb', 'tvdb'];

    function onVideoSide() {
        return document.body.getAttribute('data-side') === 'video';
    }

    function btn(svc) { return document.querySelector('[data-video-enrich="' + svc + '"]'); }

    function setText(root, sel, text) {
        var n = root.querySelector(sel);
        if (n) n.textContent = text;
    }

    function reflect(svc, d) {
        var b = btn(svc);
        if (!b) return;
        b.classList.remove('active', 'paused', 'complete', 'disabled');
        if (!d.enabled) b.classList.add('disabled');
        else if (d.idle) b.classList.add('complete');
        else if (d.running && !d.paused) b.classList.add('active');
        else if (d.paused) b.classList.add('paused');

        var tip = document.querySelector('[data-video-enrich-tooltip="' + svc + '"]');
        if (!tip) return;
        var status = !d.enabled ? 'Not configured'
            : d.idle ? 'Complete'
                : (d.running && !d.paused) ? 'Running'
                    : d.paused ? 'Paused' : 'Idle';
        setText(tip, '[data-video-enrich-status]', status);
        var cur = (d.current_item && d.current_item.name)
            ? ((d.current_item.type || 'item') + ': ' + d.current_item.name)
            : (d.idle ? 'All matched' : 'No active matches');
        setText(tip, '[data-video-enrich-current]', cur);
        var matched = 0, total = 0, prog = d.progress || {};
        for (var k in prog) {
            if (Object.prototype.hasOwnProperty.call(prog, k)) {
                matched += prog[k].matched || 0;
                total += prog[k].total || 0;
            }
        }
        setText(tip, '[data-video-enrich-progress]', 'Progress: ' + matched + ' / ' + total);

        // Feed the idle worker-orbs animation real telemetry (isolated; no-op if
        // the orbs script isn't present). Drives the inbound pulses to the hub.
        if (window.videoWorkerOrbs && typeof window.videoWorkerOrbs.onStatus === 'function') {
            window.videoWorkerOrbs.onStatus(svc, d);
        }
    }

    function pollOne(svc) {
        fetch('/api/video/enrichment/' + svc + '/status', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && !d.error) reflect(svc, d); })
            .catch(function () { /* ignore */ });
    }

    function poll() {
        if (onVideoSide() && !document.hidden) SERVICES.forEach(pollOne);
    }

    function toggle(svc) {
        var b = btn(svc);
        if (!b || b.classList.contains('disabled')) return;
        var action = b.classList.contains('paused') ? 'resume' : 'pause';
        fetch('/api/video/enrichment/' + svc + '/' + action,
            { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function () { setTimeout(function () { pollOne(svc); }, 200); })
            .catch(function () { /* ignore */ });
    }

    function init() {
        SERVICES.forEach(function (svc) {
            var b = btn(svc);
            if (b) b.addEventListener('click', function () { toggle(svc); });
        });
        var manage = document.querySelector('[data-video-manage-workers]');
        if (manage) {
            manage.addEventListener('click', function () {
                document.dispatchEvent(new CustomEvent('soulsync:video-open-workers'));
            });
        }
        poll();
        setInterval(poll, 2500);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
