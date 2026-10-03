import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { type ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

import { VideoRail } from './video-rail';

const wrap = (ui: ReactNode) =>
  render(<QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>);

const ARTISTS = [
  { key: '1', name: 'TOOL', image: '/tool.jpg', reason: 'Because you have Deftones', href: '/a/1' },
  { key: '2', name: 'Aaliyah', image: '/a.jpg', href: '/a/2' },
];

describe('VideoRail', () => {
  it('is one tall card per artist, each linking to its artist', () => {
    const { container } = wrap(
      <VideoRail title="Watch" subtitle="Point at one" artists={ARTISTS} videosOn />,
    );
    const cards = container.querySelectorAll('.dsc-promo--portrait');
    expect(cards).toHaveLength(2);
    expect(screen.getByText('Because you have Deftones')).toBeInTheDocument();
    expect(screen.getAllByText('View artist')[1].closest('a')).toHaveAttribute('href', '/a/2');
    // no reason: the card still says who it's for
    expect(screen.getByText('For you')).toBeInTheDocument();
  });

  it('is not there with no artists', () => {
    const { container } = wrap(<VideoRail title="Watch" subtitle="" artists={[]} videosOn />);
    expect(container.querySelector('.dsc-video-rail')).toBeNull();
  });
});
