import { getRouteApi } from '@tanstack/react-router';
import { useEffect, useRef } from 'react';

const route = getRouteApi('/active-downloads');

/**
 * #2128: `?open=recycle` lands on this side's recycle bin. It runs once, then
 * the param leaves the url, so a reload or a step back doesn't keep forcing
 * the bin open over whatever the user picked since.
 */
export function useOpenRecycleBin(open: () => void) {
  const { open: wanted, media } = route.useSearch();
  const navigate = route.useNavigate();
  const run = useRef(open);
  run.current = open;
  useEffect(() => {
    if (wanted !== 'recycle') return;
    run.current();
    void navigate({ search: { media }, replace: true });
  }, [wanted, media, navigate]);
}
