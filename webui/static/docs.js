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

    // Restore the interactive API explorer on every API Reference page.
    if (entry.section.id === 'api') mountApiExplorer(content);

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

/* ── Debug info panel ─────────────────────────────────────────── */
// Restores the "Copy Debug Info" button dropped by the docs rebuild. It lives
// in the docs sidebar header, under the search trigger: picks the log-line
// count and log source, hits GET /api/debug-info (admin-only), formats the
// JSON into a plain-text snapshot and copies it to the clipboard for bug
// reports. navigator.clipboard needs a secure context, so over plain HTTP on
// the LAN it falls back to execCommand, then to a select-all modal.
const _DD_LOG_LINES_OPTIONS = ['20', '50', '100', '200', '500'];
const _DD_LOG_SOURCES = [
    ['app', 'app.log'],
    ['post_processing', 'post_processing.log'],
    ['acoustid', 'acoustid.log'],
    ['source_reuse', 'source_reuse.log'],
];

function ddFormatDebugInfo(data) {
    const ck = '✓';
    const ex = '✗';
    const out = [];
    out.push('SoulSync Debug Info');
    out.push('═══════════════════════════════════');
    out.push('');

    out.push('── System ──');
    out.push(`Version:   ${data.version || 'unknown'}`);
    out.push(`OS:        ${data.os || 'unknown'}${data.docker ? ' (Docker)' : ''}`);
    out.push(`Python:    ${data.python || 'unknown'}`);
    out.push(`ffmpeg:    ${data.ffmpeg || 'unknown'}`);
    out.push(`Runner:    ${data.runner || 'unknown'}`);
    out.push(`Uptime:    ${data.uptime || 'unknown'}`);
    out.push(`Memory:    ${data.memory_usage || '?'} (system: ${data.system_memory || '?'})`);
    out.push(`CPU:       ${data.cpu_percent || '?'}`);
    out.push(`Threads:   ${data.thread_count != null ? data.thread_count : '?'}`);
    out.push('');

    out.push('── Services ──');
    const svc = data.services || {};
    out.push(`Music Source:  ${svc.music_source || 'unknown'}`);
    out.push(`Spotify:       ${svc.spotify_connected ? ck + ' Connected' : ex + ' Disconnected'}${svc.spotify_rate_limited ? ' (RATE LIMITED)' : ''}`);
    out.push(`Media Server:  ${svc.media_server_type || 'none'} ${svc.media_server_connected ? ck + ' Connected' : ex + ' Disconnected'}`);
    out.push(`Soulseek:      ${svc.soulseek_connected ? ck + ' Connected' : ex + ' Disconnected'}`);
    out.push(`Tidal:         ${svc.tidal_connected ? ck + ' Connected' : ex + ' Disconnected'}`);
    out.push(`Qobuz:         ${svc.qobuz_connected ? ck + ' Connected' : ex + ' Disconnected'}`);
    out.push(`Discogs:       ${svc.discogs_connected ? ck + ' Token set' : ex + ' No token'}`);
    out.push(`Download Mode: ${svc.download_source || 'unknown'}`);
    out.push('');

    out.push('── Library ──');
    const lib = data.library || {};
    out.push(`Artists:     ${lib.artists != null ? lib.artists.toLocaleString() : '0'}`);
    out.push(`Albums:      ${lib.albums != null ? lib.albums.toLocaleString() : '0'}`);
    out.push(`Tracks:      ${lib.tracks != null ? lib.tracks.toLocaleString() : '0'}`);
    out.push(`Database:    ${data.database_size || 'unknown'}`);
    out.push(`Watchlist:   ${data.watchlist_count || 0} artists`);
    out.push(`Wishlist:    ${data.wishlist_count || 0} pending`);
    const am = data.automations || {};
    out.push(`Automations: ${am.enabled || 0} enabled / ${am.total || 0} total`);
    out.push('');

    out.push('── Active ──');
    out.push(`Downloads: ${data.active_downloads || 0}`);
    out.push(`Syncs:     ${data.active_syncs || 0}`);
    out.push('');

    out.push('── Paths ──');
    const p = data.paths || {};
    const pathStatus = (exists, writable) => exists
        ? (writable ? ck + ' ok' : ck + ' exists ' + ex + ' not writable')
        : ex + ' missing';
    out.push(`Input:    ${p.download_path || '(not set)'} [${pathStatus(p.download_path_exists, p.download_path_writable)}]`);
    out.push(`Transfer: ${p.transfer_folder || '(not set)'} [${pathStatus(p.transfer_folder_exists, p.transfer_folder_writable)}]`);
    out.push(`Import:   ${p.staging_folder ? p.staging_folder + ' [' + (p.staging_folder_exists ? ck + ' ok' : ex + ' missing') + ']' : '(not configured — optional)'}`);
    if (p.music_videos_path) {
        out.push(`Videos:   ${p.music_videos_path} [${p.music_videos_path_exists ? ck + ' ok' : ex + ' missing'}]`);
    }
    (p.music_library_paths || []).forEach(lp => {
        out.push(`Library:  ${lp.path} [${lp.exists ? ck + ' ok' : ex + ' missing'}]`);
    });
    out.push('');

    out.push('── Config ──');
    const cfg = data.config || {};
    out.push(`Log Level:        ${cfg.log_level || 'INFO'}`);
    out.push(`Source Mode:      ${cfg.source_mode || 'unknown'}`);
    if (cfg.source_mode === 'hybrid' && cfg.hybrid_sources && cfg.hybrid_sources.length) {
        out.push(`Hybrid Priority:  ${cfg.hybrid_sources.join(' → ')}`);
    }
    out.push(`Metadata Source:  ${cfg.primary_metadata_source || 'deezer'}`);
    out.push(`Quality Profile:  ${cfg.quality_profile || 'default'}`);
    out.push(`Folder Template:  ${cfg.organization_template || '(default)'}`);
    out.push(`Post-Processing:  ${cfg.post_processing_enabled ? 'enabled' : 'disabled'}`);
    if (cfg.lossy_copy_enabled) {
        out.push(`Lossy Copy:       ${(cfg.lossy_copy_format || '').toUpperCase()} @ ${cfg.lossy_copy_bitrate || '?'}kbps`);
    }
    out.push(`AcoustID:         ${cfg.acoustid_enabled ? 'enabled' : 'disabled'}`);
    out.push(`Auto Scan:        ${cfg.auto_scan_enabled ? 'enabled' : 'disabled'}`);
    out.push(`Auto Import:      ${cfg.auto_import_enabled ? 'enabled' : 'disabled'}`);
    out.push(`Duplicate Tracks: ${cfg.allow_duplicate_tracks ? 'allowed' : 'rejected'}`);
    out.push(`Replace Quality:  ${cfg.replace_lower_quality ? 'enabled' : 'disabled'}`);
    out.push(`M3U Export:       ${cfg.m3u_export_enabled ? 'enabled' : 'disabled'}`);
    out.push('');

    out.push('── Enrichment Workers ──');
    const active = [];
    const paused = [];
    Object.entries(data.enrichment_workers || {}).forEach(([name, status]) => {
        (status === 'active' ? active : paused).push(name);
    });
    out.push(`Active:  ${active.length ? active.join(', ') : 'none'}`);
    out.push(`Paused:  ${paused.length ? paused.join(', ') : 'none'}`);
    out.push('');

    if (data.download_client_failures && data.download_client_failures.length) {
        out.push('── Download Client Failures ──');
        data.download_client_failures.forEach(f => out.push(`  ❌ ${f}`));
        out.push('');
    }

    out.push('── API Rates (calls/min) ──');
    Object.entries(data.api_rates || {}).forEach(([svcName, info]) => {
        const cpm = (info && info.cpm) || 0;
        const limit = (info && info.limit) || '?';
        out.push(`${svcName.padEnd(14)} ${String(cpm).padStart(5)}/min  (limit: ${limit})`);
    });
    if (data.spotify_rate_limit && data.spotify_rate_limit.active) {
        const rl = data.spotify_rate_limit;
        out.push('');
        out.push('*** SPOTIFY RATE LIMITED ***');
        out.push(`Triggered by: ${rl.endpoint || 'unknown'}`);
        out.push(`Remaining:    ${Math.ceil((rl.remaining_seconds || 0) / 60)} minutes`);
        out.push(`Retry-After:  ${rl.retry_after || '?'}s`);
    }
    out.push('');

    if (data.available_logs && data.available_logs.length) {
        out.push('── Log Files ──');
        data.available_logs.forEach(log => {
            out.push(`  ${log.file.padEnd(24)} ${log.size || ''}`);
        });
        out.push('');
    }

    const logLines = data.recent_logs || [];
    out.push(`── Logs: ${data.log_source || 'app'}.log (last ${logLines.length} lines) ──`);
    if (logLines.length) logLines.forEach(line => out.push(line));
    else out.push('(no log lines)');
    out.push('');
    out.push('---');
    out.push('Paste this output into your GitHub issue at https://github.com/Nezreka/SoulSync/issues');
    return out.join('\n');
}

