#!/usr/bin/env python3
"""
suggest_mapping.py

Inspects a sheet's header row, guesses which ArchivesSpace-relevant
concept each column probably is, and writes a DRAFT mapping config
(YAML) for you to review and correct -- it does not run any migration
itself, and it never overwrites an existing file unless you pass
--force.

Usage:
  python suggest_mapping.py --xlsx FILE.xlsx --sheet "Sheet 1" \
      --out configs/my_config.yaml

  # Headerless sheet -- shows column POSITIONS instead of names, and
  # writes column_index-based config entries you can point at the
  # right position:
  python suggest_mapping.py --xlsx FILE.xlsx --sheet "missing comics" \
      --no-header --out configs/missing_comics.yaml

The guesses are based on substring matching against common header
wording (see SYNONYMS below) -- treat every line in the output as a
suggestion to confirm, not a finished config. Columns it can't guess
are listed under `unmapped:` with the raw header text, so nothing is
silently dropped.
"""

import argparse
import sys

import openpyxl
import yaml

SYNONYMS = {
    "title": ["title"],
    "publisher": ["publisher"],
    "extent": ["total issues", "issues", "extent", "number of issues"],
    "scope_note": ["listing of issues held", "issues held", "run", "contents", "held"],
    "notes": ["notes", "note", "comment", "comments"],
    "physdesc": ["size", "format", "physical description", "physdesc"],
    "date": ["date", "dates"],
    "box": ["box", "container", "new box", "box number"],
    "donor": ["donor", "gift of", "source of acquisition"],
    "ignore_hint": ["computer date", "collection"],
}


def guess_target(header_text: str):
    text = (header_text or "").strip().lower()
    if not text:
        return None, 0
    best_target, best_len = None, 0
    for target, synonyms in SYNONYMS.items():
        for syn in synonyms:
            if syn in text and len(syn) > best_len:
                best_target, best_len = target, len(syn)
    return best_target, best_len


def build_draft(headers, sheet_name, has_header):
    fields = {
        "title": None,
        "publisher": None,
        "extent": None,
        "date": None,
        "physdesc": None,
        "box": None,
    }
    scope_notes = []
    unmapped = []

    for idx, header in enumerate(headers):
        column_ref = header if has_header and header else idx
        target, score = guess_target(header)

        if target in ("scope_note", "notes"):
            scope_notes.append({
                "column": column_ref,
                "_source_header": header,
                "_confidence": "guess" if target == "notes" else "guess (looks like a contents listing)",
            })
        elif target == "ignore_hint":
            unmapped.append((column_ref, header, "looks intentionally excluded in similar sheets (e.g. a computed/derived date, or a grouping column) -- confirm"))
        elif target in fields:
            if fields[target] is None:
                fields[target] = {"column": column_ref, "_source_header": header}
            else:
                unmapped.append((column_ref, header, f"looks like another '{target}' column but one is already assigned -- resolve by hand"))
        elif target == "donor":
            unmapped.append((column_ref, header, "looks like a donor/source column -- no ArchivesSpace field mapped for this yet, add one if needed"))
        else:
            unmapped.append((column_ref, header, "no confident guess"))

    return fields, scope_notes, unmapped


def render_yaml_comment_block(unmapped):
    if not unmapped:
        return ""
    lines = ["# Columns this tool couldn't confidently map -- review each one:"]
    for column_ref, header, reason in unmapped:
        lines.append(f"#   column {column_ref!r} ({header!r}): {reason}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--out", required=True, help="Path to write the draft YAML config.")
    parser.add_argument("--no-header", action="store_true",
                         help="Treat row 1 as data, not a header -- columns are referenced by position.")
    parser.add_argument("--force", action="store_true", help="Overwrite --out if it already exists.")
    args = parser.parse_args()

    import os
    if os.path.exists(args.out) and not args.force:
        print(f"{args.out} already exists. Use --force to overwrite.")
        sys.exit(1)

    wb = openpyxl.load_workbook(args.xlsx, data_only=True)
    if args.sheet not in wb.sheetnames:
        print(f"Sheet {args.sheet!r} not found. Available: {wb.sheetnames}")
        sys.exit(1)
    ws = wb[args.sheet]
    first_row = next(ws.iter_rows(values_only=True, max_row=1), ())

    has_header = not args.no_header
    headers = list(first_row) if has_header else [f"(column {i})" for i in range(len(first_row))]

    fields, scope_notes, unmapped = build_draft(headers, args.sheet, has_header)

    print(f"Sheet: {args.sheet}  (header row: {'yes' if has_header else 'no'})")
    print(f"{len(headers)} columns found.\n")
    for target, cfg in fields.items():
        if cfg:
            print(f"  {target:12s} <- column {cfg['column']!r}  (from header {cfg.get('_source_header')!r})")
        else:
            print(f"  {target:12s} <- (no guess -- leave blank or fill in by hand)")
    for cfg in scope_notes:
        print(f"  scope_note   <- column {cfg['column']!r}  (from header {cfg.get('_source_header')!r})")
    for column_ref, header, reason in unmapped:
        print(f"  ??           <- column {column_ref!r} ({header!r}): {reason}")

    # Strip the internal bookkeeping keys before writing YAML.
    def clean(cfg):
        if cfg is None:
            return None
        return {k: v for k, v in cfg.items() if not k.startswith("_")}

    draft = {
        "name": f"DRAFT -- {args.sheet}",
        "sheets": [args.sheet],
        "has_header": has_header,
        "level": "item",
        "instance_type": "mixed_materials",
        "publish_default": False,
        "title": clean(fields["title"]),
        "publisher": clean(fields["publisher"]),
        "extent": clean(fields["extent"]) and {**clean(fields["extent"]), "extent_type": "items"},
        "date": clean(fields["date"]) and {**clean(fields["date"]), "label": "publication"},
        "physdesc": clean(fields["physdesc"]),
        "box": clean(fields["box"]) and {**clean(fields["box"]), "compound": False},
        "scope_notes": [clean(c) for c in scope_notes],
    }

    header_comment = (
        f"# DRAFT mapping config auto-suggested from the headers of sheet {args.sheet!r}\n"
        f"# in {args.xlsx!r}. Every guess below needs your review before this drives a\n"
        f"# real run -- see the console output above for the reasoning, and\n"
        f"# suggest_mapping.py's own docstring for what each field means.\n"
        f"# Delete this comment block once you've reviewed the config.\n\n"
    )

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(header_comment)
        yaml.safe_dump(draft, f, sort_keys=False, allow_unicode=True)
        f.write("\n" + render_yaml_comment_block(unmapped))

    print(f"\nDraft written to {args.out} -- review it before using it with migrate.py.")


if __name__ == "__main__":
    main()
