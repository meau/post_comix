"""
genres.py

Resolves a free-text format/resource-type value (e.g. "Graphic
materials") to an ArchivesSpace `subject` record (term_type
genre_form), sourced from Getty AAT.

This is now a thin wrapper around subjects.py's general resolver --
kept as its own module (rather than inlining `resolve_subject(...,
"genre_form", "aat", ...)` at every call site) so existing callers
(migrate.py's row-level typeOfResource handling) don't need to change,
and so the specific "row-level genre, always AAT" use case stays easy
to find. See subjects.py for the actual reuse -> external-match ->
local-fallback logic and its status strings.
"""

from subjects import resolve_subject


def resolve_genre_term(term_name: str, client, genre_cache: dict, log,
                        vocabulary_ref: str = "/vocabularies/1") -> dict:
    return resolve_subject(term_name, "genre_form", "aat", client, genre_cache, log, vocabulary_ref)
