// ═══════════════════════════════════════════════════════════════════
// SoulSync Docs — modern documentation engine
// ───────────────────────────────────────────────────────────────────
// Content lives in webui/static/docs-content/*.js, each calling
//   registerDocsSection({ id, title, icon, pages: [{ id, title, lede, body }] })
// Body is Markdown (subset). This file renders nav, article, TOC,
// search palette, prev/next, deep links and scroll-spy.
//
// Integration contract (do not break):
//   - initializeDocsPage()   — called by init.js when #help-page shows
//   - navigateToDocsSection(id) — global; used by downloads.js "Learn more →"
//   - DOM: #docs-nav, #docs-content, .docs-nav-section[-title|-child|-children]
//     with data-target / data-parent attrs — used by helper.js tours
// ═══════════════════════════════════════════════════════════════════

(function () {
'use strict';

/* ── Registry ─────────────────────────────────────────────────── */
const _sections = [];
const _pageIndex = new Map();   // pageId -> { section, page }
const _flatPages = [];          // [{ section, page }] in nav order

function registerDocsSection(section) {
    if (!section || !section.id || !Array.isArray(section.pages)) return;
    _sections.push(section);
    section.pages.forEach((page) => {
        _pageIndex.set(page.id, { section, page });
        _flatPages.push({ section, page });
        // Section landing id also resolves (compat with old anchors)
        if (!_pageIndex.has(section.id)) _pageIndex.set(section.id, { section, page: section.pages[0] });
    });
}
// Expose globally for docs-content/*.js
window.registerDocsSection = registerDocsSection;

function findEntry(id) {
    if (_pageIndex.has(id)) return _pageIndex.get(id);
    return null;
}

/* ── Markdown renderer (subset) ───────────────────────────────── */
function escHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function slugify(s) {
    return String(s).toLowerCase()
        .replace(/<[^>]*>/g, '')
        .replace(/[^\w\s-]/g, '')
        .trim().replace(/[\s_]+/g, '-')
        .replace(/-+/g, '-');
}

function renderInline(s) {
    // s is already HTML-escaped by the caller contract below.
    // Order: images, links, code, bold, italic.
    let out = s;
    // images ![alt](src)
    out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
        (m, alt, src) => `[[DDIMG:${alt}|||${src}]]`);
    // links [text](url)
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (m, text, url) => {
        if (url.startsWith('#') && !url.startsWith('#/')) {
            const pid = url.slice(1);
            return `<a data-dd-link="${escHtml(pid)}">${text}</a>`;
        }
        const safe = escHtml(url);
        const ext = /^(https?:)?\/\//.test(url) || url.startsWith('mailto:');
        return `<a href="${safe}"${ext ? ' target="_blank" rel="noopener"' : ''}>${text}</a>`;
    });
    // inline code `code`
    out = out.replace(/`([^`\n]+)`/g, (m, code) => `<code>${code}</code>`);
    // bold **x**
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // italic *x*
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    // restore images
    out = out.replace(/\[\[DDIMG:([^\|]*)\|\|\|([^\]]+)\]\]/g, (m, alt, src) => {
        const file = src.startsWith('/') || src.startsWith('http') ? src : `/static/docs/${src}`;
        return `<figure class="dd-figure"><img class="dd-screenshot" src="${escHtml(file)}" alt="${alt}" loading="lazy" onclick="window.__ddLightbox && window.__ddLightbox(this)" onerror="this.closest('.dd-figure').style.display='none'"><figcaption class="dd-figcaption">${alt}</figcaption></figure>`;
    });
    return out;
}

const CALLOUTS = {
    TIP:       { cls: 'tip',     icon: '💡', label: 'Tip' },
    NOTE:      { cls: 'note',    icon: 'ℹ️', label: 'Note' },
    INFO:      { cls: 'note',    icon: 'ℹ️', label: 'Note' },
    IMPORTANT: { cls: 'note',    icon: '❗', label: 'Important' },
    WARNING:   { cls: 'warning', icon: '⚠️', label: 'Warning' },
    DANGER:    { cls: 'danger',  icon: '🛑', label: 'Danger' },
};

let _activePageId = null;
function _currentPageId() { return _activePageId || ''; }

function renderMarkdown(src) {
    const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
    const html = [];
    const toc = [];
    let i = 0;

    function pushList(items, ordered) {
        if (ordered) {
            html.push('<ol class="dd-olist">' + items.map(li => `<li>${renderInline(escHtml(li))}</li>`).join('') + '</ol>');
        } else {
            html.push('<ul>' + items.map(li => `<li>${renderInline(escHtml(li))}</li>`).join('') + '</ul>');
        }
    }

    while (i < lines.length) {
        const line = lines[i];

        // Fenced code block
        const fence = line.match(/^```(\w*)\s*$/);
        if (fence) {
            const lang = fence[1] || 'text';
            const buf = [];
            i++;
            while (i < lines.length && !lines[i].startsWith('```')) { buf.push(lines[i]); i++; }
            i++; // skip closing fence
            const code = escHtml(buf.join('\n'));
            const btnId = 'ddcb' + Math.random().toString(36).slice(2, 8);
            html.push(
                `<div class="dd-codeblock"><div class="dd-codeblock-header"><span>${escHtml(lang)}</span>` +
                `<button class="dd-copy-btn" data-dd-copy="${btnId}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span>Copy</span></button></div>` +
                `<pre><code id="${btnId}">${code}</code></pre></div>`
            );
            continue;
        }

        // Container blocks: ::: steps / ::: cards
        const container = line.match(/^:::\s*(steps|cards)\s*$/);
        if (container) {
            const kind = container[1];
            const buf = [];
            i++;
            while (i < lines.length && !lines[i].match(/^:::\s*$/)) { buf.push(lines[i]); i++; }
            i++;
            if (kind === 'steps') {
                const steps = [];
                buf.forEach(bl => {
                    const m = bl.match(/^\s*\d+[.)]\s+(.*)$/);
                    if (m) steps.push(m[1]);
                    else if (steps.length && bl.trim()) steps[steps.length - 1] += ' ' + bl.trim();
                });
                html.push('<ol class="dd-steps">' + steps.map(s => `<li>${renderInline(escHtml(s))}</li>`).join('') + '</ol>');
            } else {
                // cards: ### <emoji> Title starts a card, following lines are its body
                const cards = [];
                let cur = null;
                buf.forEach(bl => {
                    const m = bl.match(/^###\s+(.*)$/);
                    if (m) { cur = { title: m[1].trim(), body: [] }; cards.push(cur); }
                    else if (cur && bl.trim()) cur.body.push(bl.trim());
                });
                html.push('<div class="dd-cards">' + cards.map(c => {
                    const parts = c.title.split(/\s+/);
                    const emoji = /^[^\w\s]/.test(parts[0]) ? parts[0] : '▫️';
                    const title = /^[^\w\s]/.test(parts[0]) ? parts.slice(1).join(' ') : c.title;
                    return `<div class="dd-card"><div class="dd-card-icon">${escHtml(emoji)}</div>` +
                        `<h4>${renderInline(escHtml(title))}</h4>` +
                        `<p>${renderInline(escHtml(c.body.join(' ')))}</p></div>`;
                }).join('') + '</div>');
            }
            continue;
        }

        // Headings
        const hm = line.match(/^(#{2,4})\s+(.*)$/);
        if (hm) {
            const level = hm[1].length;
            const text = hm[2].trim();
            const id = slugify(text);
            toc.push({ level, text: text.replace(/[*`]/g, ''), id });
            const inner = renderInline(escHtml(text));
            if (level === 2) html.push(`<h2 id="${id}">${inner}<a class="dd-anchor" href="#docs/${_currentPageId()}--${id}" data-dd-anchor="${id}" aria-label="Link to section">#</a></h2>`);
            else if (level === 3) html.push(`<h3 id="${id}">${inner}</h3>`);
            else html.push(`<h4>${inner}</h4>`);
            i++;
            continue;
        }

        // Horizontal rule
        if (/^---\s*$/.test(line)) { html.push('<hr class="dd-hr">'); i++; continue; }

        // Blockquote / callout
        if (/^>\s?/.test(line)) {
            const buf = [];
            while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, '')); i++; }
            const first = buf[0].match(/^\[!(TIP|NOTE|INFO|IMPORTANT|WARNING|DANGER)\]\s*(.*)$/i);
            if (first) {
                const key = first[1].toUpperCase();
                const meta = CALLOUTS[key] || CALLOUTS.NOTE;
                const titleLine = first[2].trim();
                const rest = buf.slice(1).filter(b => b.trim()).map(b => `<p>${renderInline(escHtml(b))}</p>`).join('');
                html.push(`<div class="dd-callout ${meta.cls}"><span class="dd-callout-icon">${meta.icon}</span><div class="dd-callout-body"><span class="dd-callout-title">${meta.label}</span>${titleLine ? `<p>${renderInline(escHtml(titleLine))}</p>` : ''}${rest}</div></div>`);
            } else {
                html.push('<blockquote class="dd-quote">' + buf.filter(b => b.trim()).map(b => `<p>${renderInline(escHtml(b))}</p>`).join('') + '</blockquote>');
            }
            continue;
        }

        // Table
        if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
            const parseRow = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
            const headers = parseRow(line);
            i += 2;
            const rows = [];
            while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) { rows.push(parseRow(lines[i])); i++; }
            html.push('<div class="dd-table-wrap"><table><thead><tr>' +
                headers.map(h => `<th>${renderInline(escHtml(h))}</th>`).join('') +
                '</tr></thead><tbody>' +
                rows.map(r => '<tr>' + r.map(c => `<td>${renderInline(escHtml(c))}</td>`).join('') + '</tr>').join('') +
                '</tbody></table></div>');
            continue;
        }

        // Unordered list
        if (/^\s*[-*]\s+/.test(line)) {
            const items = [];
            while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
                items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
                i++;
            }
            pushList(items, false);
            continue;
        }

        // Ordered list
        if (/^\s*\d+[.)]\s+/.test(line)) {
            const items = [];
            while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
                items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
                i++;
            }
            pushList(items, true);
            continue;
        }

        // Blank line
        if (!line.trim()) { i++; continue; }

        // Paragraph (gather consecutive lines)
        const buf = [line];
        i++;
        while (i < lines.length && lines[i].trim() &&
               !/^(#{2,4}\s|```|:::\s*|>\s?|\|.*\|\s*$|---\s*$|\s*[-*]\s+|\s*\d+[.)]\s+)/.test(lines[i])) {
            buf.push(lines[i].trim());
            i++;
        }
        html.push(`<p>${renderInline(escHtml(buf.join(' ')))}</p>`);
    }

    return { html: html.join('\n'), toc };
}

