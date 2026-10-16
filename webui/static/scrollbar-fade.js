/* ── General fading scrollbars: scroll-activity marker ─────────────────────
   The visual half lives in scrollbars.css (section 4): scrollbar thumbs rest
   dim and brighten on the `.is-scrolling` class. CSS has no "the user stopped
   scrolling" state, so this file supplies it:

   - One capture-phase listener on document. Scroll events do not bubble,
     but they do capture — this is the only way a single listener sees scrolls
     from every nested scroller (page, rails, modals, drawers) without
     attaching to elements that React mounts and unmounts later.
   - The scrolling element gets `.is-scrolling`; 1200ms after its last scroll
     event the class is removed and the CSS transition fades the thumb back.
     Timers are per element (WeakMap) so one scroller's idle fade never dims
     another scroller that is still moving.
   - Viewport scrolls report target=document; the class belongs on
     documentElement, whose scrollbar is the one being painted.
   - Passive listener, no preventDefault, no scroll positions read or written:
     this never scroll-jacks, and the per-event cost is a class add plus a
     timer reset on the element that is already scrolling.
   - Shadow DOM: the app has none today. If a shadow widget ever lands, its
     internal scrolls are non-composed and would mark the host element
     instead; document stylesheets cannot style shadow thumbs anyway, so
     the fade would silently no-op there rather than misbehave. */
(function () {
  'use strict';

  var IDLE_MS = 1200;
  var timers = new WeakMap();

  function onScroll(event) {
    var el = event.target;
    if (el === document) el = document.documentElement;
    if (!el || el.nodeType !== 1 || !el.classList) return;

    el.classList.add('is-scrolling');
    var pending = timers.get(el);
    if (pending) clearTimeout(pending);
    timers.set(
      el,
      setTimeout(function () {
        el.classList.remove('is-scrolling');
        timers.delete(el);
      }, IDLE_MS),
    );
  }

  document.addEventListener('scroll', onScroll, { capture: true, passive: true });
})();
