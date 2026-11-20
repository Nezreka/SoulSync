import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import { plainText, remaining, runtime } from '../-lib/format';
import { bookCover } from '../-lib/rails';
import { booksQuery, catalogBookQuery, progressLookupQuery } from '../-player.api';
import { Empty, Hero, PlayButton, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/audiobooks/$asin')({
  component: BookPage,
});

function names(list?: ({ name: string } | string)[]): string {
  return (list ?? [])
    .map((x) => (typeof x === 'string' ? x : x.name))
    .filter(Boolean)
    .join(', ');
}

function BookPage() {
  const sections = useSections();
  const asin = Route.useParams().asin;
  const books = useQuery({ ...booksQuery(), enabled: sections.audiobooks });
  const book = books.data?.find((b) => b.asin === asin);
  const catalogAsin =
    book?.catalog_asin || (book && /^[A-Z0-9]{10}$/.test(book.asin) ? book.asin : '');
  const catalog = useQuery({ ...catalogBookQuery(catalogAsin), enabled: !!catalogAsin });
  const saved = useQuery({ ...progressLookupQuery('audiobook', [asin]), enabled: !!book });

  if (!sections.audiobooks) return <NotPartOfProfile what="Audiobooks" />;
  if (books.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  if (!book) return <NotFound what="Audiobook" />;

  const cover = bookCover(book);
  const seconds = (book.runtime_minutes ?? catalog.data?.runtime_minutes ?? 0) * 60;
  const resume = saved.data?.[asin];
  const resuming = resume && !resume.finished && resume.position_seconds > 0;
  const genres = names(catalog.data?.genres);
  const narrators = book.narrator || names(catalog.data?.narrators);

  return (
    <>
      <Hero
        backdrop={cover}
        poster={cover}
        posterShape="square"
        kicker={
          book.series_title
            ? `${book.series_title}${book.series_sequence ? ` · Book ${book.series_sequence}` : ''}`
            : 'Audiobook'
        }
        title={book.title}
        meta={
          <>
            {book.author ? <span>{book.author}</span> : null}
            {seconds ? <span>{runtime(seconds)}</span> : null}
            {resuming ? (
              <span>{remaining(resume.position_seconds, resume.duration_seconds ?? seconds)}</span>
            ) : null}
          </>
        }
        overview={plainText(catalog.data?.summary) || catalog.data?.subtitle || null}
        actions={
          <>
            <PlayButton
              label={resuming ? `Resume at ${runtime(resume.position_seconds)}` : 'Play'}
            />
            {resuming ? <PlayButton label="From the start" primary={false} /> : null}
          </>
        }
      />

      <div className={s.twoCol}>
        <section aria-label="Chapters">
          <div className={s.railHead}>
            <h2 className={s.sectionTitle}>Chapters</h2>
          </div>
          <Empty title="Chapters come with playback">
            SoulSync doesn't read chapter marks out of book files yet. They show up here, with speed
            and a sleep timer, when the player core lands.
          </Empty>
        </section>
        <dl className={s.facts}>
          {book.author ? (
            <>
              <dt>Written by</dt>
              <dd>{book.author}</dd>
            </>
          ) : null}
          {narrators ? (
            <>
              <dt>Narrated by</dt>
              <dd>{narrators}</dd>
            </>
          ) : null}
          {genres ? (
            <>
              <dt>Genre</dt>
              <dd>{genres}</dd>
            </>
          ) : null}
          {catalog.data?.release_date ? (
            <>
              <dt>Released</dt>
              <dd>{catalog.data.release_date.slice(0, 4)}</dd>
            </>
          ) : null}
        </dl>
      </div>
    </>
  );
}
