import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { FileDecisions } from './file-decisions';

afterEach(cleanup);

it('keeps long file-decision lists compact with track review owning the full paths', () => {
  const items = Array.from({ length: 7 }, (_, id) => ({
    id,
    file_decision: {
      title: `Song ${id}`,
      keep: [`/music/album/${id}.flac`],
      quarantine: [`/music/album/${id}.mp3`],
      request: '',
    },
  }));
  render(<FileDecisions items={items} />);
  expect(screen.getAllByText('FLAC')).toHaveLength(5);
  fireEvent.click(screen.getByRole('button', { name: 'Show 2 more file decisions' }));
  expect(screen.getAllByText('FLAC')).toHaveLength(7);
  expect(screen.queryByText('File paths')).toBeNull();
  expect(screen.queryByText('/music/album/0.mp3')).toBeNull();
});
