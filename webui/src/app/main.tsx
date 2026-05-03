import '@vitejs/plugin-react/preamble';
import { createRoot } from 'react-dom/client';

import { bindWindowWebRouter } from '@/platform/shell/bridge';
import { ROUTER_ROOT_ID } from '@/platform/shell/route-controllers';

import { createAppQueryClient } from './query-client';
import { AppRouterProvider, createAppRouter } from './router';

export async function bootstrapApp() {
  const container = document.getElementById(ROUTER_ROOT_ID);
  if (!container) return null;

  const queryClient = createAppQueryClient();
  const router = createAppRouter({ queryClient });

  bindWindowWebRouter(router);
  createRoot(container).render(<AppRouterProvider router={router} queryClient={queryClient} />);

  return { queryClient, router };
}

void bootstrapApp();
