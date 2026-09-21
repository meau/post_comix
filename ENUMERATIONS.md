# Enumerations: what values are actually valid?

`CONFIG_REFERENCE.md` tells you which YAML **fields** exist
(`extent_type`, `role`, `relator`, `level`, ...). This doc is about
the other half of the question: what **values** are legal inside
them.

## The authoritative source is your own instance, not this file

ArchivesSpace stores these as editable "Controlled Value Lists" —
staff with the right permission can add, remove, or rename values via
the staff UI ("Manage Controlled Value Lists"). That means the real
answer for *your* instance can differ from ArchivesSpace's stock
defaults, and differ between your test and production instances if
anyone's customized one but not the other.

**Get the live, current list:**

```bash
python list_enumerations.py                  # everything
python list_enumerations.py --grep extent     # just extent-related lists
python list_enumerations.py --grep date_label
python list_enumerations.py --grep relator
```

Read-only (`GET /config/enumerations`) — works regardless of whether
your API account has write access, and needs no special setup beyond
the usual `secrets.json`. Or check the same thing in the staff UI
under System → Manage Controlled Value Lists.

## Quick reference (stock ArchivesSpace defaults)

Everything below is what this project has actually used and, in most
cases, independently confirmed against ArchivesSpace's own source or
documentation while building it — noted per section. Treat this as a
fast starting point, not a substitute for checking your own instance,
especially anywhere marked "commonly available" rather than
"confirmed."

### `agents` / `publisher` → `role`

**Confirmed via ArchivesSpace source:** this list is short and fixed
in a way the others aren't —

- `creator`
- `source`
- `subject`

There is no dedicated "publisher" role — that's why `publisher`/
`agents` entries use `role: creator` qualified by a `relator` code
instead (see below).

### `agents` / `publisher` → `relator`

MARC relator codes — a large standard list, not an ArchivesSpace-
specific enumeration. Ones actually used in this project's configs so
far, confirmed as sensible for their context:

- `pbl` — Publisher
- `cre` — Creator
- `arc` — Architect

Others you'll likely want: `aut` (Author), `edt` (Editor), `ill`
(Illustrator), `pht` (Photographer), `com` (Compiler). The full list
is the [MARC Relator Terms and Codes](https://www.loc.gov/marc/relators/relaterm.html)
— any valid MARC relator code should work.

### `extent` → `extent_type`

**Confirmed via ArchivesSpace source/DACS citation:** `items` (DACS
2.5.4 explicitly lists "number of items" as a standard extent
expression).

**Commonly available, not independently re-confirmed this session:**
`linear_feet`, `cubic_feet`, `volumes`, `photographs`,
`photographic_prints`, `photographic_slides`, `cassettes`, `reels`,
`sheets`, `leaves`, `megabytes`, `gigabytes`, `terabytes`.

### `date` → `label`

**Confirmed via ArchivesSpace source + DACS 2.4.3:** `creation`,
`publication` (DACS explicitly calls out publication dates as the
right choice for published items — this project's own past
correction, see the mapping-decisions review).

**Commonly available:** `broadcast`, `copyright`, `deaccession`,
`agent_relation`, `usage`, `other`, `questionable`.

### `date` → `date_type` (set automatically by `date_parser.py`, not user-configured)

`single`, `inclusive` — these two are all this codebase ever
produces; `bulk` is also a standard ArchivesSpace value (used by
`resource_builder.py` for `bulkDates`) but not something
`date_parser.py` infers on its own.

### top-level `level`, and `hierarchy[].level`

**Confirmed via ArchivesSpace source (Smithsonian/AAA processing
guidance search):** `item`, `file`, `series`, `subseries`,
`collection`, `recordgrp`, `fonds`, `subfonds`, `class`, `otherlevel`.

### `instance_type`, `box.container_type`

**Commonly used, seen across institutional practice manuals:**
`mixed_materials`, `text`, `graphic_materials`, `books`. For
`box.container_type` specifically, `box` and `folder` are the two
values this project's configs actually use (see `KNOWN_LIMITATIONS.md`
on the `box.placeholder` mechanism).

### `scope_notes[].` note type, `physdesc` note type (fixed by this codebase, not user-configured)

**Confirmed via ArchivesSpace source:** `scopecontent` (multipart,
used for every `scope_notes` entry), `physdesc` (singlepart, used for
the `physdesc` field).

`resource_builder.py`'s narrative-note fields (see
`HIERARCHY_AND_RESOURCES.md`) use standard EAD/DACS note types —
`bioghist`, `accessrestrict`, `userestrict`, `prefercite`,
`arrangement`, `acqinfo`, `processinfo`, `custodhist`, `accruals`,
`appraisal`, `odd`, `relatedmaterial`, `separatedmaterial`,
`originalsloc`, `otherfindaid`, `altformavail` — these are standard
EAD elements, not independently re-verified one-by-one against
ArchivesSpace's enumeration this session, but each is a well-
established EAD/DACS element name.

### `genre` → subject `term_type` / `source` (fixed by `genres.py`, not user-configured)

**Confirmed via ArchivesSpace source:** `term_type: genre_form`,
`source: aat` (matched) or `source: local` (unreconciled fallback).
