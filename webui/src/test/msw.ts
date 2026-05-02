import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';

export { HttpResponse, http };

export const server = setupServer();
