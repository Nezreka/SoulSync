# Best in class — the program

Direction set Aug 14 2026: *"better than Spotify. every single page. every part
of the page. maybe more pages."*

This file is the spine. It exists so the work has an order and a standard,
rather than being a series of one-off passes that each look better than the
last but never add up to a product.

---

## The strategic frame (read this before picking work)

**Beating Spotify at Spotify's own game is unwinnable and not the point.**
They have a thousand engineers, every play event on earth, and licensing.
Competing on "better recommendations" is choosing a fight against their
strongest asset with our weakest.

**What Spotify structurally CANNOT do — and SoulSync already can:**

| Spotify has | SoulSync has | The capability that unlocks |
|---|---|---|
| streams | **files you own** | quality, format, bitrate, "this rip is bad" |
| no library | **a library + its gaps** | "you're missing 3 tracks off this album" |
| no acquisition | **download history** | own-vs-play, "you grabbed this and never played it" |
| one account | **your whole household** | per-profile everything, shared rooms |
| a closed catalogue | **the whole of Soulseek** | anything, at any quality |
| no ops | **it IS the ops** | the thing is a media *manager* |

Every page should be judged against one question: **does this page do something
that could only exist because the user owns their music?** Own-vs-play (stats
P4) is the model. It is the single strongest thing on the stats page precisely
because Spotify cannot render it at any budget.

Pages that are just "a nicer list" are where effort goes to die.

---

## The standard (what "best in class" means here, concretely)

A page is done when all seven hold. This is the checklist to review against —
vibes are not a criterion.

1. **It answers a question the user actually has**, in the first screenful,
   without a click.
2. **Nothing is inert.** Every number that identifies a thing is clickable to
   that thing. Every album plays. Every artist opens.
3. **It is carried by artwork**, not by text with pictures next to it.
4. **Data arrives with motion** — counts land, lists stagger, bars grow.
   Honouring `prefers-reduced-motion` is part of this, not an exception to it.
5. **Empty, loading, error and partial states are designed**, not fallbacks.
   The empty state is the one a new user sees first.
6. **It works at 380px.** Not "does not break" — *works*.
7. **The ownership angle is present.** See the frame above.

---

## Where each page actually stands

Honest assessment. "Rebuilt" means it had a real design pass; it does NOT mean
it meets the seven above.

| Page | State | Biggest gap against the standard |
|---|---|---|
| Stats | rebuilt + Wrapped | Library tab is still a table dump |
| Dashboard | rebuilt | ops cards still read like a status page |
| Search | rebuilt | — |
| Discover | rebuilt | — |
| Wishlist | rebuilt | motion, empty state |
| Watchlist | ported | never had a design pass |
| Library | ported | **the biggest page, the weakest treatment** |
| Sync | ported | in flight (task #339) |
| Downloads | ported | inert rows |
| Artist detail | ported | should be the best page in the app; is not |
| Album detail | **does not exist** | — |
| Explorer / Tools / Automations | ported | operator surfaces, lower bar |
| Chat / Arcade | rebuilt | — |

**The two biggest wins available, by a distance:**

- **Artist detail.** Every path in the app leads here. It should be the page
  people screenshot. Right now it is a port.
- **Album detail — which does not exist at all.** There is nowhere to *land*
  on an album. Every "play this album" is currently a dead end into a list.
  This is the missing page, and it is where ownership shows up hardest:
  which tracks you have, at what quality, what is missing, what you have
  played, upgrade paths.

## Proposed order

1. **Album detail page** (new surface — the missing keystone)
2. **Artist detail rebuild** (highest traffic in the app)
3. **Library page rebuild** (biggest surface, weakest treatment)
4. **A shared motion + empty-state layer**, extracted from the year story, so
   every page after this gets it for free rather than re-implementing it
5. Downloads / Watchlist / Wishlist polish against the seven
6. Stats Library tab

Item 4 is deliberately fourth and not first: build it twice in anger before
generalising it, or it will be the wrong abstraction.

---

## Open questions for Boulder

- Album detail is a new page and a new route. Confirm it is wanted before it
  is built.
- "More pages" — which? Candidates that fit the frame: album detail, a genre
  page, a "your library's health" narrative page, a per-profile year.
