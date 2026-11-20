import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';
import type { OwnedBook } from '../-player.types';

import { bookCover, newestFirst } from '../-lib/rails';
import { sortKey } from '../-lib/video';
import { booksQuery } from '../-player.api';
import { CardGrid, Empty, PageTitle, Tabs, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

type BookSort = 'added' | 'title' | 'author';

export const Route = createFileRoute('/player/audiobooks/')({
  validateSearch: (search: Record<string, unknown>): { sort?: BookSort } =>
    search.sort === 'title' || search.sort === 'author' || search.sort === 'added'
      ? { sort: search.sort }
      : {},
  component: BooksBrowse,
});

function sortBooks(books: OwnedBook[], sort: BookSort): OwnedBook[] {
  if (sort === 'added') return newestFirst(books, (b) => b.imported_at);
  const key = (b: OwnedBook) =>
    sort === 'author' ? (b.author ?? '').toLocaleLowerCase() : sortKey(b.title);
  return books
    .slice()
    .sort(
      (a, b) => key(a).localeCompare(key(b)) || sortKey(a.title).localeCompare(sortKey(b.title)),
    );
}

function BooksBrowse() {
  const sections = useSections();
  const { sort = 'added' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/audiobooks/' });
  const books = useQuery({ ...booksQuery(), enabled: sections.audiobooks });

  const cards: RailCard[] = useMemo(
    () =>
      sortBooks(books.data ?? [], sort).map((b) => ({
        key: b.asin,
        title: b.title,
        subtitle: b.author ?? undefined,
        art: bookCover(b),
        href: `/player/audiobooks/${encodeURIComponent(b.asin)}`,
        shape: 'square',
      })),
    [books.data, sort],
  );

  if (!sections.audiobooks) return <NotPartOfProfile what="Audiobooks" />;

  return (
    <>
      <PageTitle>Audiobooks</PageTitle>
      <div className={s.toolbar}>
        <Tabs
          label="Sort"
          value={sort}
          options={[
            { value: 'added', label: 'Recently added' },
            { value: 'title', label: 'Title' },
            { value: 'author', label: 'Author' },
          ]}
          onChange={(next) => void navigate({ search: { sort: next }, replace: true })}
        />
        {cards.length ? (
          <span className={s.notYet}>{cards.length.toLocaleString()} books</span>
        ) : null}
      </div>
      {books.isLoading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
      {books.data && !cards.length ? (
        <Empty title="No audiobooks yet">
          Books you add in SoulSync show up here once they are on disk.
        </Empty>
      ) : null}
      {cards.length ? <CardGrid cards={cards} shape="square" /> : null}
    </>
  );
}
