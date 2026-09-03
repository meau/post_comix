# Hierarchy and resource creation

These are the two features for spreadsheets that need more structure
than "flat rows under one existing resource."

## Series/subseries hierarchy (`hierarchy.py`)

Some spreadsheets encode a real institutional-records series
structure in-line, rather than a flat list — e.g. Brown's
publications sheet, where `seriesID`/`seriesTitle` columns mark
series boundaries between file rows. Configure this with `hierarchy:`
in the mapping config (see `CONFIG_REFERENCE.md`).

`HierarchyWalker` processes rows **in order**, tracking "current
series" (and any deeper levels) as it goes. The actual rules, learned
from real data rather than assumed up front:

- Whenever a level's `id_column`/`title_column` is **non-blank** on a
  row, that level's context updates (creating or reusing that series
  node), and any *deeper* level's context resets — a new series
  shouldn't inherit the old subseries.
- A row can **both** open a new series **and** be a file itself, if
  both the series columns and the file's title column are populated
  on the same row. These aren't mutually exclusive.
- A row with **blank** series columns simply inherits whatever
  series was last declared above it.
- A `seriesID` can be closed and **reopened** later in the same
  sheet (interrupted by other series in between) — the same node is
  reused each time, not duplicated. Verified against a real case in
  Brown's data where one series was interrupted by two different
  others and correctly resumed the same node both times.

**Every row must be walked in order, even ones already marked
successful in `state.json`** — skipping a row's *hierarchy update*
because its *file creation* already succeeded would lose track of
"current series" for every row after it on a resumed run. `migrate.py`
always calls `walker.update()` before checking whether to skip the
row's file-creation step.

Idempotency across separate runs (not just within one) is best-effort:
a series/subseries node is looked up by title, scoped to its parent,
via an ArchivesSpace search verified by fetching each candidate and
checking its actual `parent` ref. This depends on search-index
behavior that wasn't independently confirmed against a live instance
— see `KNOWN_LIMITATIONS.md`.

## Resource creation (`resource_builder.py`, `create_resource.py`)

Some source templates include a `Collection-Level Data` sheet: one
row per resource-level field (title, call number, dates, extent,
finding-aid metadata, narrative notes, creator), with the archivist's
actual entry in a **fixed column position** (index 4, "Data Entry") —
everything else in the row (Required?, Repeatable?, Field
Instructions) is guidance text for the human filling it out, not
data. Reading the wrong column here silently picks up instructional
placeholder text as if it were real data — `resource_builder.py`
reads strictly from that fixed position.

This is a template-specific reader (built for Brown's own field-name
vocabulary), not a generic YAML-configurable mapping like the row
engine — the field set is fixed and self-documented by the sheet's
own instructions. If a different institution's collection-level
template shows up with a different vocabulary, `resource_builder.py`
is the file to extend or fork.

### Usage

```bash
python create_resource.py --xlsx YourFile.xlsx --dry-run
python create_resource.py --xlsx YourFile.xlsx
```

On success, prints the new resource's URI — use that with
`migrate.py`'s resource prompt to add that collection's rows to it.

### What it does, in order

1. Reads `Collection-Level Data` into a flat `{field_key: value}` dict.
2. **Checks whether a matching resource already exists** — by
   `callNumber` (mapped to ArchivesSpace's `id_0`) if present,
   otherwise falling back to an exact title match (a weaker check —
   if `callNumber` is blank, re-running this after a title change
   could create a duplicate). **Never updates** an existing resource;
   if one is found, it just prints the URI.
3. **Refuses to create** (outside `--dry-run`) if required fields are
   missing: `callNumber`, `title`, at least one of `inclusiveDates`/
   `bulkDates`, and `sizeExtent`. ArchivesSpace would very likely
   reject an incomplete payload anyway — this catches it before the
   API call with a clear message instead of a cryptic validation
   error.
4. Builds the resource payload — narrative notes (abstract, bioghist,
   scope, access/use restrictions, etc.) mapped to their correct EAD/
   DACS note types, creator agent resolved through the same
   reconciliation logic as row-level agents (see
   `RECONCILIATION.md`), language mapped to an ISO code, extent
   parsed from free text (`"6 linear feet"` → `number: "6",
   extent_type: linear_feet"`).
5. Every field it *doesn't* use gets a reason logged, not silently
   dropped — e.g. `MARCRepositoryCode` and repository address/name
   fields describe the *repository* (which already exists — you set
   `repository_id` in `secrets.json`), not the resource being
   created.

### Known gaps in this feature

The "Added entry" fields (`addedEntryPersonLC`, `addedEntrySubjectLC`,
`addedEntryGenreAAT`, etc.) — additional linked agents and subjects
at the resource level — are recognized but not wired to any resolver
yet. If a sheet has real values in these, they're logged as a warning
("not wired up yet — ask if you need this built") rather than
silently dropped, but nothing gets linked. None of the source files
seen so far have used them, so this hasn't been built out — flag it
if you hit one that does.

`level: "collection"` and `finding_aid_status: "unprocessed"` are
hardcoded defaults in `resource_builder.py`, since nothing in the
sheet specifies them. Worth a look if that's not always the right
default for your collections.