/* ── Lightbox ─────────────────────────────────────────────────── */
window.__ddLightbox = function (img) {
    const existing = document.querySelector('.dd-lightbox');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.className = 'dd-lightbox';
    overlay.innerHTML = `<button class="dd-lightbox-close" aria-label="Close">&times;</button><img src="${img.src}" alt="${img.alt || ''}">`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('active'));
    const close = () => {
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 220);
    };
    overlay.addEventListener('click', close);
    document.addEventListener('keydown', function handler(e) {
        if (e.key === 'Escape') { close(); document.removeEventListener('keydown', handler); }
    });
};

/* ── Sidebar ──────────────────────────────────────────────────── */
function buildSidebar() {
    const nav = document.getElementById('docs-nav');
    if (!nav) return;
    let html = '';
    _sections.forEach((section) => {
        const iconHtml = section.iconImg
            ? `<img class="docs-nav-icon" src="${escHtml(section.iconImg)}" alt="" onerror="this.style.display='none'">`
            : `<span class="docs-nav-emoji">${escHtml(section.icon || '📄')}</span>`;
        html += `<div class="docs-nav-section" data-section="${escHtml(section.id)}">` +
            `<div class="docs-nav-section-title" data-target="${escHtml(section.id)}" role="button" tabindex="0">` +
            `${iconHtml}<span class="docs-nav-label">${escHtml(section.title)}</span>` +
            `<span class="docs-nav-arrow">▶</span></div>` +
            `<div class="docs-nav-children" data-parent="${escHtml(section.id)}"><div class="docs-nav-children-inner">`;
        section.pages.forEach((page) => {
            html += `<div class="docs-nav-child" data-target="${escHtml(page.id)}" role="button" tabindex="0">${escHtml(page.title)}</div>`;
        });
        html += `</div></div></div>`;
    });
    nav.innerHTML = html;

    nav.querySelectorAll('.docs-nav-section-title').forEach((title) => {
        const go = () => {
            const sectionId = title.dataset.target;
            const section = _sections.find(s => s.id === sectionId);
            const wasExpanded = title.classList.contains('expanded');
            nav.querySelectorAll('.docs-nav-section-title').forEach(t => t.classList.remove('expanded'));
            nav.querySelectorAll('.docs-nav-children').forEach(c => c.classList.remove('expanded'));
            if (!wasExpanded && section && section.pages.length) {
                title.classList.add('expanded');
                const kids = nav.querySelector(`.docs-nav-children[data-parent="${sectionId}"]`);
                if (kids) kids.classList.add('expanded');
                renderPage(section.pages[0].id);
            }
        };
        title.addEventListener('click', go);
        title.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });

    nav.querySelectorAll('.docs-nav-child').forEach((child) => {
        const go = () => renderPage(child.dataset.target);
        child.addEventListener('click', go);
        child.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
}

function syncNav(pageId) {
    const nav = document.getElementById('docs-nav');
    if (!nav) return;
    const entry = findEntry(pageId);
    if (!entry) return;
    nav.querySelectorAll('.docs-nav-section-title').forEach(t => {
        const on = t.dataset.target === entry.section.id;
        t.classList.toggle('active', on);
        t.classList.toggle('expanded', on);
    });
    nav.querySelectorAll('.docs-nav-children').forEach(c => {
        c.classList.toggle('expanded', c.dataset.parent === entry.section.id);
    });
    nav.querySelectorAll('.docs-nav-child').forEach(c => {
        c.classList.toggle('active', c.dataset.target === pageId);
    });
    const active = nav.querySelector('.docs-nav-child.active');
    if (active && typeof active.scrollIntoView === 'function') active.scrollIntoView({ block: 'nearest' });
}

/* ── Page render ──────────────────────────────────────────────── */
function renderPage(pageId) {
    const entry = findEntry(pageId);
    if (!entry) return false;
    const { section, page } = entry;
    _activePageId = page.id;

    const content = document.getElementById('docs-content');
    if (!content) return false;

    const { html, toc } = renderMarkdown(page.body || '');

    const crumb = `<nav class="dd-breadcrumb" aria-label="Breadcrumb"><span>${escHtml(section.title)}</span><span class="dd-crumb-sep">›</span><span class="dd-crumb-current">${escHtml(page.title)}</span></nav>`;

    content.innerHTML =
        `<div class="docs-progress" id="dd-progress"></div>` +
        `<button class="dd-mobile-menu-btn" id="dd-mobile-menu" aria-label="Open docs menu">☰</button>` +
        `<article class="dd-article" id="docs-${escHtml(page.id)}">${crumb}` +
        `<h1 class="dd-title">${escHtml(page.title)}</h1>` +
        (page.lede ? `<p class="dd-lede">${renderInline(escHtml(page.lede))}</p>` : '') +
        `<div class="dd-body">${html}</div></article>` +
        renderPrevNext(page.id);

    content.scrollTop = 0;
    syncNav(page.id);
    renderToc(toc);
    wireContent(content);

    try { history.replaceState(null, '', '#docs/' + page.id); } catch (e) {}

    content.querySelectorAll('[data-dd-anchor]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            const target = document.getElementById(a.dataset.ddAnchor);
            if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            try { history.replaceState(null, '', '#docs/' + page.id + '--' + a.dataset.ddAnchor); } catch (err) {}
        });
    });

    const menuBtn = document.getElementById('dd-mobile-menu');
    if (menuBtn) menuBtn.addEventListener('click', () => {
        const layout = document.querySelector('.docs-layout');
        if (layout) layout.classList.toggle('dd-sidebar-open');
    });

    return true;
}

