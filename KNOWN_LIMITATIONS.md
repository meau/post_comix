# Known limitations

An honest, running list of gaps and unverified assumptions, in one
place instead of scattered across code comments and past
conversation. Check here before assuming something "just works" on
new data.

## Search-index lag and conflict recovery

ArchivesSpace's search index can lag behind very recent writes — a
record created on one row may not be findable yet by a search on the
next, even though it genuinely exists. Every creation path in this
codebase that could hit this (corporate/person agents, genre
subjects, top containers, digital objects) catches the resulting
"must be unique" rejection and reuses the conflicting record instead
of failing that row — see RECONCILIATION.md's "conflict-recovery
safety net" section. This was a real bug until it wasn't: the
local-fallback agent-creation path had no such catch for a while and
would crash the row outright on exactly this scenario, which is what
first surfaced this whole category of issue.

**Hierarchy nodes are the one exception** — see the hierarchy note
below for why they can't self-heal the same way.

## Verified vs. unverified ArchivesSpace schema assumptions

Most of the ArchivesSpace field/enum choices in this codebase were
checked against ArchivesSpace's actual source or documentation before
being relied on (`physdesc`, `date_label` values including
`publication`, `linked_agent_role` being strictly
creator/source/subject, `extent_type` including `items`, MARC relator
codes). A few were **not** independently confirmed the same way:

- **Where the external authority URI goes on a `subject` record.**
  `genres.py` assumes a top-level `authority_id` field, by analogy
  with how `agent` names work — but this specific placement wasn't
  found in ArchivesSpace's own schema docs/source the way the other
  enums were. Test a couple of real genre-term creates with a small
  batch before trusting this at the scale of a full run.
- **Resource-scoped top-container search** (`containers.py`) relies
  on a search-index facet field (`collection_uri_u_sstr`) to confirm
  a candidate top container is already linked to the *specific*
  resource being migrated into, not just sharing an indicator
  elsewhere in the repository. **Confirmed broken on at least one real
  instance**: the field didn't match, so every search came back empty
  and every re-run recreated every container from scratch, even
  within the same resource. It still fails safe in the sense that it
  never risks a *wrong* reuse — but "always create a duplicate
  instead" turned out to be a real, not just theoretical, problem.
  Mitigated (not fixed at the root) by `container_cache.json`
  (`persistent_cache.py`): the script now remembers, itself, every
  top container it has created, so re-running the same migration
  against the same target doesn't depend on ArchivesSpace's search
  working correctly for containers *this script* already knows about.
  This only helps for the common case (re-running the same script) —
  it doesn't fix the underlying search for a container someone else
  created some other way, or if `container_cache.json` gets deleted.
  The same field-name risk in principle applies to agent/genre/
  digital-object dedup searches too, though none of those have been
  confirmed broken the way this one was; the same `persistent_cache.py`
  mechanism could be applied to them the same way if that turns out
  to be needed.
- **Hierarchy node reuse** (`hierarchy.py`) has the same shape of
  caveat: a series/subseries node is looked up by title scoped to its
  parent via search, verified by fetching each candidate and checking
  its actual `parent` ref. Confirm this on a small `--dry-run` batch
  before a full run on a new instance. **Unlike agents, genre
  subjects, top containers, and digital objects, this one has no
  conflict-recovery safety net** — ArchivesSpace doesn't enforce
  title uniqueness on archival_objects, so there's no rejection to
  catch if search-index lag causes the same series to be created
  twice; it would just silently create a duplicate series node rather
  than erroring. If you see duplicate series/subseries after a run
  processed rows unusually fast, this is why — worth checking for
  before relying on hierarchy output at scale on a fresh instance.
- **Location matching** (`locations.py`) searches on the most specific
  coordinate present, then verifies every candidate by fetching it and
  comparing all coordinate fields directly — so a search-index quirk
  would show up as a false "not found" (safe: deferred to the
  missing-locations report) rather than a wrong match. Still worth
  confirming on a small batch, same as the other search-then-verify
  features here.