async function ddCopyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
    return ok;
}

// Select-all modal shown only when no clipboard API could write (e.g. HTTP
// over LAN). Styled on the new search-palette pieces.
function ddShowDebugModal(text) {
    const existing = document.getElementById('dd-debug-modal');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.className = 'dd-palette-overlay';
    overlay.id = 'dd-debug-modal';
    overlay.innerHTML =
        `<div class="dd-palette" role="dialog" aria-label="Debug info">` +
        `<div class="dd-palette-input-row">` +
        `<span style="font-weight:600;font-size:13px;color:#fff;">Debug Info — select all &amp; copy</span>` +
        `<button type="button" data-dd-debug-close style="margin-left:auto;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;">Close</button>` +
        `</div>` +
        `<div style="padding:12px;"><textarea readonly style="width:100%;height:40vh;box-sizing:border-box;background:#0d1117;color:#e0e0e0;border:1px solid rgba(255,255,255,0.12);border-radius:8px;padding:10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;resize:none;outline:none;"></textarea></div>` +
        `</div>`;
    overlay.querySelector('[data-dd-debug-close]').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('open'));
    const ta = overlay.querySelector('textarea');
    ta.value = text;
    ta.focus();
    ta.select();
}

function ddToast(message, type) {
    if (typeof showToast === 'function') showToast(message, type || 'info');
}

function initDebugPanel() {
    const header = document.querySelector('.docs-sidebar-header');
    if (!header || header.querySelector('.dd-debug-wrap')) return;

    const wrap = document.createElement('div');
    wrap.className = 'dd-debug-wrap';
    wrap.style.cssText = 'margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.07);';

    const selStyle = 'background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:rgba(255,255,255,0.85);border-radius:6px;padding:4px 6px;font-size:12px;font-family:inherit;max-width:150px;';
    const rowStyle = 'display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:rgba(255,255,255,0.55);';

    wrap.innerHTML =
        `<button class="docs-search-trigger" id="dd-debug-btn" type="button">` +
        `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>` +
        `<span data-dd-debug-label>Copy Debug Info</span></button>` +
        `<div style="margin-top:8px;display:flex;flex-direction:column;gap:6px;">` +
        `<label style="${rowStyle}"><span>Log lines</span>` +
        `<select id="dd-debug-lines" style="${selStyle}">` +
        _DD_LOG_LINES_OPTIONS.map(v => `<option value="${v}"${v === '100' ? ' selected' : ''}>${v}</option>`).join('') +
        `</select></label>` +
        `<label style="${rowStyle}"><span>Log source</span>` +
        `<select id="dd-debug-source" style="${selStyle}">` +
        _DD_LOG_SOURCES.map(([v, label]) => `<option value="${v}">${label}</option>`).join('') +
        `</select></label>` +
        `</div>`;
    header.appendChild(wrap);

    const btn = wrap.querySelector('#dd-debug-btn');
    const labelEl = wrap.querySelector('[data-dd-debug-label]');
    const DEFAULT_LABEL = 'Copy Debug Info';
    const revert = () => setTimeout(() => { labelEl.textContent = DEFAULT_LABEL; }, 2200);

    btn.addEventListener('click', async () => {
        const lines = wrap.querySelector('#dd-debug-lines').value;
        const source = wrap.querySelector('#dd-debug-source').value;
        labelEl.textContent = 'Collecting…';
        btn.disabled = true;
        try {
            const resp = await fetch(`/api/debug-info?lines=${encodeURIComponent(lines)}&log=${encodeURIComponent(source)}`);
            if (resp.status === 403) {
                labelEl.textContent = 'Admin only';
                ddToast('Debug info is admin-only', 'error');
                revert();
                return;
            }
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            const text = ddFormatDebugInfo(data);
            if (await ddCopyText(text)) {
                labelEl.textContent = '✅ Copied!';
                ddToast('Debug info copied to clipboard', 'success');
            } else {
                ddShowDebugModal(text);
                labelEl.textContent = DEFAULT_LABEL;
                ddToast('Clipboard unavailable — copy from the dialog', 'warning');
            }
        } catch (err) {
            console.error('Debug info error:', err);
            labelEl.textContent = '❌ Failed';
            ddToast('Failed to collect debug info', 'error');
        } finally {
            btn.disabled = false;
            if (labelEl.textContent !== DEFAULT_LABEL) revert();
        }
    });
}

