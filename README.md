# Spreadsheet → ArchivesSpace migration

Migrates comics-collection spreadsheets into ArchivesSpace as child
archival objects of a resource record you already have. The engine is
general-purpose -- what it does with any given spreadsheet is driven
entirely by a YAML **mapping config** (see `configs/`), so it isn't
tied to one spreadsheet's column layout.

Two entry points:

- **`migrate.py`** (general) -- takes `--config configs/whatever.yaml`
  and works with any spreadsheet shape that config describes. Use
  this for anything new.
- **`migrate_comics.py`** (original, Peck-specific) -- kept as-is,
  unchanged, for anyone already using it. `configs/peck_comics.yaml`
  reproduces its exact behavior on the general engine, verified
  byte-for-byte identical across all 409 Comics rows -- so
  `migrate.py --config configs/peck_comics.yaml` is a drop-in
  equivalent if you'd rather standardize on one script going forward.

## Mapping a new spreadsheet

Don't hand-write a config from scratch. Run the suggestion tool
against each sheet shape first:

```bash
python suggest_mapping.py --xlsx YourFile.xlsx --sheet "Sheet 1" \
    --out configs/your_config.yaml
```

It reads the header row, guesses which ArchivesSpace concept each
column probably is (title, publisher, extent, a scope-content note,
physical description, date, box), and writes a **draft** YAML config
with its reasoning -- it never runs a migration itself, and never
overwrites an existing file unless you pass `--force`. Treat every
line as a suggestion to confirm, not a finished config: it flags
low-confidence and unmatched columns explicitly rather than guessing
silently, and columns with the same guessed purpose collide on purpose
(so you resolve them by hand) rather than one silently winning.

For a **headerless** sheet, add `--no-header` -- columns are then
referenced by 0-based position (`column: 3`) instead of by name.
Position-based columns are also the right call when several sheets
share a config but don't use *exactly* the same header text (see
`configs/ciaraldi_by_title.yaml` for a worked example -- one of its
15 sheets spells "Total Issues" as "Total issues"). `migrate.py`
hard-errors before writing anything if a name-based column doesn't
actually exist in a sheet it's asked to process, rather than silently
treating that column as blank for every row -- so a header-text
mismatch like that gets caught immediately, not discovered after a
run completes with suspiciously empty data.

Once a config is generated, **read it end to end** -- the comment
block at the top lists every column the tool couldn't confidently
place, and often surfaces things worth knowing about the data itself
(e.g. a column whose header suggests one thing but whose actual
values are something else entirely). `configs/ciaraldi_sheet1.yaml`
and `configs/ciaraldi_by_title.yaml` are left in this repo as drafts
with their open questions written in, as an example of what that
review looks like in practice -- neither has been run for real yet.

### Mapping config reference

```yaml
name: my_config
sheets: ["Sheet 1", "Sheet 2"]   # every sheet this config applies to
has_header: true                  # false for headerless sheets
level: item
instance_type: mixed_materials
publish_default: false

title:
  column: "Title"        # or an integer for positional access

publisher:                # omit (or null) if there's no publisher column
  column: "Publisher"

extent:
  column: "Issues"
  extent_type: items       # ArchivesSpace extent_type enum value

date:
  column: "Human readable dates updated final"
  label: publication       # ArchivesSpace date_label enum value

physdesc:                 # omit if there's no physical-description column
  column: "Size"

box:                       # omit if there's no container column
  column: "Box Number"
  compound: false           # true for values like "163: Box 1" -- see below

scope_notes:               # any number of these, each becomes its own scopecontent note
  - column: "Listing of Issues Held"
    prefix: "Includes "
    ensure_trailing_period: true
  - column: "Notes"
```

**Compound box values** (`compound: true`), for values like
`"163: Box 1"` -- a leading ID, a colon, then a label:

```yaml
box:
  column: "Box"
  compound: true
  strip_prefix: "Box "         # strip this off the label half, if present
  indicator_source: leading_number  # or "label" -- which half is the real box identity?
  label_as_barcode: false      # (leading_number mode) store the label half as a barcode?
  barcode_from_prefix: false   # (label mode) store the leading number as a barcode?
```

Think carefully about `indicator_source` rather than defaulting it:
in the Ciaraldi data, the leading number turned out to be the real,
collection-wide box identity (it climbs steadily and uniquely across
every sheet), while the "Box N" label resets to 1 on every title and
would otherwise silently merge different physical boxes from
different titles that both happen to be "their Box 1" -- exactly the
kind of thing worth catching with a `--dry-run` and a look at how many
distinct top containers actually get created, before a real run.

