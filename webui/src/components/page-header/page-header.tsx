import type { ReactNode } from 'react';

import clsx from 'clsx';

/**
 * The page frame: one width, one header, one row of pills, one kind of
 * section heading, for every page but the dashboard.
 *
 * Each page used to bring its own: titles ran from 22px to 34px, widths from
 * 1440px to none, some had a hero block and some an accent bar with an icon.
 * These components emit the `.pgf-*` classes in static/style.css, which is
 * the one place the look is defined, so a vanilla page can write the same
 * classes into its own markup and match.
 *
 * What makes a page itself (the wishlist's nebula, the library grid) goes
 * inside the frame and is none of its business.
 */

/**
 * The page: the floating panel every page sits in, and inside it a column
 * 1440px at most, centred, with the same padding everywhere.
 *
 * `id`, `className` and data attributes go on the panel, where the old pages
 * had theirs. A page that cannot use this component writes
 * `page-shell pgf-shell` on its panel and `pgf-page` on the column.
 */
export function PageFrame({
  children,
  className,
  id,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  id?: string;
} & Record<`data-${string}`, string | undefined>) {
  return (
    <div className={clsx('page-shell pgf-shell', className)} id={id} {...rest}>
      <div className="pgf-page">{children}</div>
    </div>
  );
}

/**
 * The page name, one line that says where things stand, and what you can do.
 *
 * `subtitle` is that line: put the numbers that matter in <b> and they are
 * drawn white. `actions` sits on the right: one `pgf-btn pgf-btn--main` for
 * the thing the page is for, plain `pgf-btn` for the rest.
 *
 * `icon` is accepted and not drawn. The old header carried a 48px picture
 * beside the title; the frame has none, and the callers that still pass one
 * do not have to change in the same commit.
 */
export function PageHeader({
  actions,
  className,
  id,
  subtitle,
  title,
}: {
  actions?: ReactNode;
  className?: string;
  /** @deprecated the frame's header has no icon */
  icon?: ReactNode;
  id?: string;
  subtitle?: ReactNode;
  title: ReactNode;
}) {
  return (
    <header className={clsx('pgf-head', className)} id={id}>
      <div className="pgf-head-main">
        <h1 className="pgf-title">{title}</h1>
        {subtitle ? <p className="pgf-status">{subtitle}</p> : null}
      </div>
      {actions ? <div className="pgf-actions">{actions}</div> : null}
    </header>
  );
}

export interface PagePill<T extends string = string> {
  id: T;
  label: ReactNode;
  /** how many it holds; left out when there is nothing to count */
  count?: number | null;
  title?: string;
}

/**
 * The page's views, as one row of pills with their counts.
 *
 * `tabs` (the default) is for views that swap what the page shows; `filters`
 * is for pills that narrow one list, where several controls may be pressed.
 */
export function PagePills<T extends string>({
  ariaLabel,
  className,
  kind = 'tabs',
  onChange,
  pills,
  value,
}: {
  ariaLabel: string;
  className?: string;
  kind?: 'tabs' | 'filters';
  onChange: (id: T) => void;
  pills: ReadonlyArray<PagePill<T>>;
  value: T;
}) {
  const tabs = kind === 'tabs';
  return (
    <div
      className={clsx('pgf-pills', className)}
      role={tabs ? 'tablist' : 'group'}
      aria-label={ariaLabel}
    >
      {pills.map((pill) => {
        const on = pill.id === value;
        return (
          <button
            key={pill.id}
            type="button"
            className="pgf-pill"
            role={tabs ? 'tab' : undefined}
            aria-selected={tabs ? on : undefined}
            aria-pressed={tabs ? undefined : on}
            title={pill.title}
            onClick={() => onChange(pill.id)}
          >
            {pill.label}
            {pill.count != null ? (
              <span className="pgf-pill-count">{pill.count.toLocaleString()}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** The row under the header: the views on the left, finding and sorting on the right. */
export function PageBar({
  children,
  className,
  end,
}: {
  children?: ReactNode;
  className?: string;
  /** search, sort and filters: drawn at the right end of the row */
  end?: ReactNode;
}) {
  return (
    <div className={clsx('pgf-bar', className)}>
      {children}
      {end ? <div className="pgf-bar-end">{end}</div> : null}
    </div>
  );
}

/** The frame's search field. */
export function PageSearch({
  ariaLabel,
  onChange,
  placeholder,
  value,
}: {
  ariaLabel?: string;
  onChange: (value: string) => void;
  placeholder: string;
  value: string;
}) {
  return (
    <label className="pgf-search">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.9}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-3.5-3.5" />
      </svg>
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

/** A group on the page: what it is, and how many or what about it. */
export function PageSection({
  children,
  className,
  note,
  title,
}: {
  children: ReactNode;
  className?: string;
  note?: ReactNode;
  title: ReactNode;
}) {
  return (
    <section className={clsx('pgf-section', className)}>
      <div className="pgf-section-head">
        <h2 className="pgf-section-title">{title}</h2>
        {note ? <span className="pgf-section-note">{note}</span> : null}
      </div>
      {children}
    </section>
  );
}
