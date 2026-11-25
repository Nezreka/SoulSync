/**
 * The docs' markdown, as React (no innerHTML): the subset docs.js renders
 * (headings, paragraphs, lists, ::: steps and ::: cards, > [!TIP] callouts,
 * tables, code, images, links). Links to another article (#gs-docker) open
 * it in the sheet; anything else opens a tab.
 *
 * Headings drop two levels: the sheet's title is the h2, the article's is
 * the h3, so a body ## is an h4.
 */

import type { ReactNode } from 'react';

import { Fragment } from 'react';

import s from '../article.module.css';

export interface MarkdownProps {
  source: string;
  /** an in-docs link: the article id it points at */
  onArticle: (id: string) => void;
}

// ── inline ────────────────────────────────────────────────────────────

const INLINE =
  /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|`([^`\n]+)`|\*\*([^*]+)\*\*|(^|[\s(])\*([^*\n]+)\*/g;

export function imageSrc(src: string): string {
  return src.startsWith('/') || src.startsWith('http') ? src : `/static/docs/${src}`;
}

function safeHref(url: string): string | null {
  if (/^(https?:)?\/\//i.test(url) || url.startsWith('mailto:') || url.startsWith('/')) return url;
  return null;
}

export function inline(text: string, onArticle: (id: string) => void, key = 'i'): ReactNode[] {
  const out: ReactNode[] = [];
  let at = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    const start = m.index ?? 0;
    const lead = m[7] ?? '';
    if (start + lead.length > at) out.push(text.slice(at, start + lead.length));
    at = start + m[0].length;
    const k = `${key}-${n++}`;
    if (m[2] !== undefined) {
      out.push(
        <img
          key={k}
          className={s.inlineImg}
          src={imageSrc(m[2])}
          alt={m[1]}
          loading="lazy"
          onError={(e) => (e.currentTarget.style.display = 'none')}
        />,
      );
    } else if (m[4] !== undefined) {
      const url = m[4];
      const label = inline(m[3], onArticle, k);
      if (url.startsWith('#') && !url.startsWith('#/')) {
        const id = url.slice(1).split('--')[0];
        out.push(
          <a
            key={k}
            href={`#docs/${id}`}
            className={s.link}
            onClick={(e) => {
              e.preventDefault();
              onArticle(id);
            }}
          >
            {label}
          </a>,
        );
      } else {
        const href = safeHref(url);
        out.push(
          href ? (
            <a key={k} href={href} className={s.link} target="_blank" rel="noopener noreferrer">
              {label}
            </a>
          ) : (
            <Fragment key={k}>{label}</Fragment>
          ),
        );
      }
    } else if (m[5] !== undefined) {
      out.push(
        <code key={k} className={s.code}>
          {m[5]}
        </code>,
      );
    } else if (m[6] !== undefined) {
      out.push(<strong key={k}>{inline(m[6], onArticle, k)}</strong>);
    } else if (m[8] !== undefined) {
      out.push(<em key={k}>{m[8]}</em>);
    }
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

// ── blocks ────────────────────────────────────────────────────────────

const CALLOUTS: Record<string, { tone: string; label: string }> = {
  TIP: { tone: 'tip', label: 'Tip' },
  NOTE: { tone: 'note', label: 'Note' },
  INFO: { tone: 'note', label: 'Note' },
  IMPORTANT: { tone: 'note', label: 'Important' },
  WARNING: { tone: 'warn', label: 'Warning' },
  DANGER: { tone: 'danger', label: 'Danger' },
};

