import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import { chapterAt, clock } from '../-engine/timeline';
import { plainText, remaining, runtime } from '../-lib/format';
import { bookItem } from '../-lib/play-items';
import { bookCover } from '../-lib/rails';
import { booksQuery, catalogBookQuery, playableQuery, progressLookupQuery } from '../-player.api';
import { Empty, Hero, PlayButton, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems, usePlayer } from '../-ui/playback/use-player';
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
  // the book's own chapters, read from its files (the same timeline the player plays)
  const timeline = useQuery({
    ...playableQuery('audiobook', asin),
    enabled: sections.audiobooks && !!book,
  });
  const chapters = timeline.data?.chapters ?? [];
  const player = usePlayer();
  const playingThis = player.item?.type === 'audiobook' && player.item.id === asin;
  const playingChapter = playingThis ? chapterAt(chapters, player.position) : -1;
  const catalogAsin =
    book?.catalog_asin || (book && /^[A-Z0-9]{10}$/.test(book.asin) ? book.asin : '');
  const catalog = useQuery({ ...catalogBookQuery(catalogAsin), enabled: !!catalogAsin });
  const saved = useQuery({ ...progressLookupQuery('audiobook', [asin]), enabled: !!book });

  if (!sections.audiobooks) return <NotPartOfProfile what="Audiobooks" />;
  if (books.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  if (!book) return <NotFound what="Audiobook" />;

  const cover = bookCover(book, 'hero');
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
              onPlay={() => playItems([bookItem(book)])}
            />
            {resuming ? (
              <PlayButton
                label="From the start"
                primary={false}
                onPlay={() => playItems([bookItem(book)], 0, { startAt: 0 })}
              />
            ) : null}
          </>
        }
      />

      <div className={s.twoCol}>
        <section aria-label="Chapters">
          <div className={s.railHead}>
            <h2 className={s.sectionTitle}>Chapters</h2>
          </div>
          {timeline.isLoading ? <div className={s.skeleton} style={{ height: '12em' }} /> : null}
          {timeline.isError ? (
            <Empty title="Chapters aren't available">
              SoulSync couldn't read this book's files.
            </Empty>
          ) : null}
          {chapters.length ? (
            <ol className={s.chapterList}>
              {chapters.map((c, i) => {
                const end = chapters[i + 1]?.start ?? timeline.data?.duration ?? c.start;
                return (
                  <li key={`${c.start}-${i}`}>
                    <button
                      type="button"
                      className={s.chapterRow}
                      data-playing={i === playingChapter ? 'true' : undefined}
                      onClick={() =>
                        playingThis
                          ? getEngine().seek(c.start)
                          : playItems([bookItem(book)], 0, { startAt: c.start })
                      }
                      data-nav
                    >
                      <span className={s.chapterNum}>{i + 1}</span>
                      <span className={s.chapterTitle}>{c.title}</span>
                      <span className={s.chapterTime}>{clock(Math.max(0, end - c.start))}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          ) : null}
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
