# Reconciliation: agents and subject terms

Four modules — `agents.py` (corporate), `people_agents.py`
(personal), `genres.py` (row-level genre/format, always AAT), and
`subjects.py` (the general resolver behind both `genres.py` and every
resource-level "added entry" field) — all resolve a free-text
spreadsheet value to an ArchivesSpace record using the same
three-tier shape:

1. **Reuse an existing ArchivesSpace record**, matched by name/term
   text (punctuation- and case-insensitive). Nothing new is created.
2. **Match against an external authority** — LC Name Authority File
   for agents, and for subjects one of Getty AAT, LC Subject Headings
   (LCSH), the Thesaurus for Graphic Materials (TGM), or the RBMS
   Controlled Vocabulary (RBMSCV), depending which the field calls
   for (see `subjects.py`'s docstring and
   `HIERARCHY_AND_RESOURCES.md`'s added-entry section) — and create a
   new record sourced from it if found.
3. **Otherwise, create a local record** — for agents, described per
   DACS (`source: local`, `rules: dacs`); for subject-type records,
   an unreconciled local term, clearly logged as such so it's easy to
   find and reconcile by hand later. A field can also request "local"
   directly, skipping lookup entirely — see the `force_local`/
   `"local"` authority note below.

This shape is deliberately conservative at every step: matching
requires either an authority's own "strong match" flag or a single,
unambiguous, normalized-exact text match — never fuzzy/"sounds like"
matching. "Marvel" will not auto-match "Marvel Comics." An ambiguous
result (more than one equally-good candidate) is treated the same as
no match: fall through to the next tier.

## Result status strings

Every resolution function returns `{"uri": ..., "status": ...}`. The
status is written to the run log and is the fastest way to audit a
run afterward — e.g. grep the log for `created_local` to see
everything that fell all the way through to an unreconciled local
record.

| Status | Meaning |
|---|---|
| `linked_existing` | Reused a record already in ArchivesSpace, matched by name/term text |
| `linked_existing_by_authority_id` | Reused a record already authorized against this exact external URI (catches two differently-worded spreadsheet values that turn out to be the same real-world thing) |
| `linked_loc` / `linked_aat` / `linked_lcsh` / `linked_tgm` / `linked_rbmscv` | Created a new record authorized against that external source (`subjects.py` builds this string as `linked_<authority>`) |
| `created_local` | Created a local record — the external source was checked and confidently has no match, or the field's own authority was `"local"` (no lookup attempted at all — see below) |
| `created_local_lc_lookup_failed` / `created_local_aat_lookup_failed` / `created_local_<lcsh\|tgm\|rbmscv>_lookup_failed` | Created a local record because **the lookup itself failed** (network/timeout) — this is *not* a confirmed absence from the external source. Worth a manual check or a re-run once the network issue clears. |

`force_local` (on `resolve_publisher_agent`/`resolve_person_agent`)
and the `"local"` authority value (on `subjects.resolve_subject`)
skip external lookup entirely, rather than attempting one and
discarding a match — used for fields whose own name already declares
no authority is being claimed (e.g. an added-entry `"...Local"`
field). This is a different thing from "the lookup found nothing" —
both end up `created_local`, but only one of them actually checked.

That last distinction is the single most important thing to
understand about this system: a network hiccup during an LC or AAT
lookup must never look identical in the log to "we checked and this
publisher genuinely isn't in LC NAF." Conflating those would silently
downgrade real matches to local records with no way to tell the
difference afterward. Every lookup retries with exponential backoff
before giving up, and giving up is always logged distinctly.

## The conflict-recovery safety net

Two different failure modes both end in ArchivesSpace rejecting a
create with a "must be unique" error, and every creation path in this
codebase (corporate/person agents, genre subjects, top containers,
digital objects) catches both the same way: parse the conflicting
record's URI out of the error message (`aspace_client.parse_conflicting_record_uri`)
and reuse it instead of failing that row.

- **Authority collision**: two *differently worded* spreadsheet
  values resolve to the same external authority — e.g. "DC Comics"
  and "National Periodical Publications" both matching the same LC
  NAF entry. The LC/AAT-matched creation paths check for an existing
  record with that authority ID *before* creating, so this is mostly
  caught proactively; the catch is a last-resort backstop.
- **Search-index lag**: a record genuinely was just created (often on
  an earlier row in the very same run), but ArchivesSpace's search
  index hasn't caught up yet, so the "does this already exist"
  search that every resolver does first comes back empty even though
  the record is really there. This is the more common cause in
  practice, and it affects the **local-fallback** creation paths too
  (not just the authority-matched ones) — a local agent, a local
  genre subject, a top container, or a digital object can all hit
  this exact same way, since none of them can check "by authority ID"
  the way an LC/AAT match can. Every one of those paths has the same
  catch-and-reuse wrapped around its create call for this reason.

When this fires, the log says so explicitly ("likely a search-index
lag from a very recent create on an earlier row") rather than framing
it as an authority match, so you can tell the two situations apart
when reviewing a run.

## Person names

`people_agents.py` expects `"Last, First"` (per the source templates
seen so far, which say so explicitly in their own header
instructions). Only the first comma is treated as the surname/
forename boundary — `"Smith, John, Jr."` splits into primary `Smith`
and rest `John, Jr.`, not three parts. A name with no comma at all is
left whole in `primary_name` rather than guessed at.

## Caveats worth knowing

- The LC "known label" redirect lookup isn't restricted by name type
  (personal vs. corporate) — in principle a personal name string
  could redirect to a corporate heading that happens to share the
  exact text. The `suggest2` fallback IS correctly filtered
  (`rdftype=PersonalName` vs. `CorporateName`). This is a
  pre-existing limitation of the redirect endpoint itself, not
  something specific to this codebase.
- The exact placement of an external authority URI on an
  ArchivesSpace `subject` record (unlike `agent` names, which have a
  documented `authority_id` field) was not independently verified
  against ArchivesSpace's live schema. Test a couple of real genre-
  term creates with `--dry-run` and a small real batch before
  trusting this at scale — see `KNOWN_LIMITATIONS.md`.
