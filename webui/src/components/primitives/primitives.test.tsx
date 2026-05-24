import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Notice, Show } from './primitives';

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
