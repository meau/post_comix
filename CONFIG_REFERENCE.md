# Mapping config reference

This is the complete field list for a `configs/*.yaml` mapping
config, used by `migrate.py`. Don't hand-write one from scratch —
run `suggest_mapping.py` against your sheet first (see the main
[README](../README.md)) and edit its draft output.

This doc covers which **fields** exist. For what **values** are
actually valid inside fields like `extent_type`, `role`, `relator`,
or `level`, see [ENUMERATIONS.md](ENUMERATIONS.md) — and, for the
authoritative live answer for your own instance, `list_enumerations.py`.

Every field below is optional unless marked **required** — omit
(or set to `null`) anything your spreadsheet doesn't have. A blank
cell for any mapped field is always just skipped (no empty
notes/extents/dates/links get created).

## Top-level

```yaml
name: my_config                 # a human label, shown in logs
sheets: ["Sheet 1", "Sheet 2"]  # required -- every sheet this config applies to
has_header: true                # false for headerless sheets
skip_rows: [2]                  # Excel row numbers to skip entirely (e.g. an embedded instructions row)
level: item                     # ArchivesSpace archival_record_level for every row's archival_object
instance_type: mixed_materials  # ArchivesSpace instance_type for the container instance
publish_default: false          # publish state for new archival_objects (--publish overrides to true)
vocabulary_ref: /vocabularies/1 # used when creating subject records (genre terms) -- almost always the default
```

### `column` values, everywhere below

Any field with a `column:` key accepts either:
- a **string** — looked up by header name (the sheet needs a header row)
- an **integer** — a 0-based positional index (works on headerless
  sheets, or when several sheets share a config but don't use
  *exactly* the same header text — see `configs/ciaraldi_by_title.yaml`
  for a worked example)

`migrate.py` hard-errors before writing anything if a name-based
column doesn't actually exist in a sheet it's asked to process,
rather than silently treating it as blank for every row.

## `title`

```yaml
title:
  column: "Title"
```

## `title_or_digital_object`

For the "this column is usually more title text, but is sometimes
actually a URL" pattern (seen in Brown's publications sheet). A
URL-shaped value is **not** folded into the title — instead it's
used to create/reuse a `digital_object` record, linked to the
archival object as a digital instance.

```yaml
title_or_digital_object:
  column: "fileTitle - data in this column will be merged with Column A to form the complete title"
  separator: " "   # joins title + this column's value, when it's NOT a URL. Default: single space.
```

## `publisher` (shorthand) and `agents` (general)

`publisher` is shorthand for a single corporate creator with
`role: creator`, `relator: pbl` — kept for backward compatibility
with the original Peck Comics config.

```yaml
publisher:
  column: "Publisher"
```

For anything else — multiple creators, personal names, a different
role/relator — use `agents`, a list of any length:

```yaml
agents:
  - column: "nameCorpCreatorLC"
    agent_type: corporate     # or "person"
    role: creator              # ArchivesSpace linked_agent_role enum: creator | source | subject
    relator: arc                # MARC relator code, e.g. "arc" (Architect), "pbl" (Publisher), "cre" (Creator)
  - column: "namePersonCreatorLC"
    agent_type: person
    role: creator
    relator: arc
```

Both `publisher` and every `agents` entry go through the same
reuse → external-authority-match → local-fallback resolution — see
[RECONCILIATION.md](RECONCILIATION.md) for exactly how that works
and what each resulting status means.

## `extent`

```yaml
extent:
  column: "Issues"
  extent_type: items   # ArchivesSpace extent_type enum value
```

## `date`

```yaml
date:
  column: "Human readable dates updated final"
  label: publication    # ArchivesSpace date_label enum value
```

