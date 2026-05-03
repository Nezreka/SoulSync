import type { ReactNode } from 'react';

type ShowChildren<T> = ReactNode | ((value: NonNullable<T>) => ReactNode);

export function Show<T>({
  fallback = null,
  children,
  when,
}: {
  children: ShowChildren<T>;
  fallback?: ReactNode;
  when: T;
}) {
  if (!when) {
    return <>{fallback}</>;
  }

  if (typeof children === 'function') {
    return <>{(children as (value: NonNullable<T>) => ReactNode)(when as NonNullable<T>)}</>;
  }

  return <>{children}</>;
}
