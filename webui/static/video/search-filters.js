/*
 * SoulSync — manual search result filters and sorting (video side).
 *
 * SeadogsBooty: narrow a 55-release search down to "1080p HEVC MeGusta under
 * 1 GB", save that as a filter, and sort by size, age, quality, indexer,
 * seeders or format score, on every movie, show, season and episode search.
 * Display only: automatic ranking and retries never read any of this.
 *
 * Pure functions on the result rows /api/video/downloads/search returns, so
 * the view and the tests share one implementation. The field and operator
 * names match core/video/search_filters.py, which validates saved filters.
 */
(function (root) {
    'use strict';

    // field -> {label, kind}. kind picks the operators and how values compare.
    var FIELDS = {
        title: { label: 'Title', kind: 'text' },
        resolution: { label: 'Resolution', kind: 'number', hint: '1080' },
        codec: { label: 'Codec', kind: 'text', hint: 'hevc' },
        source: { label: 'Source', kind: 'text', hint: 'web-dl' },
        group: { label: 'Release group', kind: 'text', hint: 'megusta' },
        indexer: { label: 'Indexer / uploader', kind: 'text' },
        size_gb: { label: 'Size (GB)', kind: 'number', hint: '1' },
        age_days: { label: 'Age (days)', kind: 'number', hint: '30' },
        format_score: { label: 'Format score', kind: 'number', hint: '100' },
        seeders: { label: 'Seeders', kind: 'number', hint: '10' },
        grabs: { label: 'Grabs', kind: 'number' },
        hdr: { label: 'HDR', kind: 'text', hint: 'dv' },
        audio: { label: 'Audio', kind: 'text', hint: 'atmos' },
        meets_profile: { label: 'Meets profile', kind: 'bool' }
    };

    var OPS = {
        contains: { label: 'contains', kinds: ['text'] },
        not_contains: { label: "doesn't contain", kinds: ['text'] },
        equals: { label: 'is', kinds: ['text', 'number', 'bool'] },
        not_equals: { label: 'is not', kinds: ['text', 'number', 'bool'] },
        gt: { label: 'more than', kinds: ['number'] },
        lt: { label: 'less than', kinds: ['number'] }
    };

    // sort key -> label. 'rank' is the server's own order (the profile's ranking).
    var SORTS = {
        rank: 'Best match',
        size_gb: 'Size',
        age_days: 'Age',
        resolution: 'Quality',
        format_score: 'Format score',
        seeders: 'Seeders',
        indexer: 'Indexer',
        grabs: 'Grabs'
    };

    // a codec goes by several names: x265, h.265 and hevc are one codec
    var CODEC_ALIASES = { x265: 'hevc', h265: 'hevc', 'h.265': 'hevc', x264: 'h264', 'h.264': 'h264', avc: 'h264' };

    function _codec(v) {
        var s = String(v || '').toLowerCase().replace(/\s+/g, '');
        return CODEC_ALIASES[s] || s;
    }

    function _num(v) {
        if (v === null || v === undefined || v === '') return null;
        var n = parseFloat(String(v).replace(/[^0-9.\-]/g, ''));
        return isNaN(n) ? null : n;
    }

    function ageDays(row, now) {
        var t = row && row.published_at ? Date.parse(row.published_at) : NaN;
        if (isNaN(t)) return null;
        return Math.max(0, ((now || Date.now()) - t) / 86400000);
    }

    // The value a filter or sort reads from one row.
    function value(row, field, now) {
        row = row || {};
        switch (field) {
            case 'title': return String(row.title || row.filename || '');
            case 'resolution': return _num(row.resolution);
            case 'codec': return _codec(row.codec);
            case 'source': return String(row.source || '');
            case 'group': return String(row.group || '');
            case 'indexer': return String(row.username || '');
            case 'size_gb': {
                var gb = _num(row.size_gb);
                return gb !== null ? gb : (row.size_bytes ? row.size_bytes / 1e9 : null);
            }
            case 'age_days': return ageDays(row, now);
            case 'format_score': return _num(row.format_score) || 0;
            case 'seeders': return _num(row.seeders);
            case 'grabs': return _num(row.grabs);
            case 'hdr': return String(row.hdr || '');
            case 'audio': return String(row.audio || '');
            case 'meets_profile': return !!row.accepted;
            default: return null;
        }
    }

    function _truthy(v) {
        return /^(1|true|yes|y|on)$/i.test(String(v == null ? '' : v).trim());
    }

    function conditionMatches(row, cond, now) {
        if (!cond || !FIELDS[cond.field] || !OPS[cond.op]) return true;   // half-built rows don't filter
        var kind = FIELDS[cond.field].kind;
        var got = value(row, cond.field, now);
        var want = cond.value;
        if (kind === 'bool') {
            var b = _truthy(want);
            return cond.op === 'not_equals' ? got !== b : got === b;
        }
        if (kind === 'number') {
            var w = _num(want);
            if (w === null) return true;            // nothing typed yet
            if (got === null) return false;         // the row doesn't know: not a match
            if (cond.op === 'gt') return got > w;
            if (cond.op === 'lt') return got < w;
            if (cond.op === 'not_equals') return got !== w;
            return got === w;
        }
        var text = String(want == null ? '' : want).trim().toLowerCase();
        if (!text) return true;
        if (cond.field === 'codec') text = _codec(text);
        var hay = String(got || '').toLowerCase();
        if (cond.op === 'contains') return hay.indexOf(text) !== -1;
        if (cond.op === 'not_contains') return hay.indexOf(text) === -1;
        if (cond.op === 'not_equals') return hay !== text;
        return hay === text;
    }

    // filter = {match: 'all'|'any', conditions: [{field, op, value}]}
    function matches(row, filter, now) {
        var conds = (filter && filter.conditions) || [];
        if (!conds.length) return true;
        if (filter.match === 'any') {
            return conds.some(function (c) { return conditionMatches(row, c, now); });
        }
        return conds.every(function (c) { return conditionMatches(row, c, now); });
    }

    // the quick box: every word must appear in the title, indexer or group
    function quickMatches(row, text) {
        var words = String(text || '').toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.length) return true;
        var hay = [row && row.title, row && row.filename, row && row.username, row && row.group,
            row && row.codec, row && row.resolution].join(' ').toLowerCase();
        return words.every(function (w) { return hay.indexOf(w) !== -1; });
    }

    // [{row, index}] in display order. 'rank' keeps the server's order; anything
    // else sorts by that field, rows that don't know it last, the server's
    // order breaking ties so equal rows never shuffle between polls.
    function sortRows(items, key, dir, now) {
        var out = items.slice();
        if (!key || key === 'rank') {
            if (dir === 'asc') out.reverse();
            return out;
        }
        var sign = dir === 'asc' ? 1 : -1;
        out.sort(function (a, b) {
            var va = value(a.row, key, now), vb = value(b.row, key, now);
            var na = va === null || va === '', nb = vb === null || vb === '';
            if (na !== nb) return na ? 1 : -1;
            if (!na && va !== vb) {
                if (typeof va === 'string' || typeof vb === 'string') {
                    return sign * String(va).localeCompare(String(vb), undefined, { sensitivity: 'base' });
                }
                return sign * (va < vb ? -1 : 1);
            }
            return a.index - b.index;
        });
        return out;
    }

    // rows -> the [{row, index}] the panel shows: quick text, then the filter, then the sort
    function view(rows, state, now) {
        state = state || {};
        var items = [];
        (rows || []).forEach(function (row, index) {
            if (!row) return;
            if (!quickMatches(row, state.text)) return;
            if (!matches(row, state.filter, now)) return;
            items.push({ row: row, index: index });
        });
        return sortRows(items, state.sort, state.dir, now);
    }

    function opsFor(field) {
        var kind = (FIELDS[field] || {}).kind;
        return Object.keys(OPS).filter(function (op) { return OPS[op].kinds.indexOf(kind) !== -1; });
    }

    root.VideoSearchFilters = {
        FIELDS: FIELDS, OPS: OPS, SORTS: SORTS,
        value: value, conditionMatches: conditionMatches, matches: matches,
        quickMatches: quickMatches, sortRows: sortRows, view: view, opsFor: opsFor
    };
})(typeof window !== 'undefined' ? window : this);