## Peck Comics — original migration

Migrates the **Comics** sheet of `Copy_of_Peck_Comics.xlsx` into ArchivesSpace
as child archival objects of a resource record you already have.

Only the "Comics" tab is handled. "Mad Comics," "Peck Gift MISC," and
"Ephemera" are intentionally out of scope for this script.

## Setup

```bash
pip install -r requirements.txt
cp secrets.json.example secrets.json
```

Edit `secrets.json`:

```json
{
  "api_url": "https://your-instance.example.org:8089",
  "repository_id": "2",
  "username": "your_api_user",
  "password": "your_api_password"
}
```

- `api_url` is the ArchivesSpace **backend/API** URL (not the staff or
  public front end) — it usually ends in `:8089`.
- `repository_id` is the numeric ID of the repository these records
  belong to.
- If you run the script without a `secrets.json` present, it will
  create the template above for you and stop, so you can fill it in.

`secrets.json` is not encrypted — keep it out of version control.

## Usage

The script always prompts for (or accepts via `--resource`) the
**resource** these rows should be created under. You can paste *any*
form of the URL/ID:

- staff URL: `https://staff.example.org/resources/123`
- public URL: `https://example.org/repositories/2/resources/123`
- raw API URI: `/repositories/2/resources/123`
- just the bare number: `123`

### 1. Preview before touching anything

```bash
python migrate_comics.py --dry-run --limit 5
```

This logs in read-only, does real lookups against ArchivesSpace and
id.loc.gov (so you can see what it *would* match/reuse/create), but
never sends a POST — every create is logged and given a fake
`DRY-RUN-#` placeholder URI instead.

### 2. Test specific rows

If a few rows look tricky, test them by their **Excel row number**
(the row number as it appears when you open the spreadsheet, header
row = 1):

```bash
python migrate_comics.py --dry-run --rows 45,217,388
```

### 3. Run for real

```bash
python migrate_comics.py
```

Progress is written to `logs/run-<timestamp>.log` and to `state.json`.
If the run is interrupted or a row errors out, just run the same
command again — rows already marked `"success"` in `state.json` are
skipped automatically, so you won't get duplicates. Use `--force` to
reprocess everything anyway.

### Flags

| Flag | Purpose |
|---|---|
| `--dry-run` | Preview only, no writes |
| `--limit N` | Only process the first N data rows |
| `--rows 45,217,388` | Only process these specific Excel row numbers |
| `--force` | Reprocess rows even if already marked successful |
| `--publish` | Create archival objects as *published* (default: unpublished, so you can review first) |
| `--resource <url or id>` | Skip the interactive prompt |
| `--xlsx`, `--sheet`, `--secrets`, `--state-file`, `--log-dir` | Override default paths |

## Field mapping

| Spreadsheet column | ArchivesSpace field |
|---|---|
| Title | `archival_object.title` |
| Publisher | `linked_agents` → `agent_corporate_entity`, `role: creator`, `relator: pbl` (see Agent resolution below) |
| Issues | `extents[0]`: `number` = value, `extent_type: items`, `portion: whole` |
| Listing of Issues Held | A `scopecontent` note: `"Includes " + value + "."` |
| Notes | A **second, separate** `scopecontent` note, verbatim |
| Human readable dates updated final | `dates[0]`: see Date parsing below |
| Computer date Begin/End | Ignored, as instructed |
| Size | A `physdesc` (physical description) note, verbatim |
| New Box Number | `instances[0].sub_container.top_container` — reused if a top container with that indicator already exists, otherwise created (`type: box`, no barcode) |

Blank cells are simply skipped — no empty notes/extents/dates are created.

Archival objects are created flat, directly under the resource you
specify (no intermediate series). Level is set to `item`.

### Date parsing

Per your spec, dates are normalized to a `"YYYY Month D"` / `"YYYY Month"`
/ `"YYYY"` expression, with `begin`/`end` always populated as `YYYY-MM-DD`.

Rules used:

- **Excel date cells** (e.g. a cell showing `5/1/1991`): if the day is
  the 1st, it's treated as **month precision** (`"1991 May"`) since a
  day-of-1 is almost always Excel's placeholder rather than a real
  reported day. A day other than the 1st is kept as exact-day precision.
- **Bare years** (`2007`) → year precision (`begin`/`end` = Jan 1 /
  Dec 31 of that year).
- **Text ranges** (`"April 1992 - July 1992"`, `"Apr - Aug 1999"`,
  `"1973-1975"`) → parsed into two sides; if only one side has a year,
  the other side borrows it (e.g. `"Apr - Aug 1999"` → April 1999 –
  August 1999). `date_type` is set to `inclusive`.