const BLOCK_START = /^(#{1,4}\s|```|:::\s*|>\s?|\|.*\|\s*$|---\s*$|\s*[-*]\s+|\s*\d+[.)]\s+)/;

export function Markdown({ source, onArticle }: MarkdownProps) {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  const md = (text: string, key: string) => inline(text, onArticle, key);
  let i = 0;
  let b = 0;
  const key = () => `b${b++}`;

  while (i < lines.length) {
    const line = lines[i];

    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++]);
      i += 1;
      blocks.push(
        <pre key={key()} className={s.pre} data-lang={fence[1] || undefined}>
          <code>{buf.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    const box = line.match(/^:::\s*(steps|cards)\s*$/);
    if (box) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !/^:::\s*$/.test(lines[i])) buf.push(lines[i++]);
      i += 1;
      const k = key();
      if (box[1] === 'steps') {
        const steps: string[] = [];
        for (const l of buf) {
          const m = l.match(/^\s*\d+[.)]\s+(.*)$/);
          if (m) steps.push(m[1]);
          else if (steps.length && l.trim()) steps[steps.length - 1] += ` ${l.trim()}`;
        }
        blocks.push(
          <ol key={k} className={s.steps}>
            {steps.map((st, n) => (
              <li key={n}>{md(st, `${k}-${n}`)}</li>
            ))}
          </ol>,
        );
      } else {
        const cards: { title: string; body: string[] }[] = [];
        for (const l of buf) {
          const m = l.match(/^###\s+(.*)$/);
          if (m) cards.push({ title: m[1].trim(), body: [] });
          else if (cards.length && l.trim()) cards[cards.length - 1].body.push(l.trim());
        }
        blocks.push(
          <ul key={k} className={s.cards}>
            {cards.map((c, n) => {
              // the docs lead each card with an emoji: the sheet keeps the words
              const title = c.title.replace(/^[^\p{L}\p{N}]+\s*/u, '');
              return (
                <li key={n} className={s.card}>
                  <strong>{md(title, `${k}-${n}t`)}</strong>
                  <span>{md(c.body.join(' '), `${k}-${n}b`)}</span>
                </li>
              );
            })}
          </ul>,
        );
      }
      continue;
    }

    const head = line.match(/^(#{1,4})\s+(.*)$/);
    if (head) {
      const level = head[1].length;
      const k = key();
      const content = md(head[2].trim(), k);
      // a body # repeats the title the header already shows
      if (level === 1) {
        i += 1;
        continue;
      }
      blocks.push(
        level === 2 ? (
          <h4 key={k} className={s.h2}>
            {content}
          </h4>
        ) : (
          <h5 key={k} className={s.h3}>
            {content}
          </h5>
        ),
      );
      i += 1;
      continue;
    }

    if (/^---\s*$/.test(line)) {
      blocks.push(<hr key={key()} className={s.hr} />);
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
      const k = key();
      const first = buf[0].match(/^\[!(TIP|NOTE|INFO|IMPORTANT|WARNING|DANGER)\]\s*(.*)$/i);
      if (first) {
        const meta = CALLOUTS[first[1].toUpperCase()] ?? CALLOUTS.NOTE;
        const paras = [first[2].trim(), ...buf.slice(1)].filter((p) => p.trim());
        blocks.push(
          <aside key={k} className={s.callout} data-tone={meta.tone}>
            <span className={s.calloutLabel}>{meta.label}</span>
            {paras.map((p, n) => (
              <p key={n}>{md(p, `${k}-${n}`)}</p>
            ))}
          </aside>,
        );
      } else {
        blocks.push(
          <blockquote key={k} className={s.quote}>
            {buf
              .filter((p) => p.trim())
              .map((p, n) => (
                <p key={n}>{md(p, `${k}-${n}`)}</p>
              ))}
          </blockquote>,
        );
      }
      continue;
    }

    if (
      /^\|.*\|\s*$/.test(line) &&
      i + 1 < lines.length &&
      /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])
    ) {
      const cells = (l: string) =>
        l
          .trim()
          .replace(/^\||\|$/g, '')
          .split('|')
          .map((c) => c.trim());
      const headers = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      const k = key();
      blocks.push(
        <div key={k} className={s.tableWrap} tabIndex={0} role="region" aria-label="Table">
          <table className={s.table}>
            <thead>
              <tr>
                {headers.map((h, n) => (
                  <th key={n}>{md(h, `${k}-h${n}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, rn) => (
                <tr key={rn}>
                  {r.map((c, cn) => (
                    <td key={cn}>{md(c, `${k}-${rn}-${cn}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const bullet = /^\s*[-*]\s+/;
    const numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line);
      const re = ordered ? numbered : bullet;
      const items: string[] = [];
      while (i < lines.length && re.test(lines[i])) items.push(lines[i++].replace(re, ''));
      const k = key();
      const kids = items.map((it, n) => <li key={n}>{md(it, `${k}-${n}`)}</li>);
      blocks.push(
        ordered ? (
          <ol key={k} className={s.list}>
            {kids}
          </ol>
        ) : (
          <ul key={k} className={s.list}>
            {kids}
          </ul>
        ),
      );
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const image = line.trim().match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/);
    if (image) {
      blocks.push(
        <figure key={key()} className={s.figure}>
          <img
            src={imageSrc(image[2])}
            alt={image[1]}
            loading="lazy"
            onError={(e) => {
              const fig = e.currentTarget.closest('figure');
              if (fig) fig.hidden = true;
            }}
          />
          {image[1] ? <figcaption>{image[1]}</figcaption> : null}
        </figure>,
      );
      i += 1;
      continue;
    }

    const buf = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !BLOCK_START.test(lines[i])) {
      buf.push(lines[i++].trim());
    }
    const k = key();
    blocks.push(
      <p key={k} className={s.p}>
        {md(buf.join(' '), k)}
      </p>,
    );
  }

  return <>{blocks}</>;
}