Always parses the free-text column with `date_parser.py` — never
uses a spreadsheet's own pre-split `dateStart`/`dateEnd` columns,
since those are typically coarser than what can be parsed from the
human-readable text (per an explicit decision on this project: prefer
re-parsing over trusting pre-split structured dates). See
`date_parser.py`'s own docstring for the full precision/range rules;
the short version is that `begin`/`end` are formatted at whatever
precision is actually known (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`) and
a single (non-range) date never gets an `end` at all.

## `physdesc`

```yaml
physdesc:
  column: "Size"
```

A `physdesc` (physical description) note — maps to EAD's `<physdesc>`
in `<did>`, not a general narrative note.

## `genre`

Reconciles a free-text format/resource-type value against Getty AAT
and links it to the archival object as a `genre_form` subject.

```yaml
genre:
  column: "typeOfResource"
```

See [RECONCILIATION.md](RECONCILIATION.md) for the resolution logic
(same reuse → AAT-match → local-fallback shape as agents).

## `box`

```yaml
box:
  column: "New Box Number"
  compound: false           # true for values like "163: Box 1" -- see below
  barcode_column: null      # a SEPARATE column holding the container's barcode (only used when creating a new container)
  container_type: box       # ArchivesSpace top_container type
  placeholder: false        # true bypasses the raw value entirely -- see below
```

**Plain values** (`compound: false`, the default): a bare number
(`1`, `1.0`) or short label is normalized straight into the
container's `indicator`.

**Compound values** (`compound: true`), for values like
`"163: Box 1"` — a leading ID, a colon, then a label:

```yaml
box:
  column: "Box"
  compound: true
  strip_prefix: "Box "               # strip this off the label half, if present
  indicator_source: leading_number   # or "label" -- which half is the real box identity?
  label_as_barcode: false            # (leading_number mode) store the label half as a barcode?
  barcode_from_prefix: false         # (label mode) store the leading number as a barcode?
```

Think carefully about `indicator_source` rather than defaulting it —
in the Ciaraldi data, the leading number turned out to be the real,
collection-wide box identity (it climbs steadily and uniquely across
every sheet), while the "Box N" label resets to 1 on every title and
would otherwise silently merge different physical boxes from
different titles that both happen to be "their Box 1". Check the
count of distinct top containers created in a `--dry-run` before
trusting either direction.

**Barcode in its own column** (`barcode_column`), for templates where
the barcode is a separate, per-box-repeated column rather than
embedded in the box value itself (seen in both Brown files — every
row of the same box reports an identical barcode):

```yaml
box:
  column: "shelfLocator1ID"
  barcode_column: "barcode"
```

Only applied when a container is newly **created** — never
overwrites the barcode on an already-existing, reused container.

**Placeholder containers** (`placeholder: true`), for cases like
"map case / drawer" that are locations, not discrete countable
containers, and don't have a reliable per-row identifier to build a
real container from:

```yaml
box:
  placeholder: true
  placeholder_indicator: "TBD"   # every row using this config shares ONE container with this indicator
  container_type: folder
```

This is a deliberate stopgap — every row sharing this config gets
the *same* placeholder container, not a real per-location one. See
`KNOWN_LIMITATIONS.md` for the fuller reasoning.

## `location`

Matches shelf coordinates against real ArchivesSpace `Location`
records (never auto-created — see
[LOCATIONS.md](LOCATIONS.md) for the full search/match/defer/re-link
workflow this drives).

```yaml
location:
  building: "John Hay Library"
  coordinate_1: {column: "Range", label: "Range"}
  coordinate_2: {column: "Bay", label: "Bay"}
  coordinate_3: {column: "Shelf", label: "Shelf"}
```

## `ignore_column`

Any non-blank value in this column skips the row entirely — no
hierarchy-state update, no archival object, nothing. Mirrors a
convention some source templates (Brown's) build in themselves:
*"Put any data in this column and that specific row will be ignored
by the script."*

```yaml
ignore_column:
  column: "Ignore"
```

## `scope_notes`

Any number of entries, each becomes its own `scopecontent` note:

```yaml
scope_notes:
  - column: "Listing of Issues Held"
    prefix: "Includes "
    suffix: ""                    # appended after the value, before ensure_trailing_period
    ensure_trailing_period: true  # add a "." if the assembled content doesn't already end with one
  - column: "Notes"
```

## `hierarchy`

For spreadsheets that encode a real series/subseries structure
rather than a flat list — see
[HIERARCHY_AND_RESOURCES.md](HIERARCHY_AND_RESOURCES.md) for the full
walkthrough of how rows are interpreted.

```yaml
hierarchy:
  - level: series
    id_column: "seriesID"
    title_column: "seriesTitle"
  - level: subseries
    id_column: "subSeriesID"
    title_column: "subSeriesTitle"
```

Any row where `mapping.title`'s column is blank is treated as a pure
hierarchy-header row (no archival object created for it, but the
hierarchy state still updates). A row can also do both at once —
open a new series *and* be a file itself — if both are populated on
the same row.
