/* Song identification — the microphone inside the Search bar.

   A clip is recorded here (or dropped in as a file) and posted to
   /api/listen/identify, which fingerprints it against Shazam. What comes back
   is written into the search field as "<artist> <title>" and the ordinary
   search runs, so the song appears on the usual result page — Top result,
   tracks, and the same Add to Wishlist control any other search gives you.
   This file never draws its own result card: it listens, then hands the query
   to the page that already knows how to show and add the song.

   The sheet is a state machine with one owner at a time. Every capture or
   upload gets an operation number; close, dismiss and a file drop all bump it,
   so a permission prompt, a MediaRecorder stop event or a fetch that arrives
   after the user has moved on can neither upload nor paint the reopened sheet.
   A file chosen during a capture takes ownership first: the recorder is torn
   down with its handlers detached, and only the file is sent.

   The microphone needs a secure context: over plain http:// a LAN address the
   browser does not expose getUserMedia at all. When that is the case the sheet
   says so and offers the file path instead of failing at the click. The sheet
   also names Shazam as the third party that receives the clip before any
   capture starts or any file is read. */

(function () {
    'use strict';

    if (window.__soulsyncListenLoaded) return;
    window.__soulsyncListenLoaded = true;

    var CLIP_MS = 9000;          // Shazam resolves a song from a few seconds; 9 s covers a noisy room
    var SILENCE_RMS = 0.004;     // below this the clip is quiet enough to say so before the server does
    var ENDPOINT = '/api/listen/identify';
    var STATUS_ENDPOINT = '/api/listen/status';
    var READY_TIMEOUT_MS = 8000;
    var BAR_ID = 'enhanced-search-bar';
    var MIC_ID = 'listen-mic-btn';
    var DISCLOSURE = 'Your clip is sent to Shazam, a third-party service from Apple, to be identified. '
        + 'SoulSync does not keep the clip.';

    /* Original F Note Sonar artwork: all three tapered crescents at both sizes.
       The raster is a tracked static asset and the CSS mask paints it in the
       current colour, so the approved silhouette is not redrawn or inlined. */
    var MARK_HTML = '<span class="listen-mark" aria-hidden="true"></span>';

    var state = {
        stream: null,
        recorder: null,
        audioCtx: null,
        analyser: null,
        raf: 0,
        timer: 0,
        countdown: 0,
        chunks: [],
        peak: 0,
        busy: false,
        open: false,
        op: 0,                    // identity of the one capture/upload allowed to act
        phase: 'idle',            // idle | preflight | starting | listening | identifying | failed
        abort: null,              // identify fetch
        readyAbort: null,         // readiness fetch
        readiness: null,
    };

    var el = {};

    function isCurrent(op) {
        return state.open && op === state.op;
    }

    function isAbort(err) {
        return !!err && (err.name === 'AbortError' || err.code === 20);
    }

    function stopTracks(stream) {
        if (!stream || typeof stream.getTracks !== 'function') return;
        stream.getTracks().forEach(function (track) {
            try { track.stop(); } catch (err) { /* already gone */ }
        });
    }

    /* ── the button in the search bar ───────────────────────────────────── */

    function micButton() {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.id = MIC_ID;
        btn.className = 'listen-mic-btn';
        btn.title = 'Identify a song playing nearby (Shazam)';
        btn.setAttribute('aria-label', 'Identify the song playing nearby');
        btn.innerHTML = MARK_HTML;
        btn.addEventListener('click', open);
        return btn;
    }

    /* The search bar is React-rendered, so this button is adopted rather than
       owned: React may drop it on any re-render or route change, and the
       observer puts it back. Scheduled through a timer so a busy page cannot
       turn the observer into a render loop. */
    var attaching = false;

    function ensureMic() {
        if (attaching) return;
        var bar = document.getElementById(BAR_ID);
        if (!bar) return;
        if (bar.querySelector('#' + MIC_ID)) return;
        var btn = micButton();
        // last child is the source picker in today's SearchBar; putting the
        // button before it keeps it next to the picker even when the clear
        // button is also present.
        var anchor = bar.lastElementChild;
        if (anchor && anchor !== btn) bar.insertBefore(btn, anchor);
        else bar.appendChild(btn);
    }

    function scheduleMic() {
        if (attaching) return;
        attaching = true;
        // a timer, not requestAnimationFrame: rAF does not run in a hidden tab
        window.setTimeout(function () {
            attaching = false;
            try { ensureMic(); } catch (err) { console.debug('[listen] mic placement failed:', err); }
        }, 16);
    }

    /* ── the sheet ──────────────────────────────────────────────────────── */

    function buildSheet() {
        var overlay = document.createElement('div');
        overlay.className = 'listen-overlay';
        overlay.id = 'listen-overlay';
        overlay.innerHTML = ''
            + '<div class="listen-sheet" role="dialog" aria-modal="true" aria-labelledby="listen-title">'
            + '  <button type="button" class="listen-close" data-listen-close aria-label="Close">&times;</button>'
            + '  <div class="listen-orb">' + MARK_HTML + '</div>'
            + '  <h2 class="listen-title" id="listen-title">Identify a song</h2>'
            + '  <p class="listen-status" data-listen-status role="status" aria-live="polite">Ready when you are.</p>'
            + '  <div class="listen-meter" data-listen-meter aria-hidden="true"></div>'
            + '  <p class="listen-disclosure" data-listen-disclosure>' + DISCLOSURE + '</p>'
            + '  <div class="listen-actions" data-listen-actions></div>'
            + '  <div class="listen-note" data-listen-note></div>'
            + '  <div class="listen-alt">'
            + '    <span>or <button type="button" data-listen-upload>upload a clip</button></span>'
            + '    <input type="file" accept="audio/*,video/*" hidden data-listen-file>'
            + '  </div>'
            + '</div>';
        document.body.appendChild(overlay);

        el.overlay = overlay;
        el.sheet = overlay.querySelector('.listen-sheet');
        el.orb = overlay.querySelector('.listen-orb');
        el.title = overlay.querySelector('.listen-title');
        el.status = overlay.querySelector('[data-listen-status]');
        el.meter = overlay.querySelector('[data-listen-meter]');
        el.disclosure = overlay.querySelector('[data-listen-disclosure]');
        el.actions = overlay.querySelector('[data-listen-actions]');
        el.note = overlay.querySelector('[data-listen-note]');
        el.file = overlay.querySelector('[data-listen-file]');

        for (var i = 0; i < 15; i++) el.meter.appendChild(document.createElement('b'));

        el.orb.addEventListener('click', function () {
            if (state.phase === 'listening') stopRecording();
        });
        overlay.querySelector('[data-listen-close]').addEventListener('click', close);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
        overlay.querySelector('[data-listen-upload]').addEventListener('click', chooseFile);
        el.file.addEventListener('change', function () {
            var file = el.file.files && el.file.files[0];
            el.file.value = '';
            if (file) uploadFile(file);
        });
        overlay.addEventListener('dragover', function (e) {
            if (!hasFiles(e)) return;
            e.preventDefault();
            overlay.classList.add('dragging');
        });
        overlay.addEventListener('dragleave', function (e) {
            if (e.target === overlay) overlay.classList.remove('dragging');
        });
        overlay.addEventListener('drop', function (e) {
            if (!hasFiles(e)) return;
            e.preventDefault();
            overlay.classList.remove('dragging');
            var file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
            if (file) uploadFile(file);
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && el.overlay && el.overlay.classList.contains('visible')) close();
        });
    }

    function hasFiles(e) {
        var types = e && e.dataTransfer && e.dataTransfer.types;
        if (!types) return false;
        for (var i = 0; i < types.length; i++) {
            if (types[i] === 'Files') return true;
        }
        return false;
    }

    function chooseFile() {
        if (!el.file) buildSheet();
        el.file.click();
    }

    function micUsable() {
        return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia
            && window.MediaRecorder);
    }

    function showSheet() {
        if (!el.overlay) buildSheet();
        state.open = true;
        el.overlay.classList.add('visible');
        el.overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('listen-open');
    }

    function open() {
        showSheet();
        var op = ++state.op;      // dismisses anything the previous open left running
        abortRequest();
        abortReadiness();
        teardownCapture();
        reset();
        state.phase = 'preflight';
        state.busy = false;
        state.readiness = null;

        if (!micUsable()) {
            el.overlay.classList.add('no-mic');
            setStatus('The microphone needs a secure address (https).');
            showNote('Browsers refuse microphone capture on a plain <code>http://</code> address. '
                + 'Over https — this server\'s own https address, for example — the microphone opens here. '
                + 'Until then a clip you already have can be identified instead.');
            setActions([{ label: 'Upload a clip', primary: true, upload: true, onClick: chooseFile }]);
            return;
        }

        setStatus('Checking the recogniser…');
        setActions([{ label: 'Cancel', ghost: true, onClick: close }]);
        checkReadiness(op).then(function (ready) {
            if (!isCurrent(op) || state.phase !== 'preflight') return;
            state.readiness = ready;
            if (!ready.ok) {
                state.phase = 'failed';
                setStatus(ready.reason || 'Song identification is unavailable.', true);
                showPlainNote(ready.reason || 'Song identification is unavailable.');
                setActions([
                    { label: 'Upload a clip', primary: true, upload: true, onClick: chooseFile },
                    { label: 'Close', ghost: true, onClick: close }
                ]);
                return;
            }
            setStatus('Ready when you are.');
            setActions([
                { label: 'Start listening', primary: true, onClick: startListening },
                { label: 'Upload a clip', upload: true, onClick: chooseFile }
            ]);
        });
    }

    function close() {
        state.open = false;
        state.op += 1;
        state.phase = 'idle';
        state.busy = false;
        abortRequest();
        abortReadiness();
        teardownCapture();
        if (!el.overlay) return;
        el.overlay.classList.remove('visible');
        el.overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('listen-open');
    }

    function reset() {
        el.overlay.classList.remove('listening', 'working', 'dragging', 'no-mic');
        el.actions.innerHTML = '';
        el.note.className = 'listen-note';
        el.note.innerHTML = '';
        el.title.textContent = 'Identify a song';
        setStatus('Ready when you are.');
        for (var i = 0; i < el.meter.children.length; i++) {
            el.meter.children[i].style.height = '3px';
            el.meter.children[i].style.opacity = '0.55';
        }
        state.peak = 0;
        state.chunks = [];
        setActions([{ label: 'Cancel', ghost: true, onClick: close }]);
    }

    function setStatus(text, isError) {
        el.status.textContent = text;
        el.status.classList.toggle('error', !!isError);
    }

    function showNote(html) {
        el.note.innerHTML = html;
        el.note.classList.add('visible');
    }

    function showPlainNote(text) {
        el.note.textContent = text;
        el.note.classList.add('visible');
    }

    function setActions(list) {
        el.actions.innerHTML = '';
        // an explicit Upload button already says it; the "or upload a clip"
        // footer would be the same action twice on the preflight/failure sheet.
        var hasUpload = list.some(function (action) { return action.upload === true; });
        if (el.overlay) el.overlay.classList.toggle('has-upload', hasUpload);
        list.forEach(function (action) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'listen-btn' + (action.primary ? ' primary' : '') + (action.ghost ? ' ghost' : '');
            button.textContent = action.label;
            button.addEventListener('click', action.onClick);
            el.actions.appendChild(button);
        });
    }

    /* ── readiness ──────────────────────────────────────────────────────── */

    function abortReadiness() {
        if (!state.readyAbort) return;
        try { state.readyAbort.abort(); } catch (err) { /* already aborted */ }
        state.readyAbort = null;
    }

    function checkReadiness(op) {
        abortReadiness();
        var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
        if (ctrl) state.readyAbort = ctrl;
        var timer = window.setTimeout(function () {
            if (ctrl) { try { ctrl.abort(); } catch (err) { /* already aborted */ } }
        }, READY_TIMEOUT_MS);

        return fetch(STATUS_ENDPOINT, {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            cache: 'no-store',
            signal: ctrl ? ctrl.signal : undefined
        }).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (body) {
                if (!res.ok) {
                    return { ok: false, reason: body.message || ('The recogniser answered ' + res.status + '.') };
                }
                return {
                    ok: body.available === true,
                    reason: body.reason || 'Song identification is unavailable on this server.'
                };
            });
        }).catch(function (err) {
            return {
                ok: false,
                reason: isAbort(err)
                    ? 'The readiness check was cancelled.'
                    : 'The recogniser status could not be checked.'
            };
        }).then(function (ready) {
            window.clearTimeout(timer);
            if (ctrl && state.readyAbort === ctrl) state.readyAbort = null;
            return ready;
        });
    }

    /* ── recording ──────────────────────────────────────────────────────── */

    function pickMimeType() {
        var candidates = [
            'audio/webm;codecs=opus', 'audio/webm',
            'audio/ogg;codecs=opus', 'audio/ogg',
            'audio/mp4;codecs=mp4a.40.2', 'audio/mp4'
        ];
        for (var i = 0; i < candidates.length; i++) {
            if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(candidates[i])) {
                return candidates[i];
            }
        }
        return '';
    }

    function startListening() {
        if (state.busy || !micUsable()) {
            if (!micUsable()) fail('This browser cannot record audio. Try uploading a clip.');
            return;
        }
        var op = ++state.op;
        abortRequest();
        abortReadiness();
        teardownCapture();
        reset();
        state.phase = 'starting';
        state.busy = true;
        state.readiness = null;
        setStatus('Checking the recogniser…');
        setActions([{ label: 'Cancel', ghost: true, onClick: close }]);

        checkReadiness(op).then(function (ready) {
            if (!isCurrent(op) || state.phase !== 'starting') return;
            if (!ready.ok) {
                state.busy = false;
                fail(ready.reason || 'Song identification is unavailable.');
                return;
            }
            requestMic(op);
        });
    }

    function requestMic(op) {
        setStatus('Waiting for the microphone…');
        var request;
        try {
            request = navigator.mediaDevices.getUserMedia({
                audio: {
                    // Shazam answers less often on processed audio: echo cancelling
                    // and auto-gain were built for speech and chew the music up
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false
                }
            });
        } catch (err) {
            state.busy = false;
            micFailed(err);
            return;
        }
        request.then(function (stream) {
            // Cancelled (or replaced by a file) while the permission prompt was
            // open: the browser still hands over a live stream. Stop it here;
            // it belongs to no operation any more.
            if (!isCurrent(op)) {
                stopTracks(stream);
                return;
            }
            attachStream(stream, op);
        }).catch(function (err) {
            if (!isCurrent(op)) return;
            state.busy = false;
            micFailed(err);
        });
    }

    function micFailed(err) {
        var name = err && err.name;
        if (name === 'NotAllowedError' || name === 'SecurityError') {
            fail('Microphone access was refused. Allow it for this site, or upload a clip.');
        } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
            fail('No microphone was found. Upload a clip instead.');
        } else {
            fail('The microphone could not be opened: ' + (err && err.message ? err.message : name || 'unknown error'));
        }
    }

    function attachStream(stream, op) {
        state.stream = stream;
        state.chunks = [];
        state.peak = 0;
        state.busy = false;

        var mimeType = pickMimeType();
        var recorder;
        try {
            recorder = mimeType ? new MediaRecorder(stream, { mimeType: mimeType }) : new MediaRecorder(stream);
        } catch (err) {
            console.error('[listen] MediaRecorder refused:', err);
            stopTracks(stream);
            state.stream = null;
            fail('This browser could not record audio. Try uploading a clip.');
            return;
        }

        state.recorder = recorder;
        state.phase = 'listening';
        recorder.ondataavailable = function (e) {
            if (!isCurrent(op) || state.recorder !== recorder) return;
            if (e.data && e.data.size) state.chunks.push(e.data);
        };
        recorder.onstop = function () {
            finishRecording(recorder, mimeType, op);
        };
        recorder.onerror = function (e) {
            if (!isCurrent(op) || state.recorder !== recorder) return;
            var message = (e && e.error && e.error.message) || 'The recording stopped unexpectedly.';
            teardownCapture();
            fail(message);
        };

        startMeter(stream, op);
        try {
            recorder.start();
        } catch (err) {
            console.error('[listen] start failed:', err);
            teardownCapture();
            fail('The recording could not be started. Try again.');
            return;
        }

        el.overlay.classList.add('listening');
        el.overlay.classList.remove('working', 'dragging');
        setStatus('Listening…');
        setActions([
            { label: 'Identify now', primary: true, onClick: stopRecording },
            { label: 'Cancel', ghost: true, onClick: close }
        ]);
        window.clearTimeout(state.timer);
        state.timer = window.setTimeout(function () {
            if (isCurrent(op) && state.phase === 'listening') stopRecording();
        }, CLIP_MS);
        countdown(op);
    }

    function countdown(op) {
        var start = Date.now();
        var total = Math.round(CLIP_MS / 1000);
        (function tick() {
            if (!isCurrent(op) || state.phase !== 'listening') return;
            var remain = Math.ceil(total - (Date.now() - start) / 1000);
            setStatus(remain > 0 ? 'Listening… ' + remain + 's' : 'Listening…');
            if (remain > 0) state.countdown = window.setTimeout(tick, 500);
            else state.countdown = 0;
        })();
    }

    function startMeter(stream, op) {
        try {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            var ctx = new Ctx();
            var source = ctx.createMediaStreamSource(stream);
            var analyser = ctx.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);
            state.audioCtx = ctx;
            state.analyser = analyser;
            var data = new Uint8Array(analyser.frequencyBinCount);
            var bars = el.meter.children;

            (function frame() {
                // a stale frame must not read the NEXT capture's analyser
                if (!isCurrent(op) || state.analyser !== analyser || state.audioCtx !== ctx) return;
                analyser.getByteTimeDomainData(data);
                var sum = 0;
                for (var i = 0; i < data.length; i++) {
                    var v = (data[i] - 128) / 128;
                    sum += v * v;
                }
                var rms = Math.sqrt(sum / data.length);
                if (rms > state.peak) state.peak = rms;
                for (var b = 0; b < bars.length; b++) {
                    // a little wobble around the instantaneous level so the
                    // meter reads as live rather than as a bar graph
                    var jitter = 0.75 + 0.5 * Math.random();
                    var h = 3 + Math.min(31, rms * 190 * jitter * (1 + b * 0.06));
                    bars[b].style.height = h.toFixed(1) + 'px';
                    bars[b].style.opacity = rms > SILENCE_RMS ? '0.95' : '0.4';
                }
                state.raf = window.requestAnimationFrame(frame);
            })();
        } catch (err) {
            console.debug('[listen] level meter unavailable:', err);
        }
    }

    function stopRecording() {
        var recorder = state.recorder;
        if (!recorder || recorder.state !== 'recording' || state.phase !== 'listening') return;
        window.clearTimeout(state.timer);
        window.clearTimeout(state.countdown);
        state.timer = 0;
        state.countdown = 0;
        state.phase = 'identifying';
        el.overlay.classList.remove('listening');
        el.overlay.classList.add('working');
        setStatus('Identifying…');
        setActions([]);
        try {
            recorder.stop();     // onstop finishes the clip
        } catch (err) {
            console.error('[listen] stop failed:', err);
            teardownCapture();
            fail('The recording could not be stopped. Try again.');
        }
    }

    function finishRecording(recorder, mimeType, op) {
        // The handler is detached during teardown, but a queued stop event may
        // still reach this closure. Only the operation that owns the recorder
        // may turn it into an upload. `listening` is allowed because a track
        // can end on its own (device unplugged); the clip is still valid.
        if (!isCurrent(op) || state.recorder !== recorder) return;
        if (state.phase !== 'identifying' && state.phase !== 'listening') return;

        var type = recorder.mimeType || mimeType || 'audio/webm';
        var blob;
        try {
            blob = new Blob(state.chunks, { type: type });
        } catch (err) {
            teardownCapture();
            fail('The recording could not be read. Try again.');
            return;
        }
        var peak = state.peak;
        teardownCapture();       // stops the tracks, nulls the handlers, hides the meter
        if (!isCurrent(op)) return;
        if (!blob.size) {
            fail('Nothing was recorded. Check the microphone and try again.');
            return;
        }
        if (peak < SILENCE_RMS) {
            showPlainNote('That clip was almost silent — the microphone may be muted, '
                + 'or the volume too low for the recogniser to hear the song.');
        }
        submitBlob(blob, type, op);
    }

    function teardownCapture() {
        window.clearTimeout(state.timer);
        window.clearTimeout(state.countdown);
        state.timer = 0;
        state.countdown = 0;
        if (state.raf) {
            window.cancelAnimationFrame(state.raf);
            state.raf = 0;
        }
        var recorder = state.recorder;
        state.recorder = null;
        if (recorder) {
            // Detach BEFORE stop: cancel and a file takeover must never let a
            // late onstop reach finishRecording.
            recorder.ondataavailable = null;
            recorder.onstop = null;
            recorder.onerror = null;
            if (recorder.state !== 'inactive') {
                try { recorder.stop(); } catch (err) { /* already stopping */ }
            }
        }
        if (state.audioCtx) {
            try { state.audioCtx.close(); } catch (err) { /* already closed */ }
            state.audioCtx = null;
        }
        state.analyser = null;
        if (state.stream) {
            stopTracks(state.stream);
            state.stream = null;
        }
        if (el.overlay) el.overlay.classList.remove('listening');
        state.busy = false;
    }

    /* ── upload, then hand the query to the search page ─────────────────── */

    function extensionFor(type) {
        if (!type) return 'webm';
        if (type.indexOf('mp4') >= 0) return 'm4a';
        if (type.indexOf('ogg') >= 0) return 'ogg';
        return 'webm';
    }

    function abortRequest() {
        if (!state.abort) return;
        try { state.abort.abort(); } catch (err) { /* already aborted */ }
        state.abort = null;
    }

    function submitBlob(blob, type, op) {
        var form = new FormData();
        form.append('audio', blob, 'clip.' + extensionFor(type));
        submitForm(form, op);
    }

    function uploadFile(file) {
        if (!file) return;
        // A file is a complete source on its own: take ownership before the
        // recorder can stop, so its clip is never submitted as well.
        var op = ++state.op;
        showSheet();
        abortRequest();
        abortReadiness();
        teardownCapture();
        reset();
        state.phase = 'identifying';
        state.busy = true;
        state.chunks = [];
        state.peak = 0;
        el.overlay.classList.add('working');
        el.overlay.classList.remove('listening', 'dragging');
        setStatus('Identifying…');
        setActions([]);

        var form = new FormData();
        form.append('audio', file, file.name || 'clip');
        submitForm(form, op);
    }

    function submitForm(form, op) {
        abortRequest();
        var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
        if (ctrl) state.abort = ctrl;
        state.busy = true;
        state.phase = 'identifying';
        el.overlay.classList.remove('listening');
        el.overlay.classList.add('working');
        setStatus('Identifying…');
        setActions([]);

        fetch(ENDPOINT, {
            method: 'POST',
            body: form,
            signal: ctrl ? ctrl.signal : undefined
        }).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (body) {
                if (!res.ok || body.success === false) {
                    throw new Error(body.message || ('The server answered ' + res.status + '.'));
                }
                return body;
            });
        }).then(function (payload) {
            if (!isCurrent(op)) return;
            if (ctrl && state.abort === ctrl) state.abort = null;
            state.busy = false;
            el.overlay.classList.remove('working');
            settled(payload);
        }).catch(function (err) {
            if (!isCurrent(op) || isAbort(err)) return;
            if (ctrl && state.abort === ctrl) state.abort = null;
            state.busy = false;
            el.overlay.classList.remove('working');
            console.error('[listen] identification failed:', err);
            fail(err && err.message ? err.message : 'The identification failed.');
        });
    }

    function fail(message, op) {
        if (op !== undefined && !isCurrent(op)) return;
        state.phase = 'failed';
        state.busy = false;
        el.overlay.classList.remove('listening', 'working', 'dragging');
        setStatus(message, true);
        el.title.textContent = 'No identification';
        var actions = [];
        if (micUsable()) {
            actions.push({
                label: 'Try again',
                primary: true,
                onClick: function () { reset(); startListening(); }
            });
        }
        actions.push({ label: 'Upload a clip', primary: !micUsable(), upload: true, onClick: chooseFile });
        actions.push({ label: 'Close', ghost: true, onClick: close });
        setActions(actions);
    }

    function settled(payload) {
        var ident = payload.identification;
        if (!ident) {
            fail(payload.message || 'The song was not recognised.');
            return;
        }
        var query = payload.query || [ident.artist, ident.title].filter(Boolean).join(' ');
        close();
        toast('Identified: ' + (ident.artist ? ident.artist + ' — ' : '') + ident.title
            + (payload.in_library ? ' (already in your library)' : ''));
        // the song's own analysis box first, the search page only if that fails
        if (!openItemBox(ident, payload.match)) runSearch(query);
    }

    /* The identified song opens in the app's own per-track analysis box — the
       one a search result's Download button opens: title, artist, the track
       listed against your library, and Begin Analysis / Add to Wishlist. It is
       built by the same host function the React search page calls, with the
       same payload shape (enrichSingleTrack in routes/search/-search.actions.ts),
       so both paths render one modal, not two.
       Opening it writes nothing: the process stays `idle` until a button in the
       box is pressed. */
    function openItemBox(ident, match) {
        var open = window.openDownloadMissingModalForArtistAlbum;
        if (typeof open !== 'function') return false;

        var row = match || {};
        var name = row.name || ident.title;
        var artist = row.artist || ident.artist || '';
        if (!name) return false;
        var albumName = row.album || ident.album || name;
        var image = row.image_url
            || (row.images && row.images[0] && (row.images[0].url || row.images[0]))
            || ident.cover_url || '';
        var images = image ? [{ url: image }] : [];
        var trackId = String(row.id || ('shazam_' + (ident.shazam_id || ident.isrc || name)));

        var durationMs = row.duration_ms || null;
        if (!durationMs && typeof ident.duration_s === 'number') durationMs = ident.duration_s * 1000;

        var track = {
            id: trackId,
            name: name,
            source: row.source || null,
            artists: (row.artists && row.artists.length) ? row.artists : (artist ? [artist] : []),
            album: {
                name: albumName,
                id: row.album_id || null,
                album_type: 'single',
                images: images,
                release_date: row.release_date || ident.released || null,
                total_tracks: 1
            },
            duration_ms: durationMs,
            popularity: row.popularity || 0,
            preview_url: null,
            external_urls: null,
            image_url: image
        };
        var album = {
            name: albumName,
            id: null,
            album_type: 'single',
            images: images,
            release_date: track.album.release_date,
            total_tracks: 1,
            artists: [{ name: artist }]
        };

        try {
            // same virtual id the search page uses for this track, so the two
            // paths share one process instead of stacking two boxes
            open('enhanced_search_track_' + trackId,
                 artist ? artist + ' - ' + name : name,
                 [track], album, { id: null, name: artist }, false);
            return true;
        } catch (err) {
            console.debug('[listen] analysis box unavailable:', err);
            return false;
        }
    }

    /* The search page owns its state, so the query goes in through its own seam
       when one is exposed. Otherwise the value is written with the native
       setter — React's tracked setter would swallow it — and an input event
       starts the ordinary debounced search. Either way the result page is the
       one every other search produces, Add to Wishlist included. */
    function runSearch(query) {
        if (!query) return;
        var setNative = function (input) {
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, query);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        };

        if (typeof window._searchPageSetQuery === 'function') {
            try { window._searchPageSetQuery(query); } catch (err) { console.debug('[listen] search seam failed:', err); }
        }

        window.setTimeout(function () {
            var input = document.getElementById('enhanced-search-input');
            if (input) {
                if (input.value.trim() !== query.trim()) setNative(input);
                try { input.focus(); } catch (err) { /* not focusable */ }
                // show the start of the query, not its tail: a phone field is
                // narrower than "artist title" and would otherwise open scrolled.
                // React re-commits the value a moment later and puts the caret
                // back at the end, so park it again after that commit.
                var park = function () {
                    if (!input.isConnected || input.value !== query) return;
                    try { input.setSelectionRange(0, 0); input.scrollLeft = 0; } catch (err) { /* no selection support */ }
                };
                park();
                window.setTimeout(park, 60);
                window.setTimeout(park, 220);
                return;
            }
            if (typeof window._gsNavigateToSearchPage === 'function') {
                window._gsNavigateToSearchPage(query, null);
                return;
            }
            if (typeof window.navigateToPage === 'function') window.navigateToPage('search');
        });
    }

    function toast(message) {
        if (typeof window.showToast === 'function') {
            try { window.showToast(message, 'success'); return; } catch (err) { /* fall through */ }
        }
        console.log('[listen]', message);
    }

    /* ── boot ───────────────────────────────────────────────────────────── */

    if (window.__SOULSYNC_LISTEN_TEST__) {
        window.__soulsyncListenTest = {
            state: state,
            el: el,
            isCurrent: isCurrent,
            open: open,
            close: close,
            reset: reset,
            startListening: startListening,
            stopRecording: stopRecording,
            uploadFile: uploadFile,
            submitForm: submitForm,
            teardownCapture: teardownCapture,
            checkReadiness: checkReadiness,
            finishRecording: finishRecording,
            micUsable: micUsable,
            ensureMic: ensureMic,
        };
    }

    function boot() {
        buildSheet();
        scheduleMic();
        new MutationObserver(scheduleMic).observe(document.body, { childList: true, subtree: true });
        // The Search page is React-rendered: it may mount after this script,
        // re-render over the button, or be replaced on a route change. An
        // observer alone missed a late first mount in testing, so a light poll
        // (one getElementById per pass) keeps the button in the field whatever
        // the page does.
        window.setInterval(function () {
            try { ensureMic(); } catch (err) { console.debug('[listen] mic placement failed:', err); }
        }, 1000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
