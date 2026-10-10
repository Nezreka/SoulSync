/*
 * SoulSync — Subtitle settings UI (Phase 3: per-title overrides + manual search).
 *
 * Self-contained IIFE, no globals except window.VideoSubtitles, event-delegated,
 * no inline handlers. Talks only to /api/video/subtitles/*.
 *
 * Two surfaces:
 *  1. mountOverrides(kind, itemId, source) — the "Subtitles" config section on
 *     movie/show detail pages: effective languages + an "Override"/"Global"
 *     badge, a language list (same comma style as video-settings.js), Save
 *     (PUT) and "Reset to global defaults" (DELETE).
 *  2. openManualSearch(kind, itemId, title, label, opts) — the Bazarr-style
 *     manual-search modal: language picker, scored candidate table, per-row
 *     Download. A manually downloaded subtitle is the user's explicit pick and
 *     is protected from auto-upgrade — the copy says so.
 */
(function () {
  'use strict';

  var OVERRIDES_URL = '/api/video/subtitles/overrides/';
  var SEARCH_URL = '/api/video/subtitles/search/';
  var DOWNLOAD_URL = '/api/video/subtitles/manual-download';

  var LANG_NAMES = {
    en: 'English',
    es: 'Spanish',
    fr: 'French',
    de: 'German',
    it: 'Italian',
    pt: 'Portuguese',
    'pt-br': 'Portuguese (BR)',
    nl: 'Dutch',
    pl: 'Polish',
    ru: 'Russian',
    ja: 'Japanese',
    ko: 'Korean',
    zh: 'Chinese',
    'zh-cn': 'Chinese (Simplified)',
    'zh-tw': 'Chinese (Traditional)',
    ar: 'Arabic',
    tr: 'Turkish',
    sv: 'Swedish',
    da: 'Danish',
    fi: 'Finnish',
    no: 'Norwegian',
    cs: 'Czech',
    el: 'Greek',
    he: 'Hebrew',
    hi: 'Hindi',
    hu: 'Hungarian',
    ro: 'Romanian',
    th: 'Thai',
    uk: 'Ukrainian',
    vi: 'Vietnamese',
    id: 'Indonesian',
    ms: 'Malay',
    ca: 'Catalan',
  };
  var PROVIDER_NAMES = { opensubtitles: 'OpenSubtitles' };

  // The override fetch for the title on screen; reset()/mount() bump it so a
  // slow response from the previous title can never paint into the new one.
  var _seq = 0;
  var _mountedKey = null;
  // key ("movie:123") -> { languages, source, global_languages }
  var _cache = {};

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function toast(msg, type) {
    if (typeof showToast === 'function') showToast(msg, type);
  }
  function langLabel(code) {
    var c = String(code || '').toLowerCase();
    return LANG_NAMES[c] || String(code || '').toUpperCase();
  }
  function providerLabel(id) {
    return PROVIDER_NAMES[id] || (id ? id.charAt(0).toUpperCase() + id.slice(1) : '—');
  }
  function keyOf(kind, itemId) {
    return kind + ':' + itemId;
  }
  // Language codes, same rule as the settings page (video-settings.js): 2-3
  // letters, optional region subtag (pt-br), separated by commas/spaces/semicolons.
  function parseLangs(raw) {
    var out = [];
    String(raw || '')
      .split(/[,;\s]+/)
      .forEach(function (p) {
        var c = p.trim().toLowerCase();
        if (/^[a-z]{2,3}(-[a-z]{2})?$/.test(c) && out.indexOf(c) === -1) out.push(c);
      });
    return out;
  }

  var _styled = false;
  function ensureStyles() {
    if (_styled) return;
    _styled = true;
    var css =
      '.vd-subcfg-section{margin:0 0 26px;}' +
      '.vsub-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);' +
      'border-radius:14px;padding:18px 20px;max-width:720px;}' +
      '.vsub-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;}' +
      '.vsub-badge{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;' +
      'padding:4px 10px;border-radius:999px;flex:none;}' +
      '.vsub-badge--override{background:rgba(88,101,242,.18);color:#a5b0ff;border:1px solid rgba(88,101,242,.45);}' +
      '.vsub-badge--global{background:rgba(255,255,255,.06);color:rgba(255,255,255,.6);border:1px solid rgba(255,255,255,.12);}' +
      '.vsub-chips{display:flex;flex-wrap:wrap;gap:8px;}' +
      '.vsub-none{font-size:13px;opacity:.5;}' +
      '.vsub-hint{font-size:12.5px;opacity:.6;line-height:1.55;margin:10px 0 14px;max-width:64ch;}' +
      '.vsub-label{display:block;font-size:13px;font-weight:600;margin-bottom:8px;}' +
      '.vsub-input{width:min(340px,100%);padding:10px 14px;font-size:14px;color:inherit;box-sizing:border-box;' +
      'background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);border-radius:10px;outline:none;}' +
      '.vsub-input:focus{border-color:rgba(255,255,255,.35);}' +
      '.vsub-btns{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;}' +
      '.vsub-btn{padding:9px 18px;font-size:14px;border-radius:10px;cursor:pointer;border:1px solid rgba(255,255,255,.14);' +
      'background:rgba(255,255,255,.06);color:inherit;font-weight:600;font-family:inherit;transition:background .15s ease;}' +
      '.vsub-btn:hover{background:rgba(255,255,255,.12);}' +
      '.vsub-btn:disabled{opacity:.5;cursor:default;}' +
      '.vsub-btn--primary{background:#5865f2;border-color:#5865f2;color:#fff;}' +
      '.vsub-btn--primary:hover{background:#4752c4;}' +
      '.vsub-btn--small{padding:7px 12px;font-size:13px;}' +
      '.vsub-err{margin-top:12px;font-size:13px;color:#ff6b6b;line-height:1.5;}' +
      '.vsub-loading{display:flex;align-items:center;gap:10px;font-size:13px;opacity:.6;padding:10px 0;}' +
      '.vsub-spin{width:14px;height:14px;border-radius:50%;border:2px solid rgba(255,255,255,.25);' +
      'border-top-color:#fff;animation:vsub-rot .7s linear infinite;display:inline-block;flex:none;}' +
      '@keyframes vsub-rot{to{transform:rotate(360deg);}}' +
      // Manual-search modal.
      '.vsub-overlay{position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;' +
      'background:rgba(4,4,8,.72);backdrop-filter:blur(6px);animation:vsub-fade .18s ease;}' +
      '@keyframes vsub-fade{from{opacity:0;}to{opacity:1;}}' +
      '.vsub-modal{width:min(780px,94vw);max-height:86vh;display:flex;flex-direction:column;overflow:hidden;' +
      'background:#14141c;border:1px solid rgba(255,255,255,.1);border-radius:16px;' +
      'box-shadow:0 24px 80px rgba(0,0,0,.6);}' +
      '.vsub-mhead{display:flex;align-items:flex-start;gap:12px;padding:18px 18px 0;}' +
      '.vsub-mtitle{font-size:17px;font-weight:700;}' +
      '.vsub-msub{font-size:13px;opacity:.75;margin-top:4px;}' +
      '.vsub-mlabel{opacity:.55;}' +
      '.vsub-mnote{font-size:12px;opacity:.5;margin-top:6px;line-height:1.5;}' +
      '.vsub-x{margin-left:auto;background:none;border:0;color:inherit;font-size:16px;cursor:pointer;opacity:.6;padding:4px;flex:none;}' +
      '.vsub-x:hover{opacity:1;}' +
      '.vsub-mbar{display:flex;gap:10px;padding:14px 18px 0;}' +
      '.vsub-select{flex:1;min-width:0;padding:10px 12px;font-size:14px;color:inherit;font-family:inherit;' +
      'background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);border-radius:10px;outline:none;}' +
      '.vsub-select option{background:#14141c;}' +
      '.vsub-merr{margin:12px 18px 0;font-size:13px;color:#ff6b6b;line-height:1.5;}' +
      '.vsub-mok{margin:12px 18px 0;font-size:13px;color:#4ade80;line-height:1.5;' +
      'background:rgba(74,222,128,.08);border:1px solid rgba(74,222,128,.25);border-radius:10px;padding:10px 12px;}' +
      '.vsub-results{overflow-y:auto;padding:14px 18px 18px;min-height:120px;}' +
      '.vsub-rhead,.vsub-row{display:grid;grid-template-columns:minmax(0,1.5fr) 110px 64px 92px 108px;gap:10px;align-items:center;}' +
      '.vsub-rhead{font-size:11px;text-transform:uppercase;letter-spacing:.06em;opacity:.45;padding:0 10px 8px;font-weight:700;}' +
      '.vsub-row{padding:10px;border-radius:10px;border:1px solid transparent;}' +
      '.vsub-row:hover{background:rgba(255,255,255,.04);}' +
      '.vsub-row--done{border-color:rgba(74,222,128,.3);background:rgba(74,222,128,.06);}' +
      '.vsub-rtitle{font-size:13.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
      '.vsub-tag{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.04em;padding:2px 6px;' +
      'border-radius:5px;background:rgba(255,255,255,.08);opacity:.7;margin-left:6px;vertical-align:1px;}' +
      '.vsub-hash{display:inline-block;font-size:10px;font-weight:700;padding:2px 6px;border-radius:5px;' +
      'background:rgba(74,222,128,.14);color:#4ade80;margin-left:6px;vertical-align:1px;}' +
      '.vsub-rprov{font-size:12px;opacity:.65;}' +
      '.vsub-rscore{font-size:13px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums;}' +
      '.vsub-rdl{font-size:12px;opacity:.65;text-align:right;font-variant-numeric:tabular-nums;}' +
      '.vsub-raction{text-align:right;}' +
      '.vsub-saved{font-size:13px;color:#4ade80;font-weight:700;}' +
      '.vsub-hint--center{text-align:center;padding:26px 10px;opacity:.5;font-size:13px;}' +
      '@media (max-width:600px){' +
      '.vsub-rhead{display:none;}' +
      '.vsub-rhead,.vsub-row{grid-template-columns:minmax(0,1fr) 70px 100px;}' +
      '.vsub-rprov,.vsub-rdl{display:none;}}';
    var st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ── Per-title language overrides ─────────────────────────────────────────

  function _hosts() {
    return {
      movie: {
        sec: document.querySelector('[data-video-detail="movie"] [data-vd-subcfg-section]'),
        host: document.querySelector('[data-video-detail="movie"] [data-vd-subcfg]'),
      },
      show: {
        sec: document.querySelector('[data-video-detail="show"] [data-vd-subcfg-section]'),
        host: document.querySelector('[data-video-detail="show"] [data-vd-subcfg]'),
      },
    };
  }

  // Hides the section on both pages and invalidates in-flight override
  // fetches. Called when a new detail page starts loading.
  function reset() {
    _seq++;
    _mountedKey = null;
    _mountedKind = null;
    _mountedItemId = null;
    closeModal();
    var h = _hosts();
    Object.keys(h).forEach(function (k) {
      if (h[k].sec) h[k].sec.hidden = true;
      if (h[k].host) h[k].host.innerHTML = '';
    });
  }

  function effectiveFirstLang(kind, itemId) {
    var s = _cache[keyOf(kind, itemId)];
    return (s && s.languages && s.languages[0]) || 'en';
  }

  // Renders the "Subtitles" section for a movie/show detail page. Overrides
  // are keyed by library item id, so TMDB previews and non-library sources
  // never show the section.
  function mountOverrides(kind, itemId, source) {
    ensureStyles();
    var mySeq = ++_seq;
    var h = _hosts();
    Object.keys(h).forEach(function (k) {
      if (h[k].sec) h[k].sec.hidden = true;
      if (h[k].host) h[k].host.innerHTML = '';
    });
    var valid = (kind === 'movie' || kind === 'show') && source === 'library' && itemId != null;
    if (!valid) {
      _mountedKey = null;
      _mountedKind = null;
      _mountedItemId = null;
      return;
    }
    var key = keyOf(kind, itemId);
    _mountedKey = key;
    _mountedKind = kind;
    _mountedItemId = itemId;
    var parts = h[kind];
    if (!parts || !parts.host) return;
    parts.sec.hidden = false;
    if (_cache[key]) {
      renderOverrides(kind, _cache[key], null);
      return;
    }
    parts.host.innerHTML =
      '<div class="vsub-card"><div class="vsub-loading"><span class="vsub-spin"></span>' +
      'Loading subtitle settings\u2026</div></div>';
    fetch(OVERRIDES_URL + kind + '/' + itemId, { headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw { status: r.status };
        return r.json();
      })
      .then(function (body) {
        if (mySeq !== _seq || _mountedKey !== key) return;
        var langs = (body && body.languages) || [];
        if (!langs.length) langs = (body && body.global_languages) || [];
        _cache[key] = {
          languages: langs.slice(),
          source: (body && body.source) || 'global',
          global_languages: ((body && body.global_languages) || []).slice(),
        };
        renderOverrides(kind, _cache[key], null);
      })
      .catch(function (err) {
        if (mySeq !== _seq || _mountedKey !== key) return;
        var msg =
          err && err.status === 404
            ? 'Subtitle settings are not available yet on this server.'
            : 'Could not reach the subtitle settings service — try again in a bit.';
        renderOverrides(kind, null, msg);
      });
  }

  function renderOverrides(kind, state, loadErr) {
    var host = _hosts()[kind].host;
    if (!host) return;
    if (loadErr) {
      host.innerHTML =
        '<div class="vsub-card"><div class="vsub-err" role="alert">' +
        esc(loadErr) +
        '</div></div>';
      return;
    }
    var noun = kind === 'movie' ? 'movie' : 'show';
    var isOverride = state.source === 'override';
    var langs = state.languages;
    var chips = langs
      .map(function (c) {
        return (
          '<span class="vd-sub" title="' + esc(langLabel(c)) + '">' + esc(langLabel(c)) + '</span>'
        );
      })
      .join('');
    var hint = isOverride
      ? 'This ' +
        noun +
        ' uses its <b>own</b> language list. Resetting hands it back to your global settings.'
      : 'Using your global subtitle settings' +
        (langs.length ? ' (<b>' + esc(langs.join(', ')) + '</b>)' : '') +
        '. Save a list below to override just this ' +
        noun +
        '.';
    host.innerHTML =
      '<div class="vsub-card">' +
      '<div class="vsub-head">' +
      '<span class="vsub-badge ' +
      (isOverride ? 'vsub-badge--override' : 'vsub-badge--global') +
      '">' +
      (isOverride ? 'Override' : 'Global') +
      '</span>' +
      '<div class="vsub-chips">' +
      (chips || '<span class="vsub-none">no languages set</span>') +
      '</div>' +
      '</div>' +
      '<p class="vsub-hint">' +
      hint +
      '</p>' +
      '<label class="vsub-label" for="vsub-langs-' +
      kind +
      '">Subtitle languages for this ' +
      noun +
      '</label>' +
      '<input class="vsub-input" id="vsub-langs-' +
      kind +
      '" type="text" placeholder="en, es" ' +
      'value="' +
      esc(langs.join(', ')) +
      '" autocomplete="off" spellcheck="false">' +
      '<div class="vsub-btns">' +
      '<button class="vsub-btn vsub-btn--primary" type="button" data-vsub="save">Save</button>' +
      (isOverride
        ? '<button class="vsub-btn" type="button" data-vsub="reset">Reset to global defaults</button>'
        : '') +
      '</div>' +
      '<div class="vsub-err" data-vsub-err role="alert" hidden></div>' +
      '</div>';
    host.querySelector('[data-vsub="save"]').addEventListener('click', function () {
      saveOverrides(kind, currentMountItemId(kind), state);
    });
    var resetBtn = host.querySelector('[data-vsub="reset"]');
    if (resetBtn)
      resetBtn.addEventListener('click', function () {
        resetOverrides(kind, state);
      });
  }

  var _mountedKind = null;
  var _mountedItemId = null;

  // The mount key embeds the item id ("movie:123"); these recover the exact
  // value (preserving its type) for the save/reset round-trips.
  function currentMountItemId(kind) {
    return _mountedKind === kind ? _mountedItemId : null;
  }

  function showSectionErr(kind, msg) {
    var host = _hosts()[kind].host;
    if (!host) return;
    var err = host.querySelector('[data-vsub-err]');
    if (!err) return;
    err.hidden = false;
    err.textContent = msg;
  }

  function saveOverrides(kind, itemId, state) {
    var host = _hosts()[kind].host;
    if (!host) return;
    var input = host.querySelector('.vsub-input');
    var btn = host.querySelector('[data-vsub="save"]');
    var langs = parseLangs(input ? input.value : '');
    if (!langs.length) {
      showSectionErr(
        kind,
        'Enter at least one language code (e.g. en, es) — or reset to global defaults.',
      );
      return;
    }
    btn.disabled = true;
    var orig = btn.textContent;
    btn.textContent = 'Saving\u2026';
    // Stale-paint guard (mirrors mountOverrides): the response must only
    // render while this title is still mounted — a mid-save navigation
    // resets _mountedKey, so a late response can't paint the wrong title's
    // state into the shared host.
    var key = keyOf(kind, itemId);
    fetch(OVERRIDES_URL + kind + '/' + itemId, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ languages: langs }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(function (body) {
        if (!body || body.ok !== true) throw new Error('bad response');
        if (_mountedKey !== key) return;
        var saved = body.languages && body.languages.length ? body.languages : langs;
        _cache[key] = {
          languages: saved.slice(),
          source: 'override',
          global_languages: state.global_languages,
        };
        renderOverrides(kind, _cache[key], null);
        toast('Subtitle languages saved for this title.');
      })
      .catch(function () {
        if (_mountedKey !== key) return;
        btn.disabled = false;
        btn.textContent = orig;
        showSectionErr(
          kind,
          'Could not save — the subtitle settings service is unavailable. Try again in a bit.',
        );
      });
  }

  function resetOverrides(kind, state) {
    var host = _hosts()[kind].host;
    if (!host) return;
    var btn = host.querySelector('[data-vsub="reset"]');
    btn.disabled = true;
    var itemId = currentMountItemId(kind);
    // Stale-paint guard (mirrors mountOverrides): see saveOverrides.
    var key = keyOf(kind, itemId);
    fetch(OVERRIDES_URL + kind + '/' + itemId, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    })
      .then(function (r) {
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(function (body) {
        if (!body || body.ok !== true) throw new Error('bad response');
        if (_mountedKey !== key) return;
        _cache[key] = {
          languages: state.global_languages.slice(),
          source: 'global',
          global_languages: state.global_languages,
        };
        renderOverrides(kind, _cache[key], null);
        toast('This title is back on your global subtitle settings.');
      })
      .catch(function () {
        if (_mountedKey !== key) return;
        btn.disabled = false;
        showSectionErr(
          kind,
          'Could not reset — the subtitle settings service is unavailable. Try again in a bit.',
        );
      });
  }

  // ── Manual subtitle search ───────────────────────────────────────────────

  function closeModal() {
    var ov = document.querySelector('.vsub-overlay');
    if (ov) ov.remove();
    document.removeEventListener('keydown', _modalKey);
  }
  function _modalKey(e) {
    if (e.key === 'Escape') closeModal();
  }

  function langOptions(selected) {
    var codes = Object.keys(LANG_NAMES).sort(function (a, b) {
      return LANG_NAMES[a].localeCompare(LANG_NAMES[b]);
    });
    return codes
      .map(function (c) {
        return (
          '<option value="' +
          esc(c) +
          '"' +
          (c === selected ? ' selected' : '') +
          '>' +
          esc(LANG_NAMES[c]) +
          '</option>'
        );
      })
      .join('');
  }

  function resultRow(c) {
    var tags = '';
    if (c.hash_match) {
      tags +=
        '<span class="vsub-hash" title="This file matches your video\u2019s hash">\u2713 hash match</span>';
    }
    if (c.hi) tags += '<span class="vsub-tag" title="Hearing-impaired captions">HI</span>';
    if (c.forced)
      tags += '<span class="vsub-tag" title="Forced (foreign-parts only) subtitles">Forced</span>';
    var score = c.score == null ? '\u2014' : String(c.score);
    var dls =
      c.download_count == null ? '\u2014' : Number(c.download_count).toLocaleString('en-US');
    return (
      '<div class="vsub-row" data-vsub-cid="' +
      esc(c.candidate_id) +
      '">' +
      '<div class="vsub-rtitle" title="' +
      esc(c.title || '') +
      '">' +
      esc(c.title || '\u2014') +
      tags +
      '</div>' +
      '<div class="vsub-rprov">' +
      esc(providerLabel(c.provider)) +
      '</div>' +
      '<div class="vsub-rscore">' +
      esc(score) +
      '</div>' +
      '<div class="vsub-rdl">' +
      esc(dls) +
      '</div>' +
      '<div class="vsub-raction">' +
      '<button class="vsub-btn vsub-btn--small" type="button" data-vsub-m="dl">Download</button>' +
      '</div>' +
      '</div>'
    );
  }

  // The Bazarr-style manual search: pick a language, search, pick a result.
  // A manually downloaded subtitle is the user's explicit pick — the UI says
  // so, and the backend protects manual picks from auto-upgrade.
  function openManualSearch(kind, itemId, title, label, opts) {
    if (kind !== 'movie' && kind !== 'episode') return;
    opts = opts || {};
    ensureStyles();
    closeModal();
    var defaultLang = (opts.defaultLang || 'en').toLowerCase();
    if (!LANG_NAMES[defaultLang]) defaultLang = 'en';
    var ov = document.createElement('div');
    ov.className = 'vsub-overlay';
    ov.innerHTML =
      '<div class="vsub-modal" role="dialog" aria-modal="true" aria-label="Search subtitles">' +
      '<div class="vsub-mhead"><div>' +
      '<div class="vsub-mtitle">Search subtitles</div>' +
      '<div class="vsub-msub">' +
      esc(title || '') +
      (label ? ' <span class="vsub-mlabel">' + esc(label) + '</span>' : '') +
      '</div>' +
      '<div class="vsub-mnote">Pick one yourself \u2014 your pick won\u2019t be auto-replaced by upgrades.</div>' +
      '</div><button class="vsub-x" type="button" data-vsub-m="close" aria-label="Close">\u2715</button></div>' +
      '<div class="vsub-mbar">' +
      '<select class="vsub-select" data-vsub-m="lang" aria-label="Subtitle language">' +
      langOptions(defaultLang) +
      '</select>' +
      '<button class="vsub-btn vsub-btn--primary" type="button" data-vsub-m="search">Search</button>' +
      '</div>' +
      '<div class="vsub-merr" data-vsub-m="err" role="alert" hidden></div>' +
      '<div class="vsub-mok" data-vsub-m="ok" hidden></div>' +
      '<div class="vsub-results" data-vsub-m="results"></div>' +
      '</div>';
    document.body.appendChild(ov);
    document.addEventListener('keydown', _modalKey);

    var searchBtn = ov.querySelector('[data-vsub-m="search"]');
    var box = ov.querySelector('[data-vsub-m="results"]');
    var errEl = ov.querySelector('[data-vsub-m="err"]');
    var okEl = ov.querySelector('[data-vsub-m="ok"]');
    var langSel = ov.querySelector('[data-vsub-m="lang"]');

    function showErr(msg) {
      errEl.hidden = false;
      errEl.textContent = msg;
    }
    function hideErr() {
      errEl.hidden = true;
      errEl.textContent = '';
    }
    function hideOk() {
      okEl.hidden = true;
      okEl.innerHTML = '';
    }

    function doSearch() {
      if (!document.body.contains(ov)) return;
      var lang = langSel.value;
      hideErr();
      hideOk();
      searchBtn.disabled = true;
      var orig = searchBtn.textContent;
      searchBtn.textContent = 'Searching\u2026';
      box.innerHTML =
        '<div class="vsub-hint vsub-hint--center"><span class="vsub-spin"></span> ' +
        'Searching for ' +
        esc(langLabel(lang)) +
        ' subtitles\u2026</div>';
      fetch(SEARCH_URL + kind + '/' + itemId + '?lang=' + encodeURIComponent(lang), {
        headers: { Accept: 'application/json' },
      })
        .then(function (r) {
          if (!r.ok) throw { status: r.status };
          return r.json();
        })
        .then(function (body) {
          if (!document.body.contains(ov)) return;
          var cands = (body && body.candidates) || [];
          if (!cands.length) {
            box.innerHTML =
              '<div class="vsub-hint vsub-hint--center">No subtitles found for ' +
              esc(langLabel(lang)) +
              ' \u2014 try another language.</div>';
            return;
          }
          box.innerHTML =
            '<div class="vsub-rhead"><span>Title</span><span>Provider</span>' +
            '<span style="text-align:right">Score</span><span style="text-align:right">Downloads</span><span></span></div>' +
            cands.map(resultRow).join('');
        })
        .catch(function (err) {
          if (!document.body.contains(ov)) return;
          box.innerHTML = '';
          showErr(
            err && err.status === 404
              ? 'Subtitle search is not available yet on this server.'
              : 'Subtitle search failed \u2014 try again.',
          );
        })
        .then(function () {
          if (!document.body.contains(ov)) return;
          searchBtn.disabled = false;
          searchBtn.textContent = orig;
        });
    }

    function doDownload(btn) {
      if (!document.body.contains(ov)) return;
      var row = btn.closest('.vsub-row');
      var cid = row ? row.getAttribute('data-vsub-cid') : null;
      if (!cid) return;
      hideErr();
      btn.disabled = true;
      btn.textContent = 'Downloading\u2026';
      fetch(DOWNLOAD_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          kind: kind,
          item_id: itemId,
          lang: langSel.value,
          candidate_id: cid,
        }),
      })
        .then(function (r) {
          if (!r.ok) throw { status: r.status };
          return r.json();
        })
        .then(function (body) {
          if (!document.body.contains(ov)) return;
          if (!body || body.ok !== true) throw { status: 0 };
          var name =
            String(body.path || '')
              .split('/')
              .pop() || 'subtitle file';
          row.classList.add('vsub-row--done');
          var saved = document.createElement('span');
          saved.className = 'vsub-saved';
          saved.textContent = '\u2713 Saved';
          btn.replaceWith(saved);
          okEl.hidden = false;
          okEl.innerHTML = 'Saved <b>' + esc(name) + '</b>. This pick won\u2019t be auto-replaced.';
          toast('Subtitle saved: ' + name);
        })
        .catch(function (err) {
          if (!document.body.contains(ov)) return;
          btn.disabled = false;
          btn.textContent = 'Download';
          showErr(
            err && err.status === 404
              ? 'That search expired \u2014 search again to get a fresh result.'
              : 'Download failed \u2014 try again or pick another result.',
          );
        });
    }

    ov.addEventListener('click', function (e) {
      if (e.target === ov) {
        closeModal();
        return;
      }
      var t = e.target.closest('[data-vsub-m]');
      if (!t) return;
      var act = t.getAttribute('data-vsub-m');
      if (act === 'close') closeModal();
      else if (act === 'search') doSearch();
      else if (act === 'dl') doDownload(t);
    });

    doSearch(); // search immediately with the effective language
  }

  window.VideoSubtitles = {
    mountOverrides: mountOverrides,
    reset: reset,
    openManualSearch: openManualSearch,
    effectiveFirstLang: effectiveFirstLang,
  };
})();
