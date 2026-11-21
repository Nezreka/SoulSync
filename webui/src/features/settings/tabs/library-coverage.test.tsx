/**
 * Settings > Library > Folders > Music: your media server's folders. Each
 * folder says in plain words whether SoulSync scans it; a folder SoulSync
 * sees but doesn't scan adds as a library folder in one click; a folder it
 * can't find takes a typed folder, tries it, and saves it as an override.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import {
  type CoverageFolder,
  type CoverageResult,
  folderVerdict,
  ServerFoldersSection,
} from './library-coverage';

function folder(over: Partial<CoverageFolder>): CoverageFolder {
  return {
    library: 'Music',
    server_folder: '/data/music',
    tracks: 1000,
    local_folder: '/music',
    found_by: 'suffix',
    status: 'covered',
    sampled: 40,
    readable: 40,
    readable_pct: 100,
    reasons: { ok: 40 },
    examples: [],
    reachable_tracks: 1000,
    ...over,
  };
}

const RESULT: CoverageResult = {
  server: 'plex',
  folders_from: 'server',
  total_tracks: 3000,
  reachable_tracks: 1000,
  reachable_pct: 33.3,
  docker: true,
  folders: [
    folder({}),
    folder({
      server_folder: '/data/archive',
      local_folder: '/archive',
      found_by: 'same',
      status: 'not_library',
      readable: 0,
      readable_pct: 0,
      reasons: { not_library: 40 },
      reachable_tracks: 0,
    }),
    folder({
      library: 'Kids',
      server_folder: '/mnt/disk7/Kids',
      local_folder: null,
      found_by: null,
      status: 'not_mounted',
      readable: 0,
      readable_pct: 0,
      reasons: { not_mounted: 40 },
      reachable_tracks: 0,
      volume_example: '- /where/kids/is/on/the/host:/kids',
      examples: [
        {
          server_path: '/mnt/disk7/Kids/A/B/1.flac',
          local_path: null,
          reason: 'not_mounted',
          why: "The folder isn't there inside SoulSync",
        },
      ],
    }),
  ],
};

function serve(result: CoverageResult = RESULT) {
  const calls: string[] = [];
  const posts: Record<string, unknown>[] = [];
  server.use(
    http.get('*/api/library/coverage', ({ request }) => {
      calls.push(new URL(request.url).search);
      return HttpResponse.json({ success: true, ...result });
    }),
    http.get('*/api/library/path-mappings', () =>
      HttpResponse.json({ success: true, mappings: [] }),
    ),
    http.post('*/api/library/coverage/check-path', async ({ request }) => {
      const body = (await request.json()) as { path: string };
      const there = body.path === '/kids';
      return HttpResponse.json({
        success: true,
        exists: there,
        is_dir: there,
        readable: there,
        entries: there ? 12 : 0,
        sample: there ? ['ABBA', 'Beck'] : [],
      });
    }),
    http.post('*/api/library/coverage/folder', async ({ request }) => {
      posts.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        success: true,
        folder: folder({
          library: 'Kids',
          server_folder: '/mnt/disk7/Kids',
          local_folder: '/kids',
          found_by: 'override',
          readable: 38,
          readable_pct: 95,
        }),
      });
    }),
    http.post('*/api/library/path-mappings', async ({ request }) => {
      posts.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ success: true, mapping: { id: 1 } });
    }),
  );
  return { calls, posts };
}

describe('your media server folders', () => {
  it('shows the overall share and each folder in plain words', async () => {
    serve();
    render(<ServerFoldersSection />);
    expect(await screen.findByText('33%')).toBeInTheDocument();
    expect(screen.getByText(/of Plex's music SoulSync can read directly/)).toBeInTheDocument();
    expect(
      screen.getByText('SoulSync sees this folder as /music, and scans it.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/isn't one of its library folders/)).toBeInTheDocument();
    expect(screen.getByText(/has to be mounted into SoulSync first/)).toBeInTheDocument();
    // docker: the compose line for the folder it can't see
    expect(screen.getByText('- /where/kids/is/on/the/host:/kids')).toBeInTheDocument();
  });

  it('adds a folder SoulSync sees but does not scan, in one click', async () => {
    serve();
    const add = vi.fn();
    render(<ServerFoldersSection onAddLibraryFolder={add} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add as a library folder' }));
    expect(add).toHaveBeenCalledWith('/archive');
    expect(screen.queryByRole('button', { name: 'Add as a library folder' })).toBeNull();
    expect(screen.getByText(/scanned on the next library scan/)).toBeInTheDocument();
  });

  it('a folder it can not see takes a typed folder, tries it, then saves it', async () => {
    const { posts } = serve();
    render(<ServerFoldersSection />);
    await screen.findByText('33%');
    const kids = screen.getByText('/mnt/disk7/Kids').closest('li') as HTMLElement;
    fireEvent.click(within(kids).getByRole('button', { name: 'SoulSync sees it somewhere else' }));
    const save = within(kids).getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    fireEvent.change(within(kids).getByLabelText(/SoulSync sees this folder as/), {
      target: { value: '/kids' },
    });
    expect(await within(kids).findByText('Found, 12 items: ABBA, Beck')).toBeInTheDocument();
    fireEvent.click(within(kids).getByRole('button', { name: 'Try it' }));
    expect(await within(kids).findByText('95%')).toBeInTheDocument();
    expect(posts[0]).toMatchObject({ server_folder: '/mnt/disk7/Kids', local_folder: '/kids' });
    fireEvent.click(within(kids).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toMatchObject({
      server_prefix: '/mnt/disk7/Kids',
      local_prefix: '/kids',
      this_server: true,
    });
  });

  it('check again asks for a fresh run', async () => {
    const { calls } = serve();
    render(<ServerFoldersSection />);
    await screen.findByText('33%');
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(calls).toContain('?refresh=1'));
    expect(calls[0]).toBe('');
  });

  it('says so when no media server is linked', async () => {
    serve({
      server: '',
      folders: [],
      total_tracks: 0,
      reachable_pct: null,
      message: 'No media server is linked, so there is nothing to compare with.',
    });
    render(<ServerFoldersSection />);
    expect(await screen.findByText(/No media server is linked/)).toBeInTheDocument();
  });

  it('bare leaves the box and heading to the host', async () => {
    serve();
    const { container } = render(<ServerFoldersSection bare />);
    await screen.findByText('33%');
    expect(container.querySelector('.settings-group')).toBeNull();
    expect(screen.queryByRole('heading', { name: "Your media server's folders" })).toBeNull();
  });

  it('a broken override says which folder is missing', () => {
    expect(
      folderVerdict(folder({ status: 'not_mounted', local_folder: null, broken_override: '/x' })),
    ).toBe("Your override says SoulSync sees this as /x, but that folder isn't there.");
  });
});