/* ── API explorer ─────────────────────────────────────────────── */
// Restores the interactive API explorer dropped by the docs rebuild: an API
// key bar (value persisted to localStorage), expandable endpoint rows, and a
// live "try it" fetch runner against /api/v1 on the same host. renderPage()
// appends the explorer to every API Reference page. The endpoint catalog is
// kept in sync with docs-content/16-api.js — key-authenticated public routes
// only. Session-authenticated internals (/api/video) and WebSocket events are
// deliberately excluded.
const _DD_API_BASE = '/api/v1';
const _DD_API_KEY_LS = 'soulsync_docs_api_key';

function _ddE(m, p, d, o) {
    return Object.assign({ m, p, d, q: [], b: [], bx: '', na: false, note: '' }, o || {});
}

const DD_API_GROUPS = [
    { title: 'System', desc: 'Health, statistics, and the live activity feed.', eps: [
        _ddE('GET', '/system/status', 'Health check: server uptime and service connectivity'),
        _ddE('GET', '/system/stats', 'Combined library and worker statistics'),
        _ddE('GET', '/system/activity', 'Recent activity feed entries, newest first'),
    ] },
    { title: 'Library', desc: 'Read-only access to your music library.', eps: [
        _ddE('GET', '/library/artists', 'List artists (paginated)', { q: [
            ['search', 'string', 0, 'Substring filter on artist name'],
            ['letter', 'string', 0, 'Filter by first letter, or "all"', 'all'],
            ['watchlist', 'string', 0, 'Filter by watchlist status', 'all'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/artists/{artist_id}', 'One artist with metadata and album list', { q: [
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/artists/{artist_id}/albums', 'Albums by an artist (paginated)', { q: [
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/albums', 'List or search albums (paginated)', { q: [
            ['search', 'string', 0, 'Substring filter on album title'],
            ['artist_id', 'int', 0, 'Filter by artist ID'],
            ['year', 'int', 0, 'Filter by release year'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/albums/{album_id}', 'One album with metadata and embedded tracks', { q: [
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/albums/{album_id}/tracks', 'Tracks on an album', { q: [
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/tracks', 'Search tracks — needs title or artist (not paginated)', { q: [
            ['title', 'string', 1, 'Track title (title or artist required)'],
            ['artist', 'string', 1, 'Artist name (title or artist required)'],
            ['limit', 'int', 0, 'Max results (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/tracks/{track_id}', 'One track with all metadata', { q: [
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/genres', 'Genre list with occurrence counts', { q: [
            ['limit', 'int', 0, 'Max genres', '50'],
        ] }),
        _ddE('GET', '/library/recently-added', 'Newest additions first', { q: [
            ['type', 'string', 0, '"albums", "artists", or "tracks"', 'albums'],
            ['limit', 'int', 0, 'Max items (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/lookup', 'Resolve an artist/album/track by external provider ID', { q: [
            ['type', 'string', 1, '"artist", "album", or "track"'],
            ['provider', 'string', 1, '"spotify", "musicbrainz", "itunes", "deezer", "tidal", "qobuz", or "genius"'],
            ['id', 'string', 1, 'The external ID value'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/library/stats', 'Library totals'),
    ] },
    { title: 'Search', desc: 'Provider search for tracks, albums, and artists.', eps: [
        _ddE('POST', '/search/tracks', 'Search providers for tracks', { b: [
            ['query', 'string', 1, 'Search query'],
            ['source', 'string', 0, '"spotify", "itunes", or "auto"'],
            ['limit', 'int', 0, 'Max results (1-50)'],
        ], bx: '{\n  "query": "Karma Police",\n  "source": "auto",\n  "limit": 10\n}' }),
        _ddE('POST', '/search/albums', 'Search providers for albums', { b: [
            ['query', 'string', 1, 'Search query'],
            ['limit', 'int', 0, 'Max results (1-50)'],
        ], bx: '{\n  "query": "OK Computer",\n  "limit": 5\n}' }),
        _ddE('POST', '/search/artists', 'Search providers for artists', { b: [
            ['query', 'string', 1, 'Search query'],
            ['limit', 'int', 0, 'Max results (1-50)'],
        ], bx: '{\n  "query": "Radiohead",\n  "limit": 5\n}' }),
    ] },
    { title: 'Downloads', desc: 'See what is downloading, and stop anything that should not be.', eps: [
        _ddE('GET', '/downloads', 'Active and queued downloads with progress'),
        _ddE('POST', '/downloads/{download_id}/cancel', 'Cancel a specific download', { b: [
            ['username', 'string', 1, 'Soulseek username for the transfer'],
        ], bx: '{\n  "username": "slsk_user42"\n}' }),
        _ddE('POST', '/downloads/cancel-all', 'Cancel all active downloads and clear completed'),
    ] },
    { title: 'Playlists', desc: 'List playlists, inspect one, or trigger a sync.', eps: [
        _ddE('GET', '/playlists', 'List playlists from the connected provider (not paginated)', { q: [
            ['source', 'string', 0, '"spotify" or "tidal"', 'spotify'],
        ] }),
        _ddE('GET', '/playlists/{playlist_id}', 'One playlist with its tracks'),
        _ddE('POST', '/playlists/{playlist_id}/sync', 'Trigger a playlist sync and download', { b: [
            ['playlist_name', 'string', 1, 'Name of the playlist'],
            ['tracks', 'array', 1, 'Array of track objects to sync'],
            ['sync_mode', 'string', 0, '"replace" or "append"'],
        ], bx: '{\n  "playlist_name": "My Playlist",\n  "tracks": [\n    { "id": "3SVAN3...", "name": "Karma Police", "artists": [{ "name": "Radiohead" }] }\n  ]\n}' }),
    ] },
    { title: 'Watchlist', desc: 'Manage watched artists, per-profile via X-Profile-Id.', eps: [
        _ddE('GET', '/watchlist', 'Watched artists for the current profile (not paginated)', { q: [
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('POST', '/watchlist', 'Add an artist to the watchlist', { b: [
            ['artist_id', 'string', 1, 'Provider artist ID'],
            ['artist_name', 'string', 1, 'Artist display name'],
            ['source', 'string', 0, 'Name the provider explicitly'],
            ['quality_profile_id', 'int', 0, 'Acquisition quality intent'],
        ], bx: '{\n  "artist_id": "4Z8W4fKeB5YxbusRsdQVPb",\n  "artist_name": "Radiohead"\n}' }),
        _ddE('PATCH', '/watchlist/{artist_id}', 'Update content type filters for a watchlist artist', { b: [
            ['include_albums', 'bool', 0, 'Include albums'],
            ['include_eps', 'bool', 0, 'Include EPs'],
            ['include_singles', 'bool', 0, 'Include singles'],
            ['include_live', 'bool', 0, 'Include live recordings'],
            ['include_remixes', 'bool', 0, 'Include remixes'],
            ['include_acoustic', 'bool', 0, 'Include acoustic versions'],
            ['include_compilations', 'bool', 0, 'Include compilations'],
            ['quality_profile_id', 'int', 0, 'Acquisition quality intent'],
        ], bx: '{\n  "include_albums": true,\n  "include_singles": false\n}' }),
        _ddE('DELETE', '/watchlist/{artist_id}', 'Remove an artist from the watchlist'),
        _ddE('POST', '/watchlist/scan', 'Trigger a watchlist scan for new releases'),
    ] },
    { title: 'Wishlist', desc: 'Tracks you want but do not have yet.', eps: [
        _ddE('GET', '/wishlist', 'Wishlist entries (paginated)', { q: [
            ['category', 'string', 0, 'Filter by category'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('POST', '/wishlist', 'Add a track to the wishlist', { b: [
            ['track_data', 'object', 1, 'Track object (id, name, artists, album, …)'],
            ['quality_profile_id', 'int', 0, 'Acquisition quality intent'],
        ], bx: '{\n  "track_data": {\n    "id": "3SVAN3BRByDmHOhKyIDxfC",\n    "name": "Karma Police",\n    "artists": [{ "name": "Radiohead" }],\n    "album": { "name": "OK Computer", "album_type": "album" }\n  }\n}' }),
        _ddE('DELETE', '/wishlist/{track_id}', 'Remove a track from the wishlist'),
        _ddE('POST', '/wishlist/process', 'Process the wishlist now'),
    ] },
    { title: 'Requests', desc: 'Submit a download request and poll its status.', eps: [
        _ddE('POST', '/request', 'Queue a search-and-download request (202 + request_id)', { b: [
            ['query', 'string', 1, 'What to find, e.g. "Radiohead - Karma Police"'],
            ['title', 'string', 0, 'Expected title the result must match'],
            ['artist', 'string', 0, 'Expected artist the result must match'],
            ['duration_ms', 'int', 0, 'Expected duration in milliseconds'],
            ['notify_url', 'string', 0, 'http(s) callback URL POSTed with the final status'],
            ['metadata', 'object', 0, 'Passthrough data echoed in automation events'],
        ], bx: '{\n  "query": "Radiohead - Karma Police"\n}', note: 'Request state is in-memory and expires — poll GET /request/{request_id} promptly.' }),
        _ddE('GET', '/request/{request_id}', 'Poll the status of a request'),
    ] },
    { title: 'Discover', desc: 'Discovery pool, similar artists, and new releases.', eps: [
        _ddE('GET', '/discover/pool', 'The current discovery pool', { q: [
            ['new_releases_only', 'string', 0, '"true" to filter new releases only', 'false'],
            ['source', 'string', 0, '"spotify" or "itunes"', 'all'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Max tracks (max 500)', '100'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/discover/pool/metadata', 'Metadata about pool entries'),
        _ddE('GET', '/discover/similar-artists', 'Similar-artist recommendations', { q: [
            ['limit', 'int', 0, 'Max artists (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/discover/recent-releases', 'Recently released albums', { q: [
            ['limit', 'int', 0, 'Max releases (max 200)', '50'],
            ['fields', 'string', 0, 'Comma-separated field names'],
        ] }),
        _ddE('GET', '/discover/bubbles', 'Discovery bubble snapshots for the current profile'),
        _ddE('GET', '/discover/bubbles/{snapshot_type}', 'One bubble snapshot (artist_bubbles, search_bubbles, discover_downloads)'),
    ] },
    { title: 'Profiles', desc: 'Create, read, update, and delete user profiles.', eps: [
        _ddE('GET', '/profiles', 'List all profiles (not paginated)'),
        _ddE('POST', '/profiles', 'Create a new profile', { b: [
            ['name', 'string', 1, 'Profile display name'],
            ['avatar_color', 'string', 0, 'Hex color for avatar'],
            ['avatar_url', 'string', 0, 'Custom avatar image URL'],
            ['is_admin', 'bool', 0, 'Admin privileges'],
            ['pin', 'string', 0, 'PIN for profile protection'],
        ], bx: '{\n  "name": "Family Room",\n  "is_admin": false,\n  "avatar_color": "#22c55e"\n}' }),
        _ddE('GET', '/profiles/{profile_id}', 'One profile'),
        _ddE('PUT', '/profiles/{profile_id}', 'Update a profile', { b: [
            ['name', 'string', 0, 'New display name'],
            ['avatar_color', 'string', 0, 'Hex color'],
            ['avatar_url', 'string', 0, 'Avatar image URL'],
            ['is_admin', 'bool', 0, 'Admin privileges'],
            ['pin', 'string', 0, 'New PIN (empty string clears PIN)'],
        ], bx: '{\n  "name": "Kids Room",\n  "avatar_color": "#f59e0b"\n}' }),
        _ddE('DELETE', '/profiles/{profile_id}', 'Delete a profile', { note: 'Profile 1 (the default admin) cannot be deleted: 403. There is no undo.' }),
    ] },
    { title: 'Settings & API Keys', desc: 'Read and update server settings, and manage API keys themselves.', eps: [
        _ddE('GET', '/settings', 'Current settings (sensitive values redacted)'),
        _ddE('PATCH', '/settings', 'Update settings (partial, dot-notation keys accepted)', { b: [
            ['{key}', 'any', 1, 'One or more key-value pairs. The "api_keys" key is blocked.'],
        ], bx: '{\n  "spotify.country": "GB",\n  "download_path": "/new/music/path"\n}' }),
        _ddE('GET', '/api-keys', 'List API keys (prefixes and labels — never raw keys or hashes)'),
        _ddE('POST', '/api-keys', 'Mint a new key (raw key returned once)', { b: [
            ['label', 'string', 0, 'Descriptive label for the key'],
        ], bx: '{\n  "label": "My Integration"\n}' }),
        _ddE('DELETE', '/api-keys/{key_id}', 'Revoke a key', { note: 'Takes effect immediately — rotate first, then revoke.' }),
        _ddE('POST', '/api-keys/bootstrap', 'Mint the first key when none exist (NO AUTH REQUIRED)', { na: true, b: [
            ['label', 'string', 0, 'Label for the key'],
        ], bx: '{\n  "label": "My First Key"\n}', note: 'Works without an API key only while zero keys exist — afterwards it answers 403.' }),
    ] },
    { title: 'Retag', desc: 'Inspect and manage retag groups — the batches behind library-wide tag fixes.', eps: [
        _ddE('GET', '/retag/groups', 'List all retag groups with track counts'),
        _ddE('GET', '/retag/groups/{group_id}', 'One retag group, with its tracks'),
        _ddE('DELETE', '/retag/groups/{group_id}', 'Delete one retag group'),
        _ddE('DELETE', '/retag/groups', 'Delete all retag groups', { note: 'Deletes every retag group at once. There is no undo.' }),
        _ddE('GET', '/retag/stats', 'Retag queue statistics'),
    ] },
    { title: 'Cache', desc: 'Peek at the metadata caches SoulSync keeps warm.', eps: [
        _ddE('GET', '/cache/musicbrainz', 'MusicBrainz cache entries', { q: [
            ['entity_type', 'string', 0, '"artist", "album", or "track"'],
            ['search', 'string', 0, 'Filter by entity name'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page (max 200)', '50'],
        ] }),
        _ddE('GET', '/cache/musicbrainz/stats', 'MusicBrainz cache statistics'),
        _ddE('GET', '/cache/discovery-matches', 'Discovery match cache entries', { q: [
            ['provider', 'string', 0, '"spotify", "itunes", etc.'],
            ['search', 'string', 0, 'Filter by title or artist'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page (max 200)', '50'],
        ] }),
        _ddE('GET', '/cache/discovery-matches/stats', 'Discovery match cache statistics'),
    ] },
    { title: 'ListenBrainz', desc: 'Read the ListenBrainz playlists SoulSync has imported or generated.', eps: [
        _ddE('GET', '/listenbrainz/playlists', 'List ListenBrainz playlists', { q: [
            ['type', 'string', 0, 'Filter by playlist_type (e.g. "weekly-jams")'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page (max 200)', '50'],
        ] }),
        _ddE('GET', '/listenbrainz/playlists/{playlist_id}', 'One playlist with tracks (ID or MBID)'),
    ] },
    { title: 'MetaSync Export', desc: 'A read-only, cursor-paged walk of the library\u2019s resolved metadata.', eps: [
        _ddE('GET', '/metasync/export', 'Export resolved metadata for artists, albums, or tracks', { q: [
            ['entity', 'string', 1, '"artist", "album", or "track"'],
            ['cursor', 'string', 0, 'Opaque base64 page cursor'],
            ['limit', 'int', 0, 'Page size', '500'],
            ['since', 'string', 0, 'ISO-8601 timestamp — only changes after this point'],
        ] }),
    ] },
    { title: 'Video', desc: 'The full video v1 surface: library, search, wishlist, watchlist, scans, downloads, calendar, and requests.', eps: [
        _ddE('GET', '/video/library', 'What\u2019s in the video library', { q: [
            ['kind', 'string', 0, '"movies" or "shows"'],
            ['search', 'string', 0, 'Title search'],
            ['letter', 'string', 0, 'First-letter filter'],
            ['sort', 'string', 0, 'Sort order'],
            ['status', 'string', 0, 'Status filter'],
            ['genre', 'string', 0, 'Genre filter'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page', '50'],
        ] }),
        _ddE('GET', '/video/library/genres', 'Video library genres'),
        _ddE('GET', '/video/search', 'TMDB multi-search', { q: [
            ['q', 'string', 1, 'Search query'],
        ] }),
        _ddE('GET', '/video/trending', 'Trending titles'),
        _ddE('GET', '/video/wishlist', 'Wishlist items (counts only unless kind is given)', { q: [
            ['kind', 'string', 0, '"movie" or "show" — omit for counts only'],
            ['search', 'string', 0, 'Title search'],
            ['sort', 'string', 0, 'Sort order'],
            ['page', 'int', 0, 'Page number', '1'],
            ['limit', 'int', 0, 'Results per page', '50'],
        ] }),
        _ddE('GET', '/video/wishlist/counts', 'Wishlist counts'),
        _ddE('POST', '/video/wishlist', 'Add a movie or show to the wishlist', { b: [
            ['movie', 'object', 0, '{tmdb_id, title, year?, poster_url?} — one of movie/show required'],
            ['show', 'object', 0, '{tmdb_id, title, …} — one of movie/show required'],
            ['episodes', 'array', 0, 'For shows: [{season_number, episode_number, …}]'],
        ], bx: '{\n  "movie": { "tmdb_id": 278, "title": "The Shawshank Redemption", "year": 1994 }\n}' }),
        _ddE('DELETE', '/video/wishlist', 'Remove from the wishlist', { b: [
            ['scope', 'string', 1, '"movie", "show", "season", or "episode"'],
            ['tmdb_id', 'int', 1, 'TMDB ID'],
            ['season_number', 'int', 0, 'For season/episode scope'],
            ['episode_number', 'int', 0, 'For episode scope'],
        ], bx: '{\n  "scope": "movie",\n  "tmdb_id": 278\n}' }),
        _ddE('GET', '/video/watchlist', 'Watched shows, people, and studios'),
        _ddE('POST', '/video/watchlist', 'Follow a show, person, or studio', { b: [
            ['kind', 'string', 1, '"show", "person", or "studio"'],
            ['tmdb_id', 'int', 1, 'TMDB ID'],
            ['title', 'string', 1, 'Display title'],
            ['poster_url', 'string', 0, 'Poster image URL'],
        ], bx: '{\n  "kind": "show",\n  "tmdb_id": 1396,\n  "title": "Breaking Bad"\n}' }),
        _ddE('DELETE', '/video/watchlist', 'Unfollow', { b: [
            ['kind', 'string', 1, '"show", "person", or "studio"'],
            ['tmdb_id', 'int', 1, 'TMDB ID'],
        ], bx: '{\n  "kind": "show",\n  "tmdb_id": 1396\n}' }),
        _ddE('POST', '/video/scan', 'Request a library scan', { b: [
            ['mode', 'string', 0, '"incremental", "deep", or "full"'],
        ], bx: '{\n  "mode": "incremental"\n}', note: 'Answers 409 if a scan is already running.' }),
        _ddE('GET', '/video/scan/status', 'Scan status'),
        _ddE('GET', '/video/downloads', 'Active video downloads'),
        _ddE('GET', '/video/downloads/status', 'Video download status'),
        _ddE('GET', '/video/downloads/history', 'Video download history'),
        _ddE('GET', '/video/calendar', 'Upcoming and recent episodes/releases', { q: [
            ['start', 'string', 0, 'ISO date'],
            ['end', 'string', 0, 'ISO date'],
        ] }),
        _ddE('GET', '/video/requests', 'List video requests'),
        _ddE('POST', '/video/requests', 'Create a video request', { b: [
            ['kind', 'string', 1, '"movie" or "show"'],
            ['tmdb_id', 'int', 1, 'TMDB ID'],
            ['title', 'string', 1, 'Display title'],
            ['year', 'int', 0, 'Release year'],
            ['poster_url', 'string', 0, 'Poster image URL'],
            ['note', 'string', 0, 'Request note'],
            ['monitor', 'bool', 0, 'Monitor for availability'],
        ], bx: '{\n  "kind": "movie",\n  "tmdb_id": 278,\n  "title": "The Shawshank Redemption"\n}' }),
        _ddE('POST', '/video/requests/{request_id}/approve', 'Approve a video request'),
        _ddE('POST', '/video/requests/{request_id}/deny', 'Deny a video request'),
    ] },
];

// Flat registry (index-stable across page renders) for the try-it runner.
const _ddApiRegistry = [];
DD_API_GROUPS.forEach(g => g.eps.forEach(e => _ddApiRegistry.push(e)));

const _DD_METHOD_COLORS = { GET: '#4ade80', POST: '#60a5fa', PUT: '#fbbf24', PATCH: '#c084fc', DELETE: '#f87171' };
function ddMethodColor(m) { return _DD_METHOD_COLORS[m] || '#9ca3af'; }

function ddBuildExplorer() {
    const root = document.createElement('section');
    root.className = 'dd-api-explorer';
    root.style.cssText = 'margin:36px 0 8px;padding:22px;border:1px solid rgba(255,255,255,0.09);border-radius:14px;background:rgba(0,0,0,0.22);';

    let html = `<h2 style="margin:0 0 6px;font-size:19px;color:#fff;">🧪 API Explorer</h2>` +
        `<p style="margin:0 0 16px;font-size:13.5px;color:rgba(255,255,255,0.6);max-width:72ch;">` +
        `Try every endpoint live against this server — requests run from your browser against ` +
        `<code style="color:#fff;">${_DD_API_BASE}</code> with your key as a Bearer token (or <code style="color:#fff;">?api_key=</code>). ` +
        `An API key acts with <strong style="color:#fff;">admin rights</strong>: POST / PUT / PATCH / DELETE endpoints act on your live library.</p>`;

    // API key bar
    html += `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px;padding:12px 14px;border:1px solid rgba(255,255,255,0.09);border-radius:10px;background:rgba(255,255,255,0.03);">` +
        `<label for="dd-api-key" style="font-size:13px;font-weight:600;color:#fff;white-space:nowrap;">API Key</label>` +
        `<input type="password" id="dd-api-key" placeholder="sk_…" autocomplete="off" spellcheck="false" ` +
        `style="flex:1;min-width:180px;background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.14);color:#fff;border-radius:8px;padding:8px 12px;font-size:13px;font-family:ui-monospace,Menlo,monospace;outline:none;">` +
        `<span id="dd-api-key-status" style="font-size:12.5px;color:rgba(255,255,255,0.5);white-space:nowrap;">Enter key to test endpoints</span>` +
        `</div>` +
        `<p style="margin:-10px 0 18px;font-size:12px;color:rgba(255,255,255,0.4);">The key is stored only in this browser (localStorage) and sent only to this server. Mint one in <strong style="color:rgba(255,255,255,0.65);">Settings → API Keys</strong>, or use the bootstrap endpoint below on a fresh install.</p>`;

    DD_API_GROUPS.forEach((group) => {
        html += `<h3 style="margin:22px 0 4px;font-size:15px;color:#fff;">${escHtml(group.title)}</h3>` +
            `<p style="margin:0 0 10px;font-size:12.5px;color:rgba(255,255,255,0.5);">${escHtml(group.desc)}</p>`;
        group.eps.forEach((ep) => {
            const idx = _ddApiRegistry.indexOf(ep);
            const color = ddMethodColor(ep.m);
            html += `<div class="dd-api-ep" data-dd-ep="${idx}" style="border:1px solid rgba(255,255,255,0.08);border-radius:10px;margin:0 0 8px;overflow:hidden;background:rgba(255,255,255,0.02);">` +
                `<div class="dd-api-ep-head" data-dd-ep-head="${idx}" role="button" tabindex="0" style="display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;user-select:none;">` +
                `<span style="font-size:11px;font-weight:700;letter-spacing:0.04em;color:${color};border:1px solid ${color}55;background:${color}14;padding:3px 8px;border-radius:6px;min-width:52px;text-align:center;">${ep.m}</span>` +
                `<code style="font-size:13px;color:#fff;">${escHtml(_DD_API_BASE + ep.p)}</code>` +
                `<span style="font-size:12.5px;color:rgba(255,255,255,0.55);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escHtml(ep.d)}</span>` +
                `<span class="dd-api-ep-arrow" data-dd-ep-arrow="${idx}" style="margin-left:auto;color:rgba(255,255,255,0.4);font-size:11px;transition:transform .15s;">▶</span>` +
                `</div>` +
                `<div class="dd-api-ep-body" data-dd-ep-body="${idx}" style="display:none;border-top:1px solid rgba(255,255,255,0.07);padding:14px;">` +
                ddBuildEpBody(ep, idx) +
                `</div></div>`;
        });
    });

    root.innerHTML = html;

    // Expand/collapse
    root.querySelectorAll('[data-dd-ep-head]').forEach((head) => {
        const idx = head.dataset.ddEpHead;
        const toggle = () => {
            const body = root.querySelector(`[data-dd-ep-body="${idx}"]`);
            const arrow = root.querySelector(`[data-dd-ep-arrow="${idx}"]`);
            const open = body.style.display === 'none';
            body.style.display = open ? '' : 'none';
            if (arrow) arrow.style.transform = open ? 'rotate(90deg)' : '';
        };
        head.addEventListener('click', toggle);
        head.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    });

    // API key bar: restore, persist, status
    const keyInput = root.querySelector('#dd-api-key');
    const keyStatus = root.querySelector('#dd-api-key-status');
    const paintKeyStatus = () => {
        const v = keyInput.value.trim();
        if (!v) { keyStatus.textContent = 'Enter key to test endpoints'; keyStatus.style.color = 'rgba(255,255,255,0.5)'; }
        else if (v.startsWith('sk_')) { keyStatus.textContent = 'Key saved ✓'; keyStatus.style.color = '#4ade80'; }
        else { keyStatus.textContent = 'Keys start with sk_'; keyStatus.style.color = '#fbbf24'; }
    };
    try {
        const saved = localStorage.getItem(_DD_API_KEY_LS);
        if (saved) keyInput.value = saved;
    } catch (e) {}
    paintKeyStatus();
    keyInput.addEventListener('input', () => {
        try { localStorage.setItem(_DD_API_KEY_LS, keyInput.value.trim()); } catch (e) {}
        paintKeyStatus();
    });

    return root;
}

function ddBuildEpBody(ep, idx) {
    let html = `<p style="margin:0 0 12px;font-size:13px;color:rgba(255,255,255,0.7);">${escHtml(ep.d)}</p>`;
    if (ep.note) {
        html += `<p style="margin:0 0 12px;font-size:12.5px;color:#fbbf24;">⚠️ ${escHtml(ep.note)}</p>`;
    }
    const tbl = (rows, title) => {
        if (!rows || !rows.length) return '';
        return `<p style="margin:12px 0 6px;font-size:12px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:rgba(255,255,255,0.45);">${title}</p>` +
            `<table style="width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:4px;">` +
            rows.map(r =>
                `<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">` +
                `<td style="padding:6px 10px 6px 0;color:#fff;font-family:ui-monospace,Menlo,monospace;white-space:nowrap;">${escHtml(r[0])}${r[2] ? ' <span style="color:#f87171;font-size:11px;">required</span>' : ''}</td>` +
                `<td style="padding:6px 10px 6px 0;color:rgba(255,255,255,0.4);font-family:ui-monospace,Menlo,monospace;">${escHtml(r[1])}</td>` +
                `<td style="padding:6px 0;color:rgba(255,255,255,0.6);">${escHtml(r[3])}${r[4] ? ` <span style="color:rgba(255,255,255,0.3);">(default: ${escHtml(r[4])})</span>` : ''}</td>` +
                `</tr>`).join('') + `</table>`;
    };
    html += tbl(ep.q, ep.m === 'GET' ? 'Query parameters' : 'Parameters');
    html += tbl(ep.b, 'Request body (JSON)');

    // Path parameter inputs
    const pathParams = [];
    const pm = ep.p.match(/\{([^}]+)\}/g);
    if (pm) pm.forEach(m2 => pathParams.push(m2.replace(/[{}]/g, '')));
    if (pathParams.length) {
        html += `<p style="margin:12px 0 6px;font-size:12px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:rgba(255,255,255,0.45);">Try it — path</p>`;
        html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px;">` +
            pathParams.map(pp =>
                `<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:rgba(255,255,255,0.6);">${escHtml(pp)}` +
                `<input type="text" id="dd-xp-${idx}-${escHtml(pp)}" placeholder="${escHtml(pp)}" style="width:130px;background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.14);color:#fff;border-radius:6px;padding:6px 8px;font-size:12.5px;outline:none;"></label>`
            ).join('') + `</div>`;
    }

    // Query parameter inputs for GET
    if (ep.m === 'GET' && ep.q.length) {
        html += `<p style="margin:12px 0 6px;font-size:12px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:rgba(255,255,255,0.45);">Try it — query</p>`;
        html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px;">` +
            ep.q.map(p =>
                `<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:rgba(255,255,255,0.6);">${escHtml(p[0])}${p[2] ? '<span style="color:#f87171;">*</span>' : ''}` +
                `<input type="text" id="dd-xq-${idx}-${escHtml(p[0])}" placeholder="${escHtml(p[4] || '')}" style="width:130px;background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.14);color:#fff;border-radius:6px;padding:6px 8px;font-size:12.5px;outline:none;"></label>`
            ).join('') + `</div>`;
    }

    // Body textarea
    if (ep.b.length) {
        html += `<p style="margin:12px 0 6px;font-size:12px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:rgba(255,255,255,0.45);">Try it — body</p>` +
            `<textarea id="dd-xb-${idx}" spellcheck="false" style="width:100%;box-sizing:border-box;min-height:90px;background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.14);color:#e5e7eb;border-radius:8px;padding:10px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;resize:vertical;outline:none;">${escHtml(ep.bx || '{}')}</textarea>`;
    }

    html += `<div style="margin-top:10px;">` +
        `<button type="button" class="docs-search-trigger" id="dd-xt-${idx}" data-dd-try="${idx}" style="width:auto;padding:8px 18px;font-weight:600;">▶ Send</button>` +
        (ep.na ? `<span style="margin-left:10px;font-size:12px;color:#4ade80;">no auth needed</span>` : '') +
        `</div>` +
        `<div id="dd-xr-${idx}" style="margin-top:10px;"></div>`;
    return html;
}

// Live try-it runner. Same-origin fetch to the running SoulSync server with
// the stored key as a Bearer token. Shows status + pretty response, or the
// network/validation error.
window._ddApiTryIt = async function (idx) {
    const ep = _ddApiRegistry[idx];
    if (!ep) return;
    const btn = document.getElementById('dd-xt-' + idx);
    const resultDiv = document.getElementById('dd-xr-' + idx);
    const keyInput = document.getElementById('dd-api-key');
    const apiKey = keyInput ? keyInput.value.trim() : '';

    if (!apiKey && !ep.na) {
        resultDiv.innerHTML = `<div style="padding:10px 12px;border-radius:8px;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.25);color:#f87171;font-size:13px;">Enter your API key above first</div>`;
        return;
    }

    // Path params
    let path = ep.p;
    const pm = path.match(/\{([^}]+)\}/g) || [];
    for (const m2 of pm) {
        const name = m2.replace(/[{}]/g, '');
        const input = document.getElementById(`dd-xp-${idx}-${name}`);
        const val = input ? input.value.trim() : '';
        if (!val) {
            resultDiv.innerHTML = `<div style="padding:10px 12px;border-radius:8px;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.25);color:#f87171;font-size:13px;">Fill in path parameter: ${escHtml(name)}</div>`;
            return;
        }
        path = path.replace(m2, encodeURIComponent(val));
    }

    // Query string for GET
    let qs = '';
    if (ep.m === 'GET' && ep.q.length) {
        const parts = [];
        ep.q.forEach(p => {
            const input = document.getElementById(`dd-xq-${idx}-${p[0]}`);
            const val = input ? input.value.trim() : '';
            if (val) parts.push(encodeURIComponent(p[0]) + '=' + encodeURIComponent(val));
        });
        if (parts.length) qs = '?' + parts.join('&');
    }

    // Body
    let body = null;
    if (ep.b.length) {
        const bodyEl = document.getElementById('dd-xb-' + idx);
        const raw = bodyEl ? bodyEl.value.trim() : '';
        if (raw) {
            try { JSON.parse(raw); } catch (e) {
                resultDiv.innerHTML = `<div style="padding:10px 12px;border-radius:8px;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.25);color:#f87171;font-size:13px;">Invalid JSON in request body: ${escHtml(e.message)}</div>`;
                return;
            }
            body = raw;
        }
    }

    const url = _DD_API_BASE + path + qs;
    const headers = {};
    if (!ep.na) headers['Authorization'] = 'Bearer ' + apiKey;
    if (body !== null) headers['Content-Type'] = 'application/json';

    if (btn) { btn.disabled = true; btn.textContent = '⏳ Sending…'; }
    resultDiv.innerHTML = '';

    const start = performance.now();
    try {
        const resp = await fetch(url, { method: ep.m, headers, body });
        const elapsed = Math.round(performance.now() - start);
        let text;
        try { text = await resp.text(); } catch (e) { text = '(empty response)'; }
        let pretty = text;
        try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (e) {}
        const ok = resp.status < 300;
        const color = ok ? '#4ade80' : resp.status < 500 ? '#fbbf24' : '#f87171';
        resultDiv.innerHTML =
            `<div style="border:1px solid rgba(255,255,255,0.1);border-radius:8px;overflow:hidden;">` +
            `<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:rgba(255,255,255,0.04);">` +
            `<span style="font-size:12.5px;font-weight:700;color:${color};">${resp.status} ${escHtml(resp.statusText)}</span>` +
            `<span style="margin-left:auto;font-size:12px;color:rgba(255,255,255,0.45);font-family:ui-monospace,Menlo,monospace;">${elapsed}ms</span>` +
            `</div>` +
            `<pre style="margin:0;padding:12px;max-height:320px;overflow:auto;background:rgba(0,0,0,0.35);font-size:12px;line-height:1.5;color:#e5e7eb;font-family:ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word;">${ddHighlightJson(escHtml(pretty))}</pre>` +
            `</div>`;
    } catch (err) {
        resultDiv.innerHTML = `<div style="padding:10px 12px;border-radius:8px;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.25);font-size:13px;">` +
            `<span style="font-weight:700;color:#f87171;">Network error</span><br>` +
            `<span style="color:rgba(255,255,255,0.65);font-size:12.5px;">${escHtml(err && err.message ? err.message : String(err))}</span></div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '▶ Send'; }
    }
};

function ddHighlightJson(escaped) {
    // escaped is already HTML-escaped; wrap JSON keys/strings/numbers in spans.
    return escaped.replace(/(&quot;([^&]|&(?!quot;))*?&quot;)(\s*:)?/g, (m, str, _inner, colon) =>
        colon ? `<span style="color:#7dd3fc;">${str}</span>:` : `<span style="color:#a5d6a7;">${str}</span>`
    );
}

function mountApiExplorer(content) {
    const explorer = ddBuildExplorer();
    const anchor = content.querySelector('.dd-prevnext');
    if (anchor) content.insertBefore(explorer, anchor);
    else content.appendChild(explorer);
    explorer.querySelectorAll('[data-dd-try]').forEach((btn) => {
        btn.addEventListener('click', () => window._ddApiTryIt(parseInt(btn.dataset.ddTry, 10)));
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
    initDebugPanel();
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
