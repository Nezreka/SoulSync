import { queryOptions } from '@tanstack/react-query';

import { readJson, shellClient } from '@/app/api-client';

export interface ShellStatusMediaServer {
  type?: string | null;
  connected?: boolean | null;
}

export interface ShellStatusPayload {
  media_server?: ShellStatusMediaServer | null;
}

export async function fetchShellStatus(): Promise<ShellStatusPayload> {
  return await readJson<ShellStatusPayload>(shellClient.get('status'));
}

export function shellStatusQueryOptions() {
  return queryOptions({
    queryKey: ['shell', 'status'] as const,
    queryFn: fetchShellStatus,
    staleTime: 30_000,
    retry: false,
  });
}
