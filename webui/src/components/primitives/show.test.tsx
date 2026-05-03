import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Show } from './show';

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
