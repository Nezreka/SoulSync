import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import * as podcastApi from './-podcasts.api';

function renderPodcastsRoute(initialEntries = ['/podcasts']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('podcasts route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    vi.restoreAllMocks();
  });

  it('renders the podcasts page header, search input, and category pills', async () => {
    vi.spyOn(podcastApi, 'fetchFeaturedPodcasts').mockResolvedValue([
      {
        title: 'Huberman Lab',
        author: 'Andrew Huberman',
        description: 'Neuroscience and science',
        categories: ['Science'],
        explicit: false,
        artwork_url: 'https://example.com/art.jpg',
        feed_url: 'https://example.com/feed.xml',
        episode_count: 150,
      },
    ]);
    vi.spyOn(podcastApi, 'fetchPodcastDownloads').mockResolvedValue([]);

    renderPodcastsRoute();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Podcasts' })).toBeInTheDocument();
    });

    expect(
      screen.getByPlaceholderText(/Search podcasts by title, topic, or host/i),
    ).toBeInTheDocument();

    // Check category pills
    expect(screen.getByRole('tab', { name: /Trending/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Technology/i })).toBeInTheDocument();

    // Check featured show card
    await waitFor(() => {
      expect(screen.getAllByText('Huberman Lab').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Andrew Huberman/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  it('allows clicking a podcast show to open the billboard and episode list', async () => {
    vi.spyOn(podcastApi, 'fetchFeaturedPodcasts').mockResolvedValue([
      {
        title: 'Lex Fridman Podcast',
        author: 'Lex Fridman',
        description: 'Conversations about science, tech, history, and life',
        categories: ['Technology'],
        explicit: false,
        artwork_url: 'https://example.com/lex.jpg',
        feed_url: 'https://example.com/lex.xml',
        episode_count: 400,
      },
    ]);

    vi.spyOn(podcastApi, 'fetchPodcastShow').mockResolvedValue({
      title: 'Lex Fridman Podcast',
      author: 'Lex Fridman',
      description: 'Conversations about science, tech, history, and life',
      categories: ['Technology'],
      explicit: false,
      artwork_url: 'https://example.com/lex.jpg',
      feed_url: 'https://example.com/lex.xml',
      website: 'https://lexfridman.com/podcast',
      language: 'en',
      episode_count: 1,
      episodes: [
        {
          guid: 'lex-400',
          title: '#400 – Deep Learning Breakthroughs',
          enclosure_url: 'https://example.com/audio/400.mp3',
          enclosure_type: 'audio/mpeg',
          enclosure_length: 50000000,
          pub_date: '2026-09-01T12:00:00Z',
          duration_seconds: 7200,
          description: 'A deep dive into neural architectures',
          show_notes: '<p>A deep dive into neural architectures</p>',
          season: 1,
          episode_number: 400,
          episode_type: 'full',
          artwork_url: null,
          chapter_url: null,
          transcript_url: null,
        },
      ],
    });
    vi.spyOn(podcastApi, 'fetchPodcastDownloads').mockResolvedValue([]);

    renderPodcastsRoute();

    await waitFor(() => {
      expect(screen.getAllByText('Lex Fridman Podcast').length).toBeGreaterThanOrEqual(1);
    });

    // Click on the show card
    fireEvent.click(screen.getAllByText('Lex Fridman Podcast')[0]);

    // Should render billboard
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Back to Podcasts/i })).toBeInTheDocument();
      expect(screen.getByText('Play Latest Episode')).toBeInTheDocument();
      expect(
        screen.getAllByText('#400 – Deep Learning Breakthroughs').length,
      ).toBeGreaterThanOrEqual(1);
    });

    // Click Back to Podcasts
    fireEvent.click(screen.getByRole('button', { name: /Back to Podcasts/i }));

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Trending/i })).toBeInTheDocument();
    });
  });

  it('renders carousel controls when multiple featured shows are provided', async () => {
    vi.spyOn(podcastApi, 'fetchFeaturedPodcasts').mockResolvedValue([
      {
        title: 'Show One',
        author: 'Host One',
        description: 'First show description',
        categories: ['Technology'],
        explicit: false,
        artwork_url: 'https://example.com/1.jpg',
        feed_url: 'https://example.com/1.xml',
        episode_count: 50,
      },
      {
        title: 'Show Two',
        author: 'Host Two',
        description: 'Second show description',
        categories: ['Science'],
        explicit: false,
        artwork_url: 'https://example.com/2.jpg',
        feed_url: 'https://example.com/2.xml',
        episode_count: 75,
      },
    ]);
    vi.spyOn(podcastApi, 'fetchPodcastDownloads').mockResolvedValue([]);

    renderPodcastsRoute();

    await waitFor(() => {
      expect(screen.getByLabelText(/Next featured podcast/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Previous featured podcast/i)).toBeInTheDocument();
    });

    // Advance to next slide
    fireEvent.click(screen.getByLabelText(/Next featured podcast/i));

    await waitFor(() => {
      expect(screen.getAllByText('Show Two').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('opens show notes modal when clicking Notes on an episode', async () => {
    vi.spyOn(podcastApi, 'fetchFeaturedPodcasts').mockResolvedValue([
      {
        title: 'Show With Notes',
        author: 'Author',
        description: 'Show description',
        categories: ['News'],
        explicit: false,
        artwork_url: 'https://example.com/art.jpg',
        feed_url: 'https://example.com/notes.xml',
        episode_count: 1,
      },
    ]);

    vi.spyOn(podcastApi, 'fetchPodcastShow').mockResolvedValue({
      title: 'Show With Notes',
      author: 'Author',
      description: 'Show description',
      categories: ['News'],
      explicit: false,
      artwork_url: 'https://example.com/art.jpg',
      feed_url: 'https://example.com/notes.xml',
      website: 'https://example.com',
      language: 'en',
      episode_count: 1,
      episodes: [
        {
          guid: 'ep-notes-1',
          title: 'Deep Dive with Full Show Notes',
          enclosure_url: 'https://example.com/audio.mp3',
          enclosure_type: 'audio/mpeg',
          enclosure_length: 35000000,
          pub_date: '2026-09-02T10:00:00Z',
          duration_seconds: 3600,
          description: 'Short description',
          show_notes:
            '<p>Welcome to the show. Links: <a href="https://example.com/guest">Guest Bio</a></p>',
          season: 1,
          episode_number: 1,
          episode_type: 'full',
          artwork_url: null,
          chapter_url: null,
          transcript_url: null,
        },
      ],
    });
    vi.spyOn(podcastApi, 'fetchPodcastDownloads').mockResolvedValue([]);

    renderPodcastsRoute();

    await waitFor(() => {
      expect(screen.getAllByText('Show With Notes').length).toBeGreaterThanOrEqual(1);
    });

    // Open show
    fireEvent.click(screen.getAllByText('Show With Notes')[0]);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /View full show notes/i })).toBeInTheDocument();
    });

    // Click Notes
    fireEvent.click(screen.getByRole('button', { name: /View full show notes/i }));

    // Show notes drawer dialog should open
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Episode Notes & Transcript')).toBeInTheDocument();
      expect(screen.getByText('Guest Bio')).toBeInTheDocument();
    });

    // Close drawer
    fireEvent.click(screen.getByLabelText(/Close show notes/i));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('opens Category Explorer modal, searches categories, and selects a category', async () => {
    const fetchSpy = vi.spyOn(podcastApi, 'fetchFeaturedPodcasts').mockResolvedValue([]);
    vi.spyOn(podcastApi, 'fetchPodcastDownloads').mockResolvedValue([]);

    renderPodcastsRoute();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /All Categories/i })).toBeInTheDocument();
    });

    // Click "All Categories" button to open modal
    fireEvent.click(screen.getByRole('button', { name: /All Categories/i }));

    // Verify modal is open
    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: /Explore All Categories/i })).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search 20 categories/i);
    expect(searchInput).toBeInTheDocument();

    // Type "Philosophy" into search
    fireEvent.change(searchInput, { target: { value: 'Philosophy' } });

    // "Philosophy & Spirituality" should be visible in filtered results
    expect(screen.getByText(/Philosophy & Spirituality/i)).toBeInTheDocument();
    // Non-matching categories like "Technology" should not be visible in modal grid
    expect(screen.queryByText(/Code, hardware, AI/i)).not.toBeInTheDocument();

    // Click on "Philosophy & Spirituality" card
    fireEvent.click(screen.getByText(/Philosophy & Spirituality/i));

    // Modal should close
    await waitFor(() => {
      expect(
        screen.queryByRole('dialog', { name: /Explore All Categories/i }),
      ).not.toBeInTheDocument();
    });

    // Active category pill for Philosophy should now be rendered
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Philosophy & Spirituality/i })).toBeInTheDocument();
    });

    // Verify fetchFeaturedPodcasts was called with 'Philosophy'
    expect(fetchSpy).toHaveBeenCalledWith('Philosophy');
  });
});
