"""
resource_builder.py

Builds (and optionally creates) an ArchivesSpace `resource` record
from a "Collection-Level Data" style sheet: one field per row, with
the field's machine-readable key in the first column and the
archivist's actual entry in a fixed "Data Entry" column -- everything
else in the row (Required?, Repeatable?, Field Instructions) is
guidance text for the human filling it out, not data.

This is a template-specific reader (built for Brown's own field-name
vocabulary), not a generic YAML-configurable mapping like the row
engine in migrate.py -- the field set is fixed and self-documented by
the sheet's own instructions, so there's nothing to make configurable
yet. If a different institution's collection-level template shows up
with a different vocabulary, this is the file to extend or fork.

Known limitation: several of Brown's own "Added entry" fields
(subjects, additional creators) aren't wired up yet -- they're
present in FIELD_MAP as ignored/logged only. Ask before using this on
a sheet that actually has them filled in.
"""

DATA_ENTRY_COLUMN_INDEX = 4  # fixed position in this template: key, Required?, Repeatable?, Instructions, Data Entry

# Fields ArchivesSpace's resource schema needs before it will accept
# a create -- confirm these are filled in Collection-Level Data
# before attempting a real (non-dry-run) creation.
REQUIRED_FIELDS = ["callNumber", "title", "inclusiveDates_or_bulkDates", "sizeExtent"]

# Narrative note fields -> (ArchivesSpace note type, singlepart vs multipart)
NOTE_FIELD_MAP = {
    "abstract": ("abstract", "singlepart"),
    "bioHistNote": ("bioghist", "multipart"),
    "scopeNote": ("scopecontent", "multipart"),
    "conditionsUse": ("userestrict", "multipart"),
    "conditionsAccess": ("accessrestrict", "multipart"),
    "preferredCitation": ("prefercite", "multipart"),
    "arrangementNote": ("arrangement", "multipart"),
    "acquisitionInformation": ("acqinfo", "multipart"),
    "processingInformation": ("processinfo", "multipart"),
    "custodialHistory": ("custodhist", "multipart"),
    "accruals": ("accruals", "multipart"),
    "appraisal": ("appraisal", "multipart"),
    "generalNote": ("odd", "multipart"),
    "relatedMaterials": ("relatedmaterial", "multipart"),
    "separatedMaterials": ("separatedmaterial", "multipart"),
    "locationOriginals": ("originalsloc", "multipart"),
    "otherFindingAids": ("otherfindaid", "multipart"),
    "otherFormats": ("altformavail", "multipart"),
}

# Fields explicitly ignored, with why:
IGNORED_FIELDS = {
    "MARCRepositoryCode": "identifies the REPOSITORY, not this resource -- your repository already exists (repository_id in secrets.json)",
    "address": "repository contact info, not resource content",
    "repositoryCorporateName": "repository identity, not resource content",
    "repositoryCorporateSubarea": "repository identity, not resource content",
    "repositoryAddress": "repository contact info, not resource content",
    "publisher": "finding-aid publisher -- no direct ArchivesSpace resource field found for this; flag if you need it recorded somewhere",
    "RIAMCOBrowsingTerm": "the sheet's own instructions say \"Do not use\"",
    "addedEntryTitle": "not yet wired up -- ambiguous which ArchivesSpace mechanism this should use; tell me if you need it",
}

# "Added entry" fields -> (resolver kind, role) -- not wired to a
# resolver call yet (see module docstring); listed here so an
# unmapped value is at least visible instead of silently dropped.
UNIMPLEMENTED_AGENT_SUBJECT_FIELDS = [
    "addedEntryPersonLC", "addedEntryPersonLocal", "addedEntryCorporateLC", "addedEntryCorporateLocal",
    "addedEntrySubjectLC", "addedEntrySubjectLocal", "addedEntrySubjectFAST",
    "addedEntryGeographicLC", "addedEntryGeographicLocal",
    "addedEntryOccupationLC", "addedEntryOccupationLocal",
    "addedEntryGenreAAT", "addedEntryGenreLCSH", "addedEntryGenreTGM", "addedEntryGenreRBGENR", "addedEntryGenreLocal",
]

LANGUAGE_CODES = {
    "english": "eng", "eng": "eng",
    "french": "fre", "spanish": "spa", "german": "ger", "italian": "ita",
}


def read_collection_level_data(xlsx_path: str, sheet_name: str = "Collection-Level Data") -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Sheet {sheet_name!r} not found in {xlsx_path}. Available: {wb.sheetnames}")
    ws = wb[sheet_name]
    fields = {}
    for row in ws.iter_rows(values_only=True):
        key = row[0]
        if not key or key in ("Required?",):
            continue
        value = row[DATA_ENTRY_COLUMN_INDEX] if len(row) > DATA_ENTRY_COLUMN_INDEX else None
        if value is not None and str(value).strip():
            fields[str(key).strip()] = str(value).strip()
    return fields


