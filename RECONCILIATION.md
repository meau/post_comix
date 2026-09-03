# Reconciliation: agents and genre terms

Three modules — `agents.py` (corporate), `people_agents.py`
(personal), `genres.py` (Getty AAT genre/form terms) — all resolve a
free-text spreadsheet value to an ArchivesSpace record using the same
three-tier shape:

1. **Reuse an existing ArchivesSpace record**, matched by name/term
   text (punctuation- and case-insensitive). Nothing new is created.
2. **Match against an external authority** — the LC Name Authority
   File for agents (`loc_client.py`), Getty AAT for genre terms
   (`aat_client.py`) — and create a new record sourced from it if
   found.
3. **Otherwise, create a local record** — for agents, described per
   DACS (`source: local`, `rules: dacs`); for genre terms, an
   unreconciled local subject, clearly logged as such so it's easy to
   find and reconcile by hand later.

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
| `linked_loc` / `linked_aat` | Created a new record authorized against the external source |
| `created_local` | Created a local record — the external source was checked and confidently has no match |
| `created_local_lc_lookup_failed` / `created_local_aat_lookup_failed` | Created a local record because **the lookup itself failed** (network/timeout) — this is *not* a confirmed absence from the external source. Worth a manual check or a re-run once the network issue clears. |

That last distinction is the single most important thing to
understand about this system: a network hiccup during an LC or AAT
lookup must never look identical in the log to "we checked and this
publisher genuinely isn't in LC NAF." Conflating those would silently
downgrade real matches to local records with no way to tell the
difference afterward. Every lookup retries with exponential backoff
before giving up, and giving up is always logged distinctly.

## The authority-ID collision guard

Two *differently worded* spreadsheet values can resolve to the same
external authority record — e.g. "DC Comics" and "National Periodical
Publications" both matching the same LC NAF entry. Without a check,
the second one would try to create a duplicate ArchivesSpace record
authorized against the same URI, which ArchivesSpace rejects outright
("Authority ID must be unique"). Every resolver checks for an
existing record with that authority ID *before* creating, and — as a
last-resort safety net — also catches ArchivesSpace's rejection and
parses the conflicting record's URI out of the error message, reusing
it instead of failing that row.

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
