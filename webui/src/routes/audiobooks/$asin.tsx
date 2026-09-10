import { createFileRoute } from '@tanstack/react-router';

import { AudiobookDetailPage } from './-ui/audiobook-detail-page';

export const Route = createFileRoute('/audiobooks/$asin')({
  component: AudiobookDetailPage,
});
