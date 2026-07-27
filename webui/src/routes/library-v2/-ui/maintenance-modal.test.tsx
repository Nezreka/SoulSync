import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MaintenanceModal } from './library-v2-page';

describe('Library v2 maintenance tools', () => {
  it('uses understandable names and makes artist versus library scope explicit', () => {
    render(<MaintenanceModal artistId={7} artistName="Massive Attack" onClose={vi.fn()} />);

    expect(screen.getByRole('dialog', { name: 'Library Health & Repair' })).toBeInTheDocument();
    expect(screen.getByText('Catalog & monitoring')).toBeInTheDocument();
    expect(screen.getByText('Artist files & tags')).toBeInTheDocument();
    expect(screen.getByText('Library-wide scans')).toBeInTheDocument();
    expect(screen.getAllByText('Entire library')).toHaveLength(2);
    expect(screen.getByText('This artist')).toBeInTheDocument();

    expect(screen.getByRole('button', { name: /Match Unmapped Artists/ })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Synchronize Wanted & Wishlist/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Find Missing Metadata/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Check Album Tags/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Find Quality Upgrades/ })).toBeInTheDocument();
  });
});