function renderPrevNext(pageId) {
    const idx = _flatPages.findIndex(e => e.page.id === pageId);
    if (idx < 0) return '';
    const prev = _flatPages[idx - 1];
    const next = _flatPages[idx + 1];
    const card = (e, dir) => e
        ? `<a class="dd-pn-${dir}" data-dd-link="${escHtml(e.page.id)}"><span class="dd-pn-dir">${dir === 'prev' ? '← Previous' : 'Next →'}</span><span class="dd-pn-title">${escHtml(e.page.title)}</span></a>`
        : `<span class="dd-pn-empty"></span>`;
    return `<nav class="dd-prevnext">${card(prev, 'prev')}${card(next, 'next')}</nav>`;
}

function wireContent(content) {
    content.querySelectorAll('[data-dd-link]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            navigateToDocsSection(a.dataset.ddLink);
            const layout = document.querySelector('.docs-layout');
            if (layout) layout.classList.remove('dd-sidebar-open');
        });
    });
    content.querySelectorAll('[data-dd-copy]').forEach(btn => {
        btn.addEventListener('click', () => {
            const code = document.getElementById(btn.dataset.ddCopy);
            if (!code) return;
            const done = () => {
                btn.classList.add('copied');
                const label = btn.querySelector('span:last-child');
                if (label) label.textContent = 'Copied';
                setTimeout(() => {
                    btn.classList.remove('copied');
                    if (label) label.textContent = 'Copy';
                }, 1600);
            };
            const text = code.innerText || code.textContent;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
            } else fallbackCopy(text, done);
        });
    });
}