- **Seasons** (`"Fall 1991 - Spring 1993"`, `"Winter 1988"`) → per your
  instruction, these collapse to **year-only** precision — no month is
  guessed from the season word.
- A date that can't be confidently parsed (only one found in the whole
  Comics sheet: the literal text `"unknown"`) is **skipped** — the
  archival object is still created, just without a `dates` subrecord —
  and logged as a warning so you can add it by hand.

`date_parser.py` has no ArchivesSpace/network dependency, so you can
sanity-check it anytime:

```bash
python3 -c "from date_parser import parse_date_cell; print(parse_date_cell('Apr - Aug 1999'))"
```

### Agent resolution (Publisher)

For each unique publisher name, in order:

1. **Search ArchivesSpace** for an existing `agent_corporate_entity`
   whose name matches (punctuation/case-insensitive). If found, it's
   reused and linked — nothing new is created.
2. **Search id.loc.gov** (Name Authority File, corporate names only)
   for a match. This is intentionally **conservative**: it only
   accepts a hit if the LC authorized label matches the spreadsheet's
   publisher name once you ignore case, whitespace, and periods/commas
   (so "E.C. Publications" matches "E.C Publications", but "Marvel"
   will **not** auto-match "Marvel Comics" — that kind of loose call is
   left to you). If exactly one such match is found, a new agent is
   created with `source: naf` and `authority_id` set to the LC URI.
3. **Otherwise, a local agent is created**, described per DACS:

   | Field | Value |
   |---|---|
   | `names[0].primary_name` / `sort_name` | The publisher string, trimmed |
   | `names[0].source` | `local` |
   | `names[0].rules` | `dacs` |
   | `publish` | `true` |
   | `agent_type` | `agent_corporate_entity` |

   Linked to the archival object as `role: creator`, `relator: pbl`
   (MARC relator code for "Publisher" — let me know if you'd rather
   this be free text instead of the relator code).

Every resolution decision (reused / LC match / local-created) is
written to the run log, so you can review afterward exactly which
publishers got authorized LC records vs. local ones.

**id.loc.gov note:** this lookup calls a public LC web service.
It's rate-limited by LC's own service and by nothing on our end;
if you're processing hundreds of unique publishers you may see
occasional lookup failures — those fail safe (fall through to local
agent creation) rather than crashing the run, but it's worth skimming
the log afterward for publishers that ended up "created_local" that
you *expected* to find in LC, in case it was a transient failure
rather than a real absence.

### Top containers (boxes)

Box numbers in the Comics sheet (1–13) are checked against existing
ArchivesSpace top containers first (by indicator, within your target
repository) and reused if found; otherwise a new one is created with
`type: box` and no barcode.

## Assumptions worth double-checking

These were reasonable defaults where the mapping was silent — flag
anything you want changed before a full live run:

1. **Level**: every row becomes an `item`-level archival object.
2. **Publish state**: new archival objects default to **unpublished**
   (pass `--publish` to change that). Newly created **local** agents
   are published immediately, per your instruction; LC-matched agents
   are also published (they're authorized records).
3. **Note type for Size**: `physdesc` (Physical Description).
4. **Two separate scope and contents notes** (Listing of Issues Held,
   then Notes), not merged.
5. **Instance type**: `mixed_materials`, wrapping the box's top
   container.
6. **Nesting**: rows are created flat, directly under the resource —
   no intermediate series/box-level archival object.

## Files

| File | Purpose |
|---|---|
| `migrate.py` | General entry point — driven by any `configs/*.yaml` |
| `migrate_comics.py` | Original Peck-specific entry point (unchanged, still works) |
| `mapping.py` | Loads a YAML mapping config, generic column-value access |
| `generic_builders.py` | Config-driven note/extent/date/box builders used by `migrate.py` |
| `suggest_mapping.py` | Drafts a starting config from a sheet's actual headers |
| `configs/*.yaml` | One mapping config per spreadsheet shape |
| `date_parser.py` | Date string → expression/begin/end (no network) |
| `aspace_client.py` | Thin ArchivesSpace REST client, with dry-run support |
| `loc_client.py` | Conservative id.loc.gov corporate-name lookup |
| `agents.py` | Publisher → agent resolution logic |
| `containers.py` | Box number → top container resolution logic |
| `resource_url.py` | Parses any ArchivesSpace URL flavor into a resource ref |
| `state.py` | Run-state tracking (resume/skip logic) |
| `secrets.json.example` | Template — copy to `secrets.json` and fill in |
