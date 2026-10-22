import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Badge, EmptyState, Notice, Show, Skeleton, Spinner } from './primitives';

describe('Show', () => {
  it('renders children when the condition is true', () => {
    render(
      <Show when={true}>
        <span>Visible</span>
      </Show>,
    );

    expect(screen.getByText('Visible')).toBeInTheDocument();
  });

  it('renders fallback when the condition is false', () => {
    render(
      <Show fallback={<span>Hidden</span>} when={false}>
        <span>Visible</span>
      </Show>,
    );

    expect(screen.getByText('Hidden')).toBeInTheDocument();
    expect(screen.queryByText('Visible')).not.toBeInTheDocument();
  });

  it('supports render-prop children', () => {
    render(<Show when="Ada">{(name) => <span>{name}</span>}</Show>);

    expect(screen.getByText('Ada')).toBeInTheDocument();
  });
});

describe('Notice', () => {
  it('renders as a note by default', () => {
    render(<Notice>Fallback message</Notice>);

    expect(screen.getByText('Fallback message')).toHaveAttribute('role', 'note');
    expect(screen.getByText('Fallback message')).toHaveAttribute('data-tone', 'info');
  });

  it('supports tone overrides', () => {
    render(
      <Notice tone="warning">
        <span>Provider fallback</span>
      </Notice>,
    );

    expect(screen.getByRole('note')).toHaveAttribute('data-tone', 'warning');
  });
});

describe('Badge', () => {
  it('renders with neutral styling by default', () => {
    render(<Badge>12</Badge>);

    expect(screen.getByText('12')).toHaveAttribute('data-slot', 'badge');
    expect(screen.getByText('12')).toHaveAttribute('data-tone', 'neutral');
  });

  it('supports tone overrides', () => {
    render(<Badge tone="warning">12</Badge>);

    expect(screen.getByText('12')).toHaveAttribute('data-tone', 'warning');
  });
});

describe('Spinner', () => {
  it('is decorative, 16px and accent-toned by default', () => {
    const { container } = render(<Spinner />);
    const spinner = container.querySelector('[data-slot="spinner"]');

    expect(spinner).toHaveAttribute('aria-hidden', 'true');
    expect(spinner).toHaveAttribute('data-tone', 'accent');
    expect(spinner).toHaveStyle({ '--spinner-size': '16px', '--spinner-ring': '2px' });
  });

  it('becomes a labelled status when given a label', () => {
    render(<Spinner label="Searching" />);

    expect(screen.getByRole('status', { name: 'Searching' })).not.toHaveAttribute('aria-hidden');
  });

  it('supports size and tone overrides', () => {
    render(<Spinner label="Loading" size={32} tone="tinted" />);

    const spinner = screen.getByRole('status', { name: 'Loading' });
    expect(spinner).toHaveAttribute('data-tone', 'tinted');
    expect(spinner).toHaveStyle({ '--spinner-size': '32px', '--spinner-ring': '3px' });
  });
});

describe('Skeleton', () => {
  it('renders a hidden placeholder that keeps the caller class', () => {
    const { container } = render(<Skeleton className="card" />);
    const skeleton = container.querySelector('[data-slot="skeleton"]');

    expect(skeleton).toHaveAttribute('aria-hidden', 'true');
    expect(skeleton).toHaveClass('card');
  });
});

describe('EmptyState', () => {
  it('renders the title as a heading with its description', () => {
    render(<EmptyState title="No issues found" description="Try adjusting your filters" />);

    expect(screen.getByRole('heading', { name: 'No issues found' })).toBeInTheDocument();
    expect(screen.getByText('Try adjusting your filters')).toHaveAttribute(
      'data-slot',
      'empty-state-description',
    );
  });

  it('hides the icon from assistive tech and renders children as actions', () => {
    const { container } = render(
      <EmptyState icon="🔍" title="Nothing here">
        <button type="button">Retry</button>
      </EmptyState>,
    );

    expect(container.querySelector('[data-slot="empty-state-icon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('leaves out the slots it is not given', () => {
    const { container } = render(<EmptyState title="Empty" />);

    expect(container.querySelector('[data-slot="empty-state-icon"]')).toBeNull();
    expect(container.querySelector('[data-slot="empty-state-description"]')).toBeNull();
  });
});