function fallbackCopy(text, done) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
    done();
}

/* ── Right TOC + scrollspy ────────────────────────────────────── */
function renderToc(toc) {
    let aside = document.getElementById('docs-toc');
    if (!aside) {
        aside = document.createElement('aside');
        aside.className = 'docs-toc';
        aside.id = 'docs-toc';
        const layout = document.querySelector('.docs-layout');
        if (layout) layout.appendChild(aside);
    }
    if (!toc.length) {
        aside.innerHTML = '';
        aside.style.display = 'none';
        return;
    }
    aside.style.display = '';
    aside.innerHTML = `<p class="dd-toc-title">On this page</p><ul class="dd-toc-list">` +
        toc.map(t => `<li><a data-dd-toc="${t.id}" class="${t.level === 3 ? 'dd-toc-h3' : ''}">${escHtml(t.text)}</a></li>`).join('') +
        `</ul>`;
    aside.querySelectorAll('[data-dd-toc]').forEach(a => {
        a.addEventListener('click', () => {
            const target = document.getElementById(a.dataset.ddToc);
            if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });
}

function initScrollSpy() {
    const content = document.getElementById('docs-content');
    if (!content || content.dataset.ddSpy) return;
    content.dataset.ddSpy = '1';
    let ticking = false;
    content.addEventListener('scroll', () => {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(() => {
            ticking = false;
            const prog = document.getElementById('dd-progress');
            if (prog) {
                const max = content.scrollHeight - content.clientHeight;
                prog.style.width = (max > 0 ? (content.scrollTop / max) * 100 : 0) + '%';
            }
            const headings = Array.from(content.querySelectorAll('.dd-body h2[id], .dd-body h3[id]'));
            let current = null;
            const top = content.getBoundingClientRect().top + 90;
            headings.forEach(h => {
                if (h.getBoundingClientRect().top <= top) current = h.id;
            });
            document.querySelectorAll('#docs-toc [data-dd-toc]').forEach(a => {
                a.classList.toggle('active', a.dataset.ddToc === current);
            });
        });
    }, { passive: true });
}

/* ── Search ───────────────────────────────────────────────────── */
function stripMarkdown(s) {
    return String(s)
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/!\[[^\]]*\]\([^)]+\)/g, ' ')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/[#>*`_|:-]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

let _searchIndex = null;
function buildSearchIndex() {
    _searchIndex = [];
    _flatPages.forEach(({ section, page }) => {
        const text = stripMarkdown((page.lede || '') + '\n' + (page.body || ''));
        _searchIndex.push({
            pageId: page.id,
            sectionTitle: section.title,
            pageTitle: page.title,
            text,
            textLower: text.toLowerCase(),
        });
    });
}

function searchDocs(query) {
    if (!_searchIndex) buildSearchIndex();
    const q = query.toLowerCase().trim();
    if (!q) return [];
    const words = q.split(/\s+/);
    const results = [];
    _searchIndex.forEach(entry => {
        const titleLower = entry.pageTitle.toLowerCase();
        const secLower = entry.sectionTitle.toLowerCase();
        let score = 0;
        let firstPos = -1;
        const allMatch = words.every(w => {
            const ti = titleLower.indexOf(w);
            const si = secLower.indexOf(w);
            const bi = entry.textLower.indexOf(w);
            if (ti >= 0) { score += 30 - Math.min(ti, 20); return true; }
            if (si >= 0) { score += 8; return true; }
            if (bi >= 0) {
                score += 5;
                if (firstPos < 0 || bi < firstPos) firstPos = bi;
                return true;
            }
            return false;
        });
        if (!allMatch) return;
        let snippet = '';
        if (firstPos >= 0) {
            const start = Math.max(0, firstPos - 60);
            const end = Math.min(entry.text.length, firstPos + 130);
            snippet = (start > 0 ? '…' : '') + entry.text.slice(start, end) + (end < entry.text.length ? '…' : '');
        } else {
            snippet = entry.text.slice(0, 140) + (entry.text.length > 140 ? '…' : '');
        }
        results.push({ pageId: entry.pageId, sectionTitle: entry.sectionTitle, pageTitle: entry.pageTitle, score, snippet });
    });
    results.sort((a, b) => b.score - a.score);
    return results.slice(0, 12);
}

function highlight(text, query) {
    const words = query.trim().split(/\s+/).filter(Boolean).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    if (!words.length) return escHtml(text);
    const re = new RegExp('(' + words.join('|') + ')', 'gi');
    return escHtml(text).replace(re, '<mark>$1</mark>');
}

/* ── Command palette ──────────────────────────────────────────── */
let _paletteEl = null;
let _paletteOpen = false;
let _paletteSel = 0;
let _paletteResults = [];

function openPalette() {
    if (_paletteOpen) return;
    _paletteOpen = true;
    if (!_paletteEl) {
        _paletteEl = document.createElement('div');
        _paletteEl.className = 'dd-palette-overlay';
        _paletteEl.innerHTML =
            `<div class="dd-palette" role="dialog" aria-label="Search documentation">` +
            `<div class="dd-palette-input-row">` +
            `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>` +
            `<input class="dd-palette-input" id="dd-palette-input" placeholder="Search documentation…" autocomplete="off" spellcheck="false">` +
            `<span class="dd-kbd">esc</span></div>` +
            `<div class="dd-palette-results" id="dd-palette-results"></div>` +
            `<div class="dd-palette-footer"><span><span class="dd-kbd">↑↓</span> navigate</span><span><span class="dd-kbd">↵</span> open</span><span><span class="dd-kbd">esc</span> close</span></div>` +
            `</div>`;
        document.body.appendChild(_paletteEl);
        _paletteEl.addEventListener('mousedown', (e) => { if (e.target === _paletteEl) closePalette(); });
        const input = _paletteEl.querySelector('#dd-palette-input');
        input.addEventListener('input', () => renderPaletteResults(input.value));
        input.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); movePaletteSel(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); movePaletteSel(-1); }
            else if (e.key === 'Enter') { e.preventDefault(); choosePaletteSel(); }
            else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
        });
    }
    _paletteSel = 0;
    renderPaletteResults('');
    requestAnimationFrame(() => _paletteEl.classList.add('open'));
    setTimeout(() => {
        const input = document.getElementById('dd-palette-input');
        if (input) { input.value = ''; input.focus(); renderPaletteResults(''); }
    }, 30);
}