def check_required_fields(fields: dict, warnings: list):
    if not fields.get("callNumber"):
        warnings.append(
            "callNumber is blank -- this is the resource's primary identifier (id_0). "
            "Without it, this tool can't reliably check whether the resource already "
            "exists on a re-run, and ArchivesSpace will very likely reject the create."
        )
    if not fields.get("title"):
        warnings.append("title is blank -- ArchivesSpace requires a resource title.")
    if not fields.get("inclusiveDates") and not fields.get("bulkDates"):
        warnings.append(
            "Both inclusiveDates and bulkDates are blank -- ArchivesSpace resources "
            "require at least one date. This create will very likely be rejected."
        )
    if not fields.get("sizeExtent"):
        warnings.append(
            "sizeExtent is blank -- ArchivesSpace resources require at least one "
            "extent. This create will very likely be rejected."
        )
    if fields.get("creatorPerson") and fields.get("creatorCorporate"):
        warnings.append(
            "Both creatorPerson and creatorCorporate are filled in, but the sheet's "
            "own instructions say to use only one ('either a person or a corporate "
            "but not both'). Both will be linked as creator unless you fix the sheet."
        )


def _parse_extent_text(text: str):
    """"6 linear feet" -> {number: "6", extent_type: "linear_feet"}. Best
    effort -- falls back to putting the whole string in `number` with
    extent_type "other_unmapped" if it doesn't parse, so nothing is
    silently dropped."""
    import re
    m = re.match(r"^\s*([\d.]+)\s+(.+?)\s*$", text)
    if not m:
        return {"jsonmodel_type": "extent", "portion": "whole", "number": text, "extent_type": "other_unmapped"}
    number, unit = m.group(1), m.group(2).lower().strip()
    unit_map = {
        "linear feet": "linear_feet", "linear foot": "linear_feet",
        "cubic feet": "cubic_feet", "photographs": "photographs",
        "architectural drawings": "other_unmapped",  # not a standard AS extent_type; flag for review
        "items": "items", "volumes": "volumes", "boxes": "boxes",
    }
    return {
        "jsonmodel_type": "extent",
        "portion": "whole",
        "number": number,
        "extent_type": unit_map.get(unit, "other_unmapped"),
    }


def build_resource_payload(fields: dict, resolve_creator_agent, warnings: list) -> dict:
    """resolve_creator_agent(name, agent_type) -> {"uri": ...} or None
    -- pass in a closure over your ArchivesSpace client/caches so this
    module doesn't need its own copy of the agent-resolution logic.
    """
    payload = {
        "jsonmodel_type": "resource",
        "title": fields.get("title", "[Untitled]"),
        "id_0": fields.get("callNumber", ""),
        "level": "collection",
        "publish": False,
        "finding_aid_status": "unprocessed",
        "extents": [],
        "dates": [],
        "notes": [],
        "linked_agents": [],
        "lang_materials": [],
    }

    if fields.get("filingTitle"):
        payload["finding_aid_filing_title"] = fields["filingTitle"]
    if fields.get("author"):
        payload["finding_aid_author"] = fields["author"]
    if fields.get("sponsor"):
        payload["finding_aid_sponsor"] = fields["sponsor"]
    if fields.get("creationDate"):
        payload["finding_aid_date"] = fields["creationDate"]

    lang = fields.get("materialLanguage") or fields.get("findingAidLanguage")
    if lang:
        code = LANGUAGE_CODES.get(lang.lower())
        if code:
            payload["lang_materials"] = [{
                "jsonmodel_type": "lang_material",
                "language_and_script": {"jsonmodel_type": "language_and_script", "language": code},
            }]
        else:
            warnings.append(f'materialLanguage/findingAidLanguage {lang!r} not in the known language-code '
                             f'list -- lang_materials left empty. Add it to LANGUAGE_CODES in resource_builder.py.')

    if fields.get("inclusiveDates"):
        payload["dates"].append({
            "jsonmodel_type": "date", "label": "creation", "date_type": "inclusive",
            "expression": fields["inclusiveDates"],
        })
    if fields.get("bulkDates"):
        payload["dates"].append({
            "jsonmodel_type": "date", "label": "creation", "date_type": "bulk",
            "expression": fields["bulkDates"],
        })

    if fields.get("sizeExtent"):
        # sizeExtent can genuinely contain several extent statements,
        # one per line (see the sheet's own example: "6 linear feet /
        # 10 photographs / 400 architectural drawings").
        for line in fields["sizeExtent"].splitlines():
            line = line.strip()
            if line:
                payload["extents"].append(_parse_extent_text(line))

    for field_key, (note_type, shape) in NOTE_FIELD_MAP.items():
        val = fields.get(field_key)
        if not val:
            continue
        if shape == "singlepart":
            payload["notes"].append({
                "jsonmodel_type": "note_singlepart", "type": note_type, "publish": True, "content": [val],
            })
        else:
            payload["notes"].append({
                "jsonmodel_type": "note_multipart", "type": note_type, "publish": True,
                "subnotes": [{"jsonmodel_type": "note_text", "content": val, "publish": True}],
            })

    if fields.get("creatorCorporate"):
        link = resolve_creator_agent(fields["creatorCorporate"], "corporate")
        if link:
            payload["linked_agents"].append({"ref": link["uri"], "role": "creator", "relator": "cre"})
    if fields.get("creatorPerson"):
        link = resolve_creator_agent(fields["creatorPerson"], "person")
        if link:
            payload["linked_agents"].append({"ref": link["uri"], "role": "creator", "relator": "cre"})

    for field_key, reason in IGNORED_FIELDS.items():
        if fields.get(field_key):
            warnings.append(f'{field_key} has a value ({fields[field_key]!r}) but is not used: {reason}')
    for field_key in UNIMPLEMENTED_AGENT_SUBJECT_FIELDS:
        if fields.get(field_key):
            warnings.append(f'{field_key} has a value ({fields[field_key]!r}) but "added entry" fields '
                             f'are not wired up yet -- nothing was linked for this. Ask if you need this built.')

    return payload
