import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import { remaining, runtime, shortDate } from '../-lib/format';
import { backdropSet, stillSet, WIDE_SIZES } from '../-lib/images';
import { episodeItems } from '../-lib/play-items';
import { backdropUrl, fraction, posterUrl } from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import { progressLookupQuery, showDetailQuery, showFilesQuery } from '../-player.api';
import { Comments } from '../-ui/comments';
import { DetailLayout, InfoPanel, Section } from '../-ui/detail';
import { DownloadButton } from '../-ui/download-button';
import { InfoButton } from '../-ui/item-info';
import { Art, Badge, Hero, PlayButton, PlayerLink, VerdictBadge, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';
import { Facts } from '../-ui/video-facts';
import { CastRow, VersionPicker } from '../-ui/video-parts';

export const Route = createFileRoute('/player/tv/$showId_/episode/$episodeId')({
  component: EpisodePage,
});

function EpisodePage() {
  const sections = useSections();
  const params = Route.useParams();
  const showId = Number(params.showId);
  const episodeId = Number(params.episodeId);

  const detail = useQuery({
    ...showDetailQuery(showId),
    enabled: sections.video && Number.isFinite(showId),
  });
  const files = useQuery({
    ...showFilesQuery(showId),
    enabled: sections.video && Number.isFinite(showId),
  });

  const show = detail.data;
  const located = useMemo(() => {
    if (!show) return null;
    const seasons = (show.seasons ?? [])
      .slice()
      .sort((a, b) => (a.season_number || 999) - (b.season_number || 999));
    const flat: {
      seasonNumber: number;
      seasonName: string;
      ep: (typeof seasons)[number]['episodes'][number];
    }[] = [];
    for (const se of seasons) {
      const eps = (se.episodes ?? []).slice().sort((a, b) => a.episode_number - b.episode_number);
      for (const ep of eps) {
        flat.push({
          seasonNumber: se.season_number,
          seasonName: se.season_number === 0 ? 'Specials' : `Season ${se.season_number}`,
          ep,
        });
      }
    }
    const idx = flat.findIndex((x) => x.ep.id === episodeId);
    if (idx < 0) return null;
    const currentSeason = seasons.find((se) => se.season_number === flat[idx].seasonNumber);
    return {
      entry: flat[idx],
      prev: idx > 0 ? flat[idx - 1] : null,
      next: idx < flat.length - 1 ? flat[idx + 1] : null,
      seasonEpisodes: (currentSeason?.episodes ?? [])
        .slice()
        .sort((a, b) => a.episode_number - b.episode_number),
    };
  }, [show, episodeId]);

  const progressIds = useMemo(
    () => (located ? located.seasonEpisodes.map((e) => String(e.id)) : []),
    [located],
  );
  const progress = useQuery({
    ...progressLookupQuery('episode', progressIds),
    enabled: sections.video && progressIds.length > 0,
  });

  const [selectedFileId, setSelectedFileId] = useState<number | undefined>(undefined);

  if (!sections.video) return <NotPartOfProfile what="Movies and TV" />;
  if (detail.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  if (!show || !located) return <NotFound what="Episode" />;

  const { entry, prev, next, seasonEpisodes } = located;
  const ep = entry.ep;
  const epFiles =
    files.data?.seasons?.flatMap((se) => se.episodes).find((e) => e.id === ep.id)?.files ?? [];
  const ranked = rankVersions(epFiles);
  const activeFile = ranked.find((f) => f.id === selectedFileId) ?? ranked[0];
  const owned = ep.owned === true || (ep.owned !== false && (epFiles.length > 0 || !files.data));

  const row = progress.data?.[String(ep.id)];
  const seconds =
    activeFile?.runtime_seconds ?? (ep.runtime_minutes ? ep.runtime_minutes * 60 : null);
  const resumePos =
    row && !row.finished && row.position_seconds > 10 ? row.position_seconds : undefined;
  const done =
    resumePos !== undefined ? fraction(resumePos, row?.duration_seconds ?? seconds) : undefined;

  const sortedSeasons = (show.seasons ?? [])
    .slice()
    .sort((a, b) => (a.season_number || 999) - (b.season_number || 999));

  const playThis = (fileId?: number) => {
    const queue = episodeItems(show, sortedSeasons, ep.id);
    if (!queue.length) return;
    const pick = fileId ?? activeFile?.id;
    if (pick) queue[0] = { ...queue[0], part: String(pick) };
    playItems(queue);
  };

  const still = ep.has_still ? stillSet(ep.id) : null;
  const heroBackdrop = still ? still.src : backdropSet('show', show.id).src;
  const code = `S${String(entry.seasonNumber).padStart(2, '0')} · E${String(ep.episode_number).padStart(2, '0')}`;

  return (
    <>
      <Hero
        backdrop={heroBackdrop ?? backdropUrl('show', show.id)}
        poster={show.has_poster !== false ? posterUrl('show', show.id, 500) : null}
        kicker={
          <PlayerLink
            href={`/player/tv/${show.id}?season=${entry.seasonNumber}`}
            className={s.railMore}
          >
            {show.title} · {code}
          </PlayerLink>
        }
        title={ep.title || `Episode ${ep.episode_number}`}
        meta={
          <>
            {ep.air_date ? <span>{shortDate(ep.air_date)}</span> : null}
            {seconds ? <span>{runtime(seconds)}</span> : null}
            {ep.rating ? <span>★ {ep.rating.toFixed(1)}</span> : null}
            {resumePos !== undefined && done !== undefined && done < 1 ? (
              <span>{remaining(resumePos, row?.duration_seconds ?? seconds)}</span>
            ) : null}
          </>
        }
        badges={
          <div className={s.badges}>
            {ep.watched ? <Badge tone="yes">Watched</Badge> : null}
            {!owned ? <Badge>Not in your library</Badge> : null}
            {activeFile?.resolution ? (
              <Badge>{resolutionLabel(activeFile.resolution)}</Badge>
            ) : null}
            {activeFile?.playable ? (
              <VerdictBadge
                verdict={activeFile.playable.verdict}
                reasons={activeFile.playable.reasons}
              />
            ) : null}
          </div>
        }
        overview={ep.overview}
        actions={
          <>
            <PlayButton
              label={resumePos !== undefined ? 'Resume' : 'Play'}
              onPlay={owned ? () => playThis() : undefined}
              why="This episode isn't in your library yet."
            />
            {owned ? (
              <DownloadButton
                type="episode"
                id={String(ep.id)}
                title={`${show.title} · ${ep.title ?? 'Episode'}`}
              />
            ) : null}
            {owned ? (
              <InfoButton
                type="episode"
                id={ep.id}
                title={ep.title ?? 'Episode'}
                className={s.btn}
                labelled
              />
            ) : null}
            {prev ? (
              <PlayerLink
                href={`/player/tv/${show.id}/episode/${prev.ep.id}`}
                className={s.btn}
                data-nav
              >
                ← Prev (S{prev.seasonNumber}E{prev.ep.episode_number})
              </PlayerLink>
            ) : null}
            {next ? (
              <PlayerLink
                href={`/player/tv/${show.id}/episode/${next.ep.id}`}
                className={s.btn}
                data-nav
              >
                Next (S{next.seasonNumber}E{next.ep.episode_number}) →
              </PlayerLink>
            ) : null}
            <PlayerLink
              href={`/player/tv/${show.id}?season=${entry.seasonNumber}`}
              className={s.btn}
              data-nav
            >
              All episodes
            </PlayerLink>
          </>
        }
      />

      <DetailLayout
        main={
          <>
            {ranked.length > 1 ? (
              <Section title="Versions">
                <VersionPicker
                  files={ranked}
                  value={activeFile?.id}
                  onChange={(fid) => setSelectedFileId(fid)}
                />
              </Section>
            ) : null}

            <Section title={`More in ${entry.seasonName}`}>
              <div className={s.list} aria-label={entry.seasonName}>
                {seasonEpisodes.map((other) => {
                  const isCurrent = other.id === ep.id;
                  const otherRow = progress.data?.[String(other.id)];
                  const otherOwned = other.owned !== false;
                  const otherDone =
                    otherRow && !otherRow.finished
                      ? fraction(otherRow.position_seconds, otherRow.duration_seconds)
                      : otherRow?.finished
                        ? 1
                        : undefined;
                  return (
                    <PlayerLink
                      key={other.id}
                      href={`/player/tv/${show.id}/episode/${other.id}`}
                      className={s.row}
                      data-media="true"
                      data-missing={otherOwned ? undefined : 'true'}
                      data-active={isCurrent ? 'true' : undefined}
                      data-nav
                    >
                      <div className={s.rowArt}>
                        <Art
                          src={other.has_still ? stillSet(other.id).src : null}
                          srcSet={other.has_still ? stillSet(other.id).srcSet : undefined}
                          sizes={WIDE_SIZES}
                          alt={`Episode ${other.episode_number}`}
                        />
                        {otherDone !== undefined ? (
                          <div className={s.rowBar}>
                            <div
                              className={s.progressFill}
                              style={{ width: `${otherDone * 100}%` }}
                            />
                          </div>
                        ) : null}
                      </div>
                      <div className={s.rowMain}>
                        <span className={s.rowTitle}>
                          {other.episode_number}. {other.title || `Episode ${other.episode_number}`}
                        </span>
                        <span className={s.rowSub}>
                          {[
                            shortDate(other.air_date),
                            other.runtime_minutes ? runtime(other.runtime_minutes * 60) : '',
                            isCurrent ? 'Viewing' : '',
                            otherOwned ? '' : 'not in your library',
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                        {other.overview ? (
                          <span className={s.rowText}>{other.overview}</span>
                        ) : null}
                      </div>
                      <div className={s.rowEnd}>
                        {other.watched ? <Badge tone="yes">Watched</Badge> : null}
                      </div>
                    </PlayerLink>
                  );
                })}
              </div>
            </Section>

            <CastRow cast={show.cast} />
            <Comments itemType="episode" itemId={String(ep.id)} title={`${show.title} · ${code}`} />
          </>
        }
        side={
          <InfoPanel title="Episode details">
            <Facts
              rows={[
                ['Show', show.title],
                ['Season', entry.seasonName],
                ['Episode', String(ep.episode_number)],
                ['Aired', ep.air_date ? shortDate(ep.air_date) : null],
                ['Runtime', seconds ? runtime(seconds) : null],
                ['Rating', ep.rating ? `★ ${ep.rating.toFixed(1)}` : null],
                [
                  'Resolution',
                  activeFile?.resolution ? resolutionLabel(activeFile.resolution) : null,
                ],
                ['Video', activeFile?.video_codec?.toUpperCase() ?? null],
                ['Audio', activeFile?.audio_codec?.toUpperCase() ?? null],
              ]}
            />
          </InfoPanel>
        }
      />
    </>
  );
}
