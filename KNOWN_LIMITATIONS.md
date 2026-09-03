# Known limitations

An honest, running list of gaps and unverified assumptions, in one
place instead of scattered across code comments and past
conversation. Check here before assuming something "just works" on
new data.

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
  elsewhere in the repository. If your ArchivesSpace instance indexes
  this differently, the search comes back empty and the code falls
  back to **creating** a new container rather than risking a wrong
  reuse — so it fails safe, but check the `--dry-run` log for boxes
  you expect to be reused showing up as `"created"` instead.
- **Hierarchy node reuse** (`hierarchy.py`) has the same shape of
  caveat: a series/subseries node is looked up by title scoped to its
  parent via search, verified by fetching each candidate and checking
  its actual `parent` ref. Confirm this on a small `--dry-run` batch
  before a full run on a new instance.

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

- **"Map case / drawer" placeholder containers are a deliberate
  stopgap, not real location modeling.** `box.placeholder: true`
  creates one shared `folder`-type top container for every row using
  that config — it does not create ArchivesSpace `Location` records
  (building/room/coordinate fields) or represent the map case/drawer
  distinction at all. If real per-location tracking is needed later,
  this needs a proper `Location`-record-based rebuild, not just a
  config tweak.
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
