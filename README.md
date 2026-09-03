# Spreadsheet → ArchivesSpace migration

Migrates collection inventory spreadsheets into ArchivesSpace. The
engine is general-purpose — what it does with any given spreadsheet
is driven entirely by a YAML **mapping config** (`configs/*.yaml`),
so it isn't tied to any one spreadsheet's column layout.

## Which script do I run?

| I want to... | Run |
|---|---|
| Map a new spreadsheet's columns for the first time | `suggest_mapping.py` — drafts a starting config from the header row |
| Migrate spreadsheet rows into an **existing** resource | `migrate.py --config configs/whatever.yaml` |
| Create the **resource record itself** from a `Collection-Level Data` sheet, if it doesn't already exist | `create_resource.py` |
| Migrate the original Peck Comics file specifically | `migrate_comics.py` (unchanged original), or `migrate.py --config configs/peck_comics.yaml` (verified identical, gets ongoing engine fixes) |

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
- If you run any script without a `secrets.json` present, it creates
  the template above for you and stops, so you can fill it in.

`secrets.json` is not encrypted — keep it out of version control.

## Mapping a new spreadsheet

```bash
python suggest_mapping.py --xlsx YourFile.xlsx --sheet "Sheet 1" \
    --out configs/your_config.yaml
```

Reads the header row, guesses which ArchivesSpace concept each column
probably is, and writes a **draft** config with its reasoning. It
never runs a migration itself, and never overwrites an existing file
unless you pass `--force`. Treat every line as a suggestion to
confirm, not a finished config — read the comment block at the top of
what it writes, which lists everything it couldn't confidently place.

For a headerless sheet, add `--no-header`.

Once you have a draft, see **[docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md)**
for the complete field-by-field reference — every option, with
examples. `configs/ciaraldi_sheet1.yaml` and `configs/ciaraldi_by_title.yaml`
are left in this repo as real worked examples of a draft with its
open questions written in.

## Running a migration

Both `migrate.py` and `migrate_comics.py` prompt for (or accept via
`--resource`) the target **resource** — paste any form of the URL/ID
(staff URL, public URL, raw API URI, or just the bare number).

**1. Preview before touching anything:**
```bash
python migrate.py --config configs/your_config.yaml --xlsx YourFile.xlsx --dry-run --limit 5
```
Logs in read-only and does real lookups (so you see what it *would*
match/reuse/create), but never POSTs — every create is logged and
given a fake `DRY-RUN-#` placeholder URI instead.

**2. Test specific rows** by their Excel row number:
```bash
python migrate.py --config configs/your_config.yaml --xlsx YourFile.xlsx --dry-run --rows 45,217,388
```

**3. Run for real:**
```bash
python migrate.py --config configs/your_config.yaml --xlsx YourFile.xlsx
```
Progress goes to `logs/run-<timestamp>.log` and `state.json`. If a
run is interrupted or a row errors out, just re-run the same command
— rows already marked `"success"` are skipped automatically. Use
`--force` to reprocess everything anyway.

### Flags

| Flag | Purpose |
|---|---|
| `--config` | Path to a mapping YAML config (`migrate.py` only) |
| `--sheets` | Comma-separated sheet names, overriding the config's own `sheets:` list |
| `--dry-run` | Preview only, no writes |
| `--limit N` | Only process the first N data rows per sheet |
| `--rows 45,217,388` | Only process these specific Excel row numbers |
| `--force` | Reprocess rows even if already marked successful |
| `--publish` | Create archival objects as *published* (default: unpublished) |
| `--resource <url or id>` | Skip the interactive prompt |
| `--xlsx`, `--secrets`, `--state-file`, `--log-dir` | Override default paths |

## Creating a resource record

If a spreadsheet includes a `Collection-Level Data` sheet (one row
per resource-level field — title, dates, extent, notes, creator,
finding-aid metadata):

```bash
python create_resource.py --xlsx YourFile.xlsx --dry-run
python create_resource.py --xlsx YourFile.xlsx
```

Checks whether a matching resource already exists first (never
creates a duplicate or updates one in place), and refuses to create
an incomplete one — see **[docs/HIERARCHY_AND_RESOURCES.md](docs/HIERARCHY_AND_RESOURCES.md)**
for exactly what it checks and how. On success it prints the new
resource's URI, ready to paste into `migrate.py`'s resource prompt.

## Further reading

- **[docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md)** — every
  mapping config field, with examples.
- **[docs/RECONCILIATION.md](docs/RECONCILIATION.md)** — how
  publisher/person-agent and Getty AAT genre-term resolution work:
  the reuse → external-match → local-fallback pattern, and what
  every status string in the log means.
- **[docs/HIERARCHY_AND_RESOURCES.md](docs/HIERARCHY_AND_RESOURCES.md)**
  — series/subseries hierarchy walking, and the resource-creation
  workflow.
- **[docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md)** — every
  honest gap and unverified assumption in one place. Read this before
  assuming something "just works" on a new spreadsheet shape.

## Files

| File | Purpose |
|---|---|
| `migrate.py` | General entry point — driven by any `configs/*.yaml` |
| `migrate_comics.py` | Original Peck-specific entry point (unchanged, frozen — see Known Limitations) |
| `create_resource.py` | Creates a resource record from a `Collection-Level Data` sheet |
| `suggest_mapping.py` | Drafts a starting config from a sheet's actual headers |
| `mapping.py` | Loads a YAML mapping config, generic column-value access |
| `generic_builders.py` | Config-driven note/extent/date/box/title builders |
| `resource_builder.py` | Collection-Level Data → resource payload |
| `hierarchy.py` | Series/subseries walking for hierarchical sheets |
| `agents.py` | Corporate agent (publisher) resolution |
| `people_agents.py` | Personal agent (individual creator) resolution |
| `genres.py` | Genre/form term resolution (Getty AAT) |
| `digital_objects.py` | URL → digital_object detection/creation |
| `containers.py` | Box/container resolution (plain and compound values) |
| `date_parser.py` | Date string → expression/begin/end (no network) |
| `aspace_client.py` | Thin ArchivesSpace REST client, with dry-run support |
| `loc_client.py` | Conservative id.loc.gov name lookup (corporate + personal) |
| `aat_client.py` | Conservative Getty AAT reconciliation |
| `resource_url.py` | Parses any ArchivesSpace URL flavor into a resource ref |
| `state.py` | Run-state tracking (resume/skip logic) |
| `configs/*.yaml` | One mapping config per spreadsheet shape |
| `secrets.json.example` | Template — copy to `secrets.json` and fill in |
