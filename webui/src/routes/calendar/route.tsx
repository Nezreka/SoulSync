import { createFileRoute } from '@tanstack/react-router';

import { usePermMode } from '@/platform/shell/my-permissions';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';
import { guardPageAccess } from '@/platform/shell/route-guard';

import type { CalendarView } from './-calendar.types';

import { CalendarPage } from './-ui/calendar-page';

export interface CalendarSearch {
  /** the calendar is the video side's; the param keys its permission and side */
  media: 'video';
  view?: CalendarView;
  /** any day inside the week or month to show, YYYY-MM-DD; absent = today */
  date?: string;
  /** an episode to open on arrival (the video dashboard's upcoming row) */
  ep?: string;
}

const VIEWS: CalendarView[] = ['week', 'agenda', 'month'];

export function parseCalendarSearch(search: Record<string, unknown>): CalendarSearch {
  const out: CalendarSearch = { media: 'video' };
  if (typeof search.view === 'string' && (VIEWS as string[]).includes(search.view))
    out.view = search.view as CalendarView;
  if (typeof search.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(search.date))
    out.date = search.date;
  if ((typeof search.ep === 'string' || typeof search.ep === 'number') && String(search.ep))
    out.ep = String(search.ep);
  return out;
}

/** /calendar?media=video: the video calendar. The vanilla shell hands
 * /video-calendar off here, the same way it hands off the wishlist and tools. */
export const Route = createFileRoute('/calendar')({
  validateSearch: parseCalendarSearch,
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'calendar', '?media=video');
  },
  component: CalendarRoute,
});

function CalendarRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const profile = useProfile();
  // the calendar's get buttons are for episodes: TV's own mode (#2166),
  // not music's
  const tvGet = usePermMode('tv');
  useReactPageShell('calendar', '?media=video');
  return (
    <CalendarPage
      profileId={profile.profileId}
      isAdmin={profile.isAdmin}
      canDownload={tvGet === 'download'}
      search={search}
      onSearch={(next) =>
        void navigate({ search: (prev) => ({ ...prev, ...next, media: 'video' }), replace: true })
      }
    />
  );
}
