import clsx from 'clsx';
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type CSSProperties,
  type ReactNode,
} from 'react';

import styles from './primitives.module.css';

type ShowChildren<T> = ReactNode | ((value: NonNullable<T>) => ReactNode);

export interface ShowProps<T> {
  children: ShowChildren<T>;
  fallback?: ReactNode;
  when: T;
}

export function Show<T>({ fallback = null, children, when }: ShowProps<T>) {
  if (!when) {
    return <>{fallback}</>;
  }

  if (typeof children === 'function') {
    return <>{(children as (value: NonNullable<T>) => ReactNode)(when as NonNullable<T>)}</>;
  }

  return <>{children}</>;
}

type BaseBadgeProps = ComponentPropsWithoutRef<'span'>;

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export type BadgeProps = Omit<BaseBadgeProps, 'className'> & {
  className?: string;
  tone?: BadgeTone;
};

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  { className, tone = 'neutral', ...props },
  ref,
) {
  return (
    <span
      ref={ref}
      className={clsx(styles.badge, className)}
      data-slot="badge"
      data-tone={tone}
      {...props}
    />
  );
});

export type NoticeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export type NoticeProps = Omit<ComponentPropsWithoutRef<'div'>, 'className'> & {
  className?: string;
  tone?: NoticeTone;
};

export const Notice = forwardRef<HTMLDivElement, NoticeProps>(function Notice(
  { className, tone = 'info', role = 'note', ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      className={clsx(styles.notice, className)}
      data-tone={tone}
      role={role}
      {...props}
    />
  );
});

export type SpinnerTone = 'accent' | 'soft' | 'tinted' | 'light';

export type SpinnerProps = Omit<ComponentPropsWithoutRef<'span'>, 'children' | 'className'> & {
  className?: string;
  /** Accessible name. Without one the spinner is decorative (aria-hidden). */
  label?: string;
  /** Diameter in px. The ring is 3px from 28px up, 2px below. */
  size?: number;
  tone?: SpinnerTone;
};

export const Spinner = forwardRef<HTMLSpanElement, SpinnerProps>(function Spinner(
  { className, label, size = 16, style, tone = 'accent', ...props },
  ref,
) {
  return (
    <span
      ref={ref}
      className={clsx(styles.spinner, className)}
      data-slot="spinner"
      data-tone={tone}
      {...(label ? { role: 'status', 'aria-label': label } : { 'aria-hidden': true })}
      style={
        {
          '--spinner-size': `${size}px`,
          '--spinner-ring': `${size >= 28 ? 3 : 2}px`,
          ...style,
        } as CSSProperties
      }
      {...props}
    />
  );
});

export type SkeletonProps = Omit<ComponentPropsWithoutRef<'div'>, 'children' | 'className'> & {
  className?: string;
};

/** A shimmering placeholder. Size and shape come from `className`. */
export const Skeleton = forwardRef<HTMLDivElement, SkeletonProps>(function Skeleton(
  { className, ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      className={clsx(styles.skeleton, className)}
      data-slot="skeleton"
      aria-hidden="true"
      {...props}
    />
  );
});

export type EmptyStateProps = Omit<ComponentPropsWithoutRef<'div'>, 'className' | 'title'> & {
  className?: string;
  /** Decorative glyph above the title, e.g. an emoji. */
  icon?: ReactNode;
  title?: ReactNode;
  /** One line (or paragraph) under the title. */
  description?: ReactNode;
};

/** The "nothing here" block: icon, title, description, then any children (actions). */
export const EmptyState = forwardRef<HTMLDivElement, EmptyStateProps>(function EmptyState(
  { children, className, description, icon, title, ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      className={clsx(styles.emptyState, className)}
      data-slot="empty-state"
      {...props}
    >
      {icon ? (
        <div className={styles.emptyStateIcon} data-slot="empty-state-icon" aria-hidden="true">
          {icon}
        </div>
      ) : null}
      {title ? (
        <h3 className={styles.emptyStateTitle} data-slot="empty-state-title">
          {title}
        </h3>
      ) : null}
      {description ? (
        <p className={styles.emptyStateDescription} data-slot="empty-state-description">
          {description}
        </p>
      ) : null}
      {children}
    </div>
  );
});