## Date parsing

- **Day-level ranges within a single month aren't parsed** — e.g.
  `"1998 March 8-11"` or `"1978 May 12-14"`. These get logged as a
  warning and the archival object is still created, just without a
  `dates` subrecord. Found in Brown's publications data; not yet
  fixed.
- Season words (`Winter`, `Spring`, `Summer`, `Fall`) collapse to
  year-only precision, by explicit decision — no month is guessed
  from a season.
- `dateStart`/`dateEnd` (or similarly pre-split structured date
  columns) are never used, even when present — the free-text
  expression column is always re-parsed instead, since pre-split
  columns have consistently turned out to be coarser than what the
  text itself supports.

## Containers and locations

- **Real `Location` record matching exists** (`locations.py`,
  `missing_locations.py`, `relink_locations.py`) — see
  `LOCATIONS.md` for the full workflow. It never auto-creates a
  Location; unmatched coordinates are deferred to a spreadsheet for
  staff review and a later re-link pass.
- **"Map case / drawer" placeholder containers are a separate,
  deliberate stopgap, not location modeling.** `box.placeholder: true`
  creates one shared `folder`-type top container for every row using
  that config — it does not search or create `Location` records at
  all, and doesn't represent the map case/drawer distinction in any
  structured way. If a spreadsheet's shelf data is reliable enough to
  search/match (like the Range/Bay/Shelf scheme locations.py was
  built for), use `location:` instead of this.
- **True two-level containers (e.g. box-within-a-case, or a folder
  sub-container inside a box) aren't implemented.** Some source
  templates have a second shelf-locator column pair
  (`shelfLocator2`/`shelfLocator2ID`) clearly intended for this, but
  no dataset processed so far actually uses it (it was present but
  entirely blank in the one file that had the columns) — so this was
  never built out. `sub_container`'s `indicator_2`/`type_2` fields
  are where this would go if needed.

## Resource creation

- The "Added entry" fields on a `Collection-Level Data` sheet
  (additional subjects and creators beyond the primary one) are
  recognized but not wired to any resolver — see
  `HIERARCHY_AND_RESOURCES.md`. Logged as a warning if present with a
  real value, never silently dropped, but nothing gets linked.
- `level: "collection"` and `finding_aid_status: "unprocessed"` are
  hardcoded defaults, not read from the sheet.
- Existence checking for "does this resource already exist" falls
  back to an exact title match when `callNumber` is blank — weaker
  than an ID match, and could create a duplicate on a re-run after a
  title edit.

## External lookups (LC NAF, Getty AAT)

- Both are public web services outside this project's control —
  expect occasional lookup failures under heavy load. These are
  designed to fail safe (see `RECONCILIATION.md`'s
  `created_local_lc_lookup_failed` / `created_local_aat_lookup_failed`
  statuses) rather than silently mistaken for a confirmed absence,
  but they still mean a local record gets created that might need
  manual reconciliation once the network issue clears.
- The LC "known label" redirect lookup isn't restricted by name type
  (personal vs. corporate) — a personal name string could in
  principle redirect to a corporate heading sharing the exact text.
  The `suggest2` fallback path IS correctly type-filtered. This is a
  limitation of the redirect endpoint itself.

## Not yet generalized

- `migrate_comics.py` (the original, Peck-specific script) is frozen
  as-is and does **not** get any of the newer capabilities (hierarchy,
  multiple agents, genre reconciliation, digital objects,
  `ignore_column`, `barcode_column`) — it only ever does what it did
  originally. `configs/peck_comics.yaml` on `migrate.py` is the
  actively-maintained equivalent going forward.
- `resource_builder.py`'s field vocabulary is specific to the Brown
  template family seen so far. A different institution's
  collection-level template with different field names would need
  this file extended or forked, not just a config change.
