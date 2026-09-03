# Peck Comics → ArchivesSpace migration

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
/ `"YYYY"` expression, with `begin` (and `end`, for ranges) formatted at
**whatever precision is actually known** — `YYYY`, `YYYY-MM`, or
`YYYY-MM-DD` — never padded out to a fake day-of-month or month-of-year.
A single (non-range) date only gets a `begin`; no `end` is added.

| Input | expression | begin | end | date_type |
|---|---|---|---|---|
| `1999` | `1999` | `1999` | — | single |
| Excel date `5/1/1991` (day=1) | `1991 May` | `1991-05` | — | single |
| Excel date `5/14/1991` | `1991 May 14` | `1991-05-14` | — | single |
| `April 1992 - July 1992` | `1992 April - 1992 July` | `1992-04` | `1992-07` | inclusive |
| `1973-1975` | `1973 - 1975` | `1973` | `1975` | inclusive |

Rules used:

- **Excel date cells** (e.g. a cell showing `5/1/1991`): if the day is
  the 1st, it's treated as **month precision** (`"1991 May"`) since a
  day-of-1 is almost always Excel's placeholder rather than a real
  reported day. A day other than the 1st is kept as exact-day precision.
- **Bare years** (`2007`) → year precision, `begin: "2007"`, no `end`.
- **Text ranges** (`"April 1992 - July 1992"`, `"Apr - Aug 1999"`,
  `"1973-1975"`) → parsed into two sides; if only one side has a year,
  the other side borrows it (e.g. `"Apr - Aug 1999"` → April 1999 –
  August 1999). `date_type` is set to `inclusive`, and `begin`/`end`
  are each formatted at their own side's precision.
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
   for a match, via two lookup paths:
   - the exact authorized-heading redirect
     (`id.loc.gov/authorities/names/label/<name>`) — the strongest
     signal, since it only resolves when the spreadsheet string
     already *is* (or is a registered variant of) an authorized
     heading;
   - falling back to `suggest2` search, accepted only on a
     normalized-exact, unambiguous match.

   Both are intentionally **conservative**: a match is only accepted
   once you ignore case, whitespace, and periods/commas (so "E.C.
   Publications" matches "E.C Publications", but "Marvel" will
   **not** auto-match "Marvel Comics" — that kind of loose call is
   left to you). Network calls retry with backoff on timeouts and on
   LC's own retryable errors (429/500/502/503/504), so a transient
   hiccup doesn't get mistaken for "not in LC NAF."

   If a confident match is found:
   - If ArchivesSpace **already has an agent authorized against that
     exact LC URI** (created from a *differently worded* spreadsheet
     publisher string for the same real-world publisher), that
     existing agent is reused rather than creating a duplicate
     ArchivesSpace would reject anyway.
   - Otherwise, a new agent is created with `source: naf` and
     `authority_id` set to the LC URI, using the record's real
     authorized label (fetched from the record itself, not just
     whatever text a search result handed back).

   **If the LC lookup fails at the network level** (not "no match,"
   but "id.loc.gov couldn't be reached reliably after retries"), that
   is logged distinctly and the publisher falls through to step 3 —
   it is explicitly **not** treated as proof the publisher isn't in
   LC NAF.
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

Every resolution decision is written to the run log with one of these
statuses, so you can review afterward exactly what happened for each
publisher:

| Status | Meaning |
|---|---|
| `linked_existing` | Reused an ArchivesSpace agent already there, matched by name |
| `linked_existing_by_authority_id` | Reused an ArchivesSpace agent already authorized against this LC URI (caught a same-publisher/different-spelling collision) |
| `linked_loc` | Created a new agent authorized against LC NAF |
| `created_local` | Created a local DACS agent — LC NAF was checked and confidently has no match |
| `created_local_lc_lookup_failed` | Created a local DACS agent because the **LC lookup itself failed** (network/timeout) — **not** a confirmed absence from LC NAF. Worth a manual check or a re-run. |

**id.loc.gov note:** this lookup calls a public LC web service.
Failures retry automatically, but if you're processing hundreds of
unique publishers you may still see the occasional
`created_local_lc_lookup_failed`. Search the log for that status
string after a run and double check those publishers by hand (or
just re-run with `--force` on the affected rows once the network
issue clears — the collision guard above means re-running won't
create duplicates even for publishers that already got a local agent).

### Top containers (boxes)

Box numbers in the Comics sheet (1–13) are checked against existing
ArchivesSpace top containers first, scoped to **the target resource**
(not just the repository) — so if some other collection in the same
repository already has its own box "1", this script will never link
to it. Only a box already linked to *this* resource is reused;
otherwise a new one is created with `type: box` and no barcode.

This resource-scoping relies on ArchivesSpace's search index exposing
which resource(s) a top container is linked to, via a facet field
(`collection_uri_u_sstr`). This is the same mechanism the staff UI
uses for its own "containers linked to this resource" filtering, but
field names can vary slightly across ArchivesSpace versions/plugins.
The script fails safe: if that filtered search doesn't confirm a
match, it **creates a new container** rather than risk reusing the
wrong one — so double check the `logs/run-*.log` output after your
`--dry-run` to make sure boxes are being reused for repeat rows the
way you expect (you should see the same box's indicator show up as
`"reused_this_run"` or `"reused_existing"` on later rows, not
`"created"` again every time). If you see boxes being needlessly
re-created, let me know and I'll adjust the field name for your
instance's search index.

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
| `migrate_comics.py` | Main script / entry point |
| `date_parser.py` | Date string → expression/begin/end (no network) |
| `aspace_client.py` | Thin ArchivesSpace REST client, with dry-run support |
| `loc_client.py` | Conservative id.loc.gov corporate-name lookup |
| `agents.py` | Publisher → agent resolution logic |
| `containers.py` | Box number → top container resolution logic |
| `resource_url.py` | Parses any ArchivesSpace URL flavor into a resource ref |
| `state.py` | Run-state tracking (resume/skip logic) |
| `secrets.json.example` | Template — copy to `secrets.json` and fill in |
