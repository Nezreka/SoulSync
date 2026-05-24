import clsx from 'clsx';
import { forwardRef, type ComponentPropsWithoutRef, type ReactNode } from 'react';

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
