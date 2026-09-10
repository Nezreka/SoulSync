import { createFileRoute, redirect } from '@tanstack/react-router';

/**
 * The audiobook wishlist lives on the wishlist page, not here.
 *
 * It started out as its own page under /audiobooks because every other
 * audiobook file already lived here — convenience, not design. It left the app
 * with two wishlist surfaces for what is conceptually one feature. This route
 * stays only so an existing link or bookmark still lands somewhere sensible.
 */
export const Route = createFileRoute('/audiobooks/wishlist')({
  beforeLoad: () => {
    throw redirect({ to: '/wishlist', search: { media: 'audiobooks' }, replace: true });
  },
});
