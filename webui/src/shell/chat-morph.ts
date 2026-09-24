/**
 * patch the chat message list in place instead of rebuilding it.
 *
 * renderMessages used to do host.innerHTML = everything on every new message,
 * which threw away whatever the reader had open: a playing youtube video, a
 * spotify / deezer / soundcloud player, an inline web preview, a loaded image,
 * a dismissed link card. in a busy room the video died every few seconds.
 *
 * so the new html is diffed against what each node looked like when it was
 * rendered (not what it looks like now: players and link cards get added to a
 * line after render). an unchanged node is left exactly where it is. it is
 * never moved, because moving an iframe reloads it; new nodes are inserted
 * around it and gone ones removed. a message group whose author row is the
 * same is diffed one level down, so a new message joining the group keeps the
 * earlier lines (and the video in one of them) alive.
 */

const RENDERED = new WeakMap<Element, string>();
const GROUP_KEY = new WeakMap<Element, string>();

function groupBody(el: Element): Element | null {
  return el.classList.contains('chat-group') ? el.querySelector(':scope > .chat-group-body') : null;
}

function record(el: Element): void {
  RENDERED.set(el, el.outerHTML);
  const body = groupBody(el);
  if (!body) return;
  // the group minus its body: class list, avatar. same key = same author row
  const shell = el.cloneNode(true) as Element;
  shell.querySelector(':scope > .chat-group-body')?.remove();
  GROUP_KEY.set(el, shell.outerHTML);
  for (const child of Array.from(body.children)) RENDERED.set(child, child.outerHTML);
}

function sigOf(el: Element): string {
  return RENDERED.get(el) ?? el.outerHTML;
}

function sameGroup(old: Element, next: Element): boolean {
  const key = GROUP_KEY.get(next);
  return key !== undefined && GROUP_KEY.get(old) === key;
}

function reconcile(parent: Element, next: Element[], descend: boolean): void {
  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType !== Node.ELEMENT_NODE) node.remove();
  }
  let cur = parent.firstElementChild;
  for (const n of next) {
    const want = RENDERED.get(n) ?? n.outerHTML;
    if (cur && sigOf(cur) === want) {
      cur = cur.nextElementSibling;
      continue;
    }
    if (descend && cur && sameGroup(cur, n)) {
      const oldBody = groupBody(cur);
      const newBody = groupBody(n);
      if (oldBody && newBody) {
        reconcile(oldBody, Array.from(newBody.children), false);
        RENDERED.set(cur, want);
        cur = cur.nextElementSibling;
        continue;
      }
    }
    // an older node further on still matches: what sits before it is gone
    let found: Element | null = null;
    for (let probe = cur?.nextElementSibling ?? null; probe; probe = probe.nextElementSibling) {
      if (sigOf(probe) === want) {
        found = probe;
        break;
      }
    }
    if (found) {
      while (cur && cur !== found) {
        const gone: Element = cur;
        cur = cur.nextElementSibling;
        gone.remove();
      }
      cur = found.nextElementSibling;
      continue;
    }
    parent.insertBefore(n, cur);
  }
  while (cur) {
    const gone: Element = cur;
    cur = cur.nextElementSibling;
    gone.remove();
  }
}

/** make host's children match html, keeping every node that didn't change. */
export function patchChatMessages(host: Element, html: string): void {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const next = Array.from(tpl.content.children);
  for (const n of next) record(n);
  reconcile(host, next, true);
}
