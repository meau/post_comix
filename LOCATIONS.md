# Locations

For spreadsheets with real shelf-location data (Range/Bay/Shelf,
building/room/coordinate schemes, etc.) that should be matched
against ArchivesSpace's `Location` records — a different concept
from a `top_container` (see the distinction below).

**Locations are never auto-created by this codebase.** A `Location`
represents real physical shelving infrastructure — creating one
should be a deliberate, reviewed action, not something a migration
script improvises from spreadsheet text. Instead: matched locations
get linked immediately, and unmatched ones are deferred through a
two-phase export/re-link workflow.

## Location vs. top_container

Easy to conflate, genuinely different things:
- A **top_container** (`containers.py`) is the physical *thing*
  holding materials — a box, a folder. It's what an `instance`
  actually links to.
- A **Location** (`locations.py`) is *where* a container currently
  sits — building, room, coordinates (e.g. Range/Bay/Shelf). It
  attaches to a top_container via `container_locations`, not
  directly to an archival object.

A container can exist without a location (you know you have a box,
just not where it is right now); a location record shouldn't exist
without representing a real, physically identifiable spot.

## Configuring it

```yaml
location:
  building: "John Hay Library"   # fixed string -- not read from a column
  coordinate_1: {column: "Range", label: "Range"}
  coordinate_2: {column: "Bay", label: "Bay"}
  coordinate_3: {column: "Shelf", label: "Shelf"}
```

Any coordinate level can be omitted if your data doesn't have it.
`building` is currently always a fixed string in the config, not a
spreadsheet column — no dataset seen so far has varied it per row.

## The workflow

**Phase 1 — during a normal `migrate.py` run:**

For each row with location coordinates, `migrate.py`:
1. Searches ArchivesSpace for a `Location` matching every non-blank
   coordinate level (and `building`, if given) — verified by fetching
   each candidate and comparing its actual `coordinate_N_label`/
   `coordinate_N_indicator` fields, not just a fuzzy title search.
2. **If found:** attached immediately, at container-creation time
   (via `container_locations` in the same POST that creates the
   top_container).
3. **If not found:** the coordinate combination is recorded, and — if
   a *new* top_container was created for this row — that container's
   URI is added to a "pending relink" list. Nothing about creating
   the top_container or the archival object is blocked by a missing
   location; migration proceeds normally, just without that link for
   now.

At the end of the run:
- `logs/missing-locations-<timestamp>.xlsx` — one row per **distinct**
  missing coordinate combination (not per spreadsheet row), with a
  count of how many rows referenced it, an example title for context,
  and a blank column for staff to note the ArchivesSpace URI once
  they've created it.
- `pending_relink.json` — one entry per top_container that was
  created without its location, accumulating across runs (never
  overwritten, only appended to) so this stays accurate even if you
  run `migrate.py` multiple times before getting to Phase 2.

**Phase 2 — staff creates the real Location records** in ArchivesSpace
from the missing-locations spreadsheet, at their own pace, with real
review (building assignment, whether some of the "distinct missing
locations" are actually typos that should merge, etc.).

**Phase 3 — re-link:**

```bash
python relink_locations.py --pending-relink-file pending_relink.json --dry-run
python relink_locations.py --pending-relink-file pending_relink.json
```

For every pending entry, re-searches for a now-existing match; if
found, fetches the top_container fresh (so a concurrent edit isn't
clobbered) and updates it with the location link. Entries still
unresolved stay in the file — safe to re-run repeatedly as more
locations get created over time; each run only ever narrows the
file, never duplicates or loses entries.

## Scoping notes

- Location resolution/attachment only happens when a top_container is
  **newly created**. An already-existing, reused container's location
  is never touched by the main migration run — only `relink_locations.py`
  updates existing containers, and only ones already in the pending
  file.
- This is unrelated to the `box.placeholder` mechanism (see
  `CONFIG_REFERENCE.md`) used for "map case / drawer" data — that's a
  deliberate stopgap that doesn't create or search real `Location`
  records at all. If a spreadsheet's location data is reliable enough
  to search/match (like the Range/Bay/Shelf scheme this feature was
  built for), use `location:`, not `box.placeholder`.
- See `KNOWN_LIMITATIONS.md` for the same category of caveat that
  applies to other search-then-verify features in this codebase:
  search-index field-name assumptions should be checked with a small
  `--dry-run` batch before a full run on a new ArchivesSpace instance.
