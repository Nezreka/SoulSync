/**
 * The People tab's API, served with MSW from real P0 payloads
 * (people.json: made by running core/permission_store against a temp
 * install shaped like the mockup's household; quality options, invites,
 * audit and the feature list written to match their routes).
 */

import { HttpResponse, http, server } from '@/test/msw';

import raw from './people.json';

type Json = Record<string, unknown>;
export const FIXTURE = raw as unknown as {
  catalogue: Json;
  profiles: {
    profiles: Array<Json & { id: number; name: string; is_admin: boolean; disabled: boolean }>;
  };
  templates: {
    templates: Array<
      Json & { id: number; name: string; followers: Array<{ id: number; name: string }> }
    >;
    signup_template_id: number;
  };
  policy: { policy: Json; changed: string[] };
  perms: Record<string, Json>;
  policy_dry_run: Json & { impact: Json };
  quality: Json;
  invites: { invites: Json[] };
  audit: { entries: Json[] };
  features: Json;
  counts: { music: Json; video: Json };
};

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export interface Calls {
  method: string;
  url: string;
  body: unknown;
}

export function servePeople(
  opts: {
    templates?: typeof FIXTURE.templates;
    /** rows on top of the saved policy */
    policy?: Json;
    forbid?: boolean;
    onPolicy?: (body: Json, dry: boolean) => Response | undefined;
    onTemplate?: (id: number, body: Json, dry: boolean) => Response | undefined;
    onDelete?: (id: number, reassign: string | null) => Response | undefined;
  } = {},
) {
  const calls: Calls[] = [];
  let templates = clone(opts.templates ?? FIXTURE.templates);
  let policy = clone(FIXTURE.policy);
  if (opts.policy) policy = { ...policy, policy: { ...policy.policy, ...opts.policy } };
  const track = async (request: Request) => {
    let body: unknown = null;
    try {
      body = await request.clone().json();
    } catch {
      body = null;
    }
    calls.push({ method: request.method, url: request.url, body });
    return body as Json;
  };
  const deny = () =>
    HttpResponse.json({ success: false, error: 'Admin access required' }, { status: 403 });
  server.use(
    http.get('*/api/permissions/catalogue', () =>
      opts.forbid ? deny() : HttpResponse.json(FIXTURE.catalogue),
    ),
    http.get('*/api/permissions/quality-options', () => HttpResponse.json(FIXTURE.quality)),
    http.get('*/api/permissions/policy', () => (opts.forbid ? deny() : HttpResponse.json(policy))),
    http.get('*/api/permissions/templates', () =>
      opts.forbid ? deny() : HttpResponse.json(templates),
    ),
    http.get('*/api/profiles', () => HttpResponse.json(FIXTURE.profiles)),
    http.get('*/api/profiles/:id/permissions', ({ params }) => {
      const p = FIXTURE.perms[String(params.id)];
      return p ? HttpResponse.json(p) : HttpResponse.json({ success: false }, { status: 404 });
    }),
    http.get('*/api/profiles/invites', () => HttpResponse.json(FIXTURE.invites)),
    http.delete('*/api/profiles/invites/:id', async ({ request }) => {
      await track(request);
      return HttpResponse.json({ success: true });
    }),
    http.get('*/api/profiles/audit', () => HttpResponse.json(FIXTURE.audit)),
    http.get('*/api/profiles/feature-defaults', () => HttpResponse.json(FIXTURE.features)),
    http.get('*/api/requests/music/counts', () => HttpResponse.json(FIXTURE.counts.music)),
    http.get('*/api/video/requests/counts', () => HttpResponse.json(FIXTURE.counts.video)),
    http.put('*/api/permissions/policy', async ({ request }) => {
      const body = await track(request);
      const dry = new URL(request.url).searchParams.get('dry_run') === '1';
      const custom = opts.onPolicy?.(body, dry);
      if (custom) return custom;
      if (!dry) {
        policy = { ...policy, policy: { ...policy.policy, ...(body.set as Json) } };
      }
      return HttpResponse.json({ ...FIXTURE.policy_dry_run, dry_run: dry });
    }),
    http.put('*/api/permissions/templates/:id', async ({ request, params }) => {
      const body = await track(request);
      const id = Number(params.id);
      const dry = new URL(request.url).searchParams.get('dry_run') === '1';
      const custom = opts.onTemplate?.(id, body, dry);
      if (custom) return custom;
      const t = templates.templates.find((x) => x.id === id)!;
      const next: typeof t = {
        ...t,
        ...(body.name ? { name: String(body.name) } : {}),
        ...(body.description !== undefined ? { description: body.description } : {}),
        perms: { ...(t.perms as Json), ...((body.perms as Json) ?? {}) },
        ...(body.sidebar ? { sidebar: body.sidebar } : {}),
      };
      if (!dry)
        templates = {
          ...templates,
          templates: templates.templates.map((x) => (x.id === id ? next : x)),
        };
      return HttpResponse.json({
        success: true,
        dry_run: dry,
        template: next,
        impact: {
          reaches: t.followers.length,
          changes: t.followers
            .map((f) => ({
              profile_id: f.id,
              name: f.name,
              rows: Object.entries((body.perms as Json) ?? {}).map(([k, v]) => ({
                id: k,
                before: (t.perms as Json)[k],
                after: v,
              })),
            }))
            .filter((c) => c.rows.length),
          not_following: [],
        },
      });
    }),
    http.post('*/api/permissions/templates', async ({ request }) => {
      const body = await track(request);
      const from =
        templates.templates.find((x) => x.id === body.from_template_id) ?? templates.templates[0];
      const made: typeof from = {
        ...clone(from),
        id: 99,
        slug: null,
        builtin: false,
        name: String(body.name),
        followers: [],
      };
      templates = { ...templates, templates: [...templates.templates, made] };
      return HttpResponse.json({ success: true, template: made }, { status: 201 });
    }),
    http.delete('*/api/permissions/templates/:id', async ({ request, params }) => {
      await track(request);
      const id = Number(params.id);
      const reassign = new URL(request.url).searchParams.get('reassign_to');
      const custom = opts.onDelete?.(id, reassign);
      if (custom) return custom;
      const t = templates.templates.find((x) => x.id === id)!;
      if (id === templates.signup_template_id) {
        return HttpResponse.json(
          { success: false, error: 'signup_template', message: 'New people start on this one.' },
          { status: 409 },
        );
      }
      if (t.followers.length && !reassign) {
        return HttpResponse.json(
          { success: false, error: 'has_followers', message: 'People still follow it.' },
          { status: 409 },
        );
      }
      templates = { ...templates, templates: templates.templates.filter((x) => x.id !== id) };
      return HttpResponse.json({ success: true, deleted: id, moved: reassign ? t.followers : [] });
    }),
    http.put('*/api/permissions/signup-template', async ({ request }) => {
      const body = await track(request);
      templates = { ...templates, signup_template_id: Number(body.template_id) };
      return HttpResponse.json({ success: true, signup_template_id: body.template_id });
    }),
  );
  return { calls };
}

/** an admin is looking (index.html's getCurrentProfileContext) */
export function signInAs(admin: boolean, profileId = admin ? 1 : 3) {
  (window as unknown as Record<string, unknown>).getCurrentProfileContext = () => ({
    profileId,
    isAdmin: admin,
  });
  window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
}
