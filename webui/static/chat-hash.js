// Synchronous SHA-256 for Arcade commitments.
//
// Why not crypto.subtle: it is async, and the game fold is a pure synchronous
// function over the message stream. Verification has to happen INSIDE the fold
// or clients would disagree about whether a reveal was honest, which is the
// one thing the fold exists to prevent.
//
// Why not a cheap 32-bit hash: a commitment is only worth anything if you
// cannot find a second input that matches it. With a weak hash a player could
// commit to one Battleship board and reveal a different one that happens to
// collide, which is exactly the cheat the commitment is supposed to stop.
// SHA-256 makes that infeasible; a djb2 makes it trivial.
//
// This is NOT here for secrecy. Nothing in SoulSync chat is secret (see
// core/chat_codec.py — "a FORMAT, not a secret"). It is here so that a claim
// made earlier can be checked later.
//
// Tested against the NIST vectors: tests/static/test_chat_hash.mjs

(function () {
    'use strict';

    var K = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
        0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
        0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
        0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
        0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
        0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
        0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
        0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];

    function _rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

    // UTF-8 bytes, so the same string hashes identically everywhere. A naive
    // charCodeAt would give two clients different digests for the same name
    // the moment somebody used an accent or an emoji.
    function _utf8(str) {
        var out = [];
        for (var i = 0; i < str.length; i++) {
            var c = str.charCodeAt(i);
            if (c < 0x80) {
                out.push(c);
            } else if (c < 0x800) {
                out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
            } else if (c >= 0xd800 && c <= 0xdbff && i + 1 < str.length) {
                // surrogate pair -> one code point
                var lo = str.charCodeAt(i + 1);
                if (lo >= 0xdc00 && lo <= 0xdfff) {
                    var cp = 0x10000 + ((c - 0xd800) << 10) + (lo - 0xdc00);
                    out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 63),
                             0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63));
                    i++;
                    continue;
                }
                out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
            } else {
                out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
            }
        }
        return out;
    }

    function sha256(message) {
        var bytes = _utf8(String(message == null ? '' : message));
        var h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

        var bitLen = bytes.length * 8;
        bytes = bytes.slice();
        bytes.push(0x80);
        while (bytes.length % 64 !== 56) bytes.push(0);
        // 64-bit big-endian length. JS bitwise ops are 32-bit, so the high
        // word is computed by division rather than shifting.
        var hi = Math.floor(bitLen / 4294967296);
        var lo = bitLen >>> 0;
        bytes.push((hi >>> 24) & 255, (hi >>> 16) & 255, (hi >>> 8) & 255, hi & 255);
        bytes.push((lo >>> 24) & 255, (lo >>> 16) & 255, (lo >>> 8) & 255, lo & 255);

        var w = new Array(64);
        for (var off = 0; off < bytes.length; off += 64) {
            var i;
            for (i = 0; i < 16; i++) {
                w[i] = (bytes[off + i * 4] << 24) | (bytes[off + i * 4 + 1] << 16) |
                       (bytes[off + i * 4 + 2] << 8) | bytes[off + i * 4 + 3];
            }
            for (i = 16; i < 64; i++) {
                var s0 = _rotr(w[i - 15], 7) ^ _rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
                var s1 = _rotr(w[i - 2], 17) ^ _rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
                w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
            }
            var a = h[0], b = h[1], c = h[2], d = h[3],
                e = h[4], f = h[5], g = h[6], hh = h[7];
            for (i = 0; i < 64; i++) {
                var S1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25);
                var ch = (e & f) ^ (~e & g);
                var t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
                var S0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22);
                var maj = (a & b) ^ (a & c) ^ (b & c);
                var t2 = (S0 + maj) | 0;
                hh = g; g = f; f = e; e = (d + t1) | 0;
                d = c; c = b; b = a; a = (t1 + t2) | 0;
            }
            h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0;
            h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
            h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0;
            h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
        }

        var hex = '';
        for (var j = 0; j < 8; j++) {
            hex += ('00000000' + (h[j] >>> 0).toString(16)).slice(-8);
        }
        return hex;
    }

    // A commitment is hash(secret + payload). The secret is what stops anyone
    // brute-forcing the payload out of the digest: a Battleship board has few
    // enough sensible layouts that a bare hash of one could simply be looked
    // up. Truncated to 32 chars for the wire — still 128 bits, and envelope
    // space is scarce.
    function commit(secret, payload) {
        return sha256(String(secret) + '|' + String(payload)).slice(0, 32);
    }

    function verify(digest, secret, payload) {
        return typeof digest === 'string' && digest === commit(secret, payload);
    }

    // A salt with no cryptographic pretensions — it only has to be unguessable
    // by a person, not by a machine, and it never protects anything valuable.
    function salt() {
        var s = '';
        for (var i = 0; i < 4; i++) {
            s += ('00000000' + Math.floor(Math.random() * 4294967296).toString(16)).slice(-8);
        }
        return s;
    }

    window.ChatHash = { sha256: sha256, commit: commit, verify: verify, salt: salt };
})();