function closePalette() {
    if (!_paletteOpen || !_paletteEl) return;
    _paletteOpen = false;
    _paletteEl.classList.remove('open');
}

function renderPaletteResults(query) {
    const box = document.getElementById('dd-palette-results');
    if (!box) return;
    _paletteResults = query.trim() ? searchDocs(query) : defaultPaletteResults();
    _paletteSel = 0;
    if (!_paletteResults.length) {
        box.innerHTML = `<div class="dd-palette-empty">No results for “${escHtml(query)}”. Try different keywords.</div>`;
        return;
    }
    let html = '';
    let lastSec = null;
    _paletteResults.forEach((r, idx) => {
        if (r.sectionTitle !== lastSec) {
            html += `<div class="dd-palette-group">${escHtml(r.sectionTitle)}</div>`;
            lastSec = r.sectionTitle;
        }
        html += `<button class="dd-palette-item${idx === _paletteSel ? ' selected' : ''}" data-dd-pi="${idx}">` +
            `<span class="dd-pi-title">${query.trim() ? highlight(r.pageTitle, query) : escHtml(r.pageTitle)}</span>` +
            (r.snippet && query.trim() ? `<span class="dd-pi-snippet">${highlight(r.snippet, query)}</span>` : '') +
            `</button>`;
    });
    box.innerHTML = html;
    box.querySelectorAll('[data-dd-pi]').forEach(btn => {
        btn.addEventListener('click', () => {
            _paletteSel = parseInt(btn.dataset.ddPi, 10);
            choosePaletteSel();
        });
        btn.addEventListener('mousemove', () => {
            const i = parseInt(btn.dataset.ddPi, 10);
            if (i !== _paletteSel) { _paletteSel = i; paintPaletteSel(); }
        });
    });
}

function defaultPaletteResults() {
    const picks = ['gs-first-setup', 'wf-download', 'search-quality', 'sync-spotify', 'auto-builder', 'troubleshooting'];
    return picks.map(pid => {
        const e = findEntry(pid);
        if (!e) return null;
        return { pageId: pid, sectionTitle: e.section.title, pageTitle: e.page.title, snippet: '' };
    }).filter(Boolean);
}

function paintPaletteSel() {
    document.querySelectorAll('#dd-palette-results [data-dd-pi]').forEach(btn => {
        btn.classList.toggle('selected', parseInt(btn.dataset.ddPi, 10) === _paletteSel);
    });
    const sel = document.querySelector(`#dd-palette-results [data-dd-pi="${_paletteSel}"]`);
    if (sel && typeof sel.scrollIntoView === 'function') sel.scrollIntoView({ block: 'nearest' });
}

function movePaletteSel(d) {
    if (!_paletteResults.length) return;
    _paletteSel = (_paletteSel + d + _paletteResults.length) % _paletteResults.length;
    paintPaletteSel();
}

function choosePaletteSel() {
    const r = _paletteResults[_paletteSel];
    if (!r) return;
    closePalette();
    navigateToDocsSection(r.pageId);
}

function initPalette() {
    const header = document.querySelector('.docs-sidebar-header');
    if (header && !header.querySelector('.docs-search-trigger')) {
        const h3 = header.querySelector('h3');
        if (h3) h3.innerHTML = `Documentation <span class="dd-docs-badge">Help</span>`;
        const btn = document.createElement('button');
        btn.className = 'docs-search-trigger';
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg><span>Search docs…</span><span class="dd-kbd-hint"><span class="dd-kbd">/</span></span>`;
        btn.addEventListener('click', openPalette);
        header.appendChild(btn);
    }
    // "/" opens search when the help page is visible and the user isn't typing
    document.addEventListener('keydown', (e) => {
        if (_paletteOpen) return;
        if (e.key !== '/') return;
        const tag = document.activeElement && document.activeElement.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (document.activeElement && document.activeElement.isContentEditable) return;
        const helpPage = document.getElementById('help-page');
        if (!helpPage || helpPage.offsetParent === null) return;
        e.preventDefault();
        openPalette();
    });
}

/* ── Public API ───────────────────────────────────────────────── */
let _docsInitialized = false;

function initializeDocsPage() {
    if (_docsInitialized) return;
    if (!_sections.length) return; // content scripts not loaded yet
    _docsInitialized = true;

    const nav = document.getElementById('docs-nav');
    const content = document.getElementById('docs-content');
    if (!nav || !content) return;

    buildSidebar();
    initPalette();
    initScrollSpy();

    let startId = _flatPages.length ? _flatPages[0].page.id : null;
    const m = (location.hash || '').match(/^#docs\/([A-Za-z0-9_-]+)/);
    if (m && findEntry(m[1])) startId = m[1];
    if (startId) renderPage(startId);

    const hm2 = (location.hash || '').match(/--([A-Za-z0-9-]+)$/);
    if (hm2) {
        setTimeout(() => {
            const t = document.getElementById(hm2[1]);
            if (t && typeof t.scrollIntoView === 'function') t.scrollIntoView({ block: 'start' });
        }, 120);
    }
}

// Expose globally (init.js calls this; downloads.js calls navigateToDocsSection)
window.initializeDocsPage = initializeDocsPage;

window.navigateToDocsSection = function (sectionId) {
    if (!sectionId) return;
    if (typeof navigateToPage === 'function') navigateToPage('help');
    let tries = 0;
    const attempt = () => {
        tries++;
        if (!_sections.length && tries < 20) { setTimeout(attempt, 150); return; }
        if (!_docsInitialized) initializeDocsPage();
        const entry = findEntry(sectionId);
        if (entry) {
            renderPage(entry.page.id);
            syncNav(entry.page.id);
        } else if (_flatPages.length) {
            renderPage(_flatPages[0].page.id);
        }
    };
    setTimeout(attempt, 60);
};

// Legacy alias used in older markup
window.navigateToDocs = window.navigateToDocsSection;

})();
