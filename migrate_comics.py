#!/usr/bin/env python3
"""
migrate_comics.py

Migrates the "Comics" sheet of the Peck Comics spreadsheet into
ArchivesSpace as child archival objects of a resource you specify.

Usage examples:

  # First, always look before you leap:
  python migrate_comics.py --dry-run --limit 5

  # Test a couple of specific (known-tricky) rows by their Excel row number:
  python migrate_comics.py --dry-run --rows 45,217,388

  # The real thing:
  python migrate_comics.py

  # Re-run after fixing something -- rows already marked "success" in
  # state.json are skipped automatically. To force-redo everything:
  python migrate_comics.py --force

See README.md for the full mapping, all assumptions, and setup steps.
"""

import argparse
import datetime
import json
import os
import sys

import openpyxl

from aspace_client import ArchivesSpaceClient, ArchivesSpaceError
from agents import resolve_publisher_agent
from containers import resolve_top_container
from date_parser import parse_date_cell
from resource_url import parse_resource_reference, build_resource_ref, ResourceUrlError
from state import RunState

SECRETS_TEMPLATE = {
    "api_url": "",
    "repository_id": "",
    "username": "",
    "password": "",
}

REQUIRED_COLUMNS = [
    "Title",
    "Publisher",
    "Issues",
    "Listing of Issues Held",
    "Human readable dates updated final",
    "Size",
    "Notes",
    "New Box Number",
]


def load_or_init_secrets(path: str) -> dict:
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(SECRETS_TEMPLATE, f, indent=2)
        print(f"No {path} found -- created a template for you.")
        print("Fill in api_url, repository_id, username, and password, then re-run.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        secrets = json.load(f)

    missing = [k for k in SECRETS_TEMPLATE if not secrets.get(k)]
    if missing:
        print(f"{path} is missing values for: {', '.join(missing)}")
        print("Fill those in and re-run.")
        sys.exit(1)

    return secrets


def read_rows(xlsx_path: str, sheet_name: str):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if sheet_name not in wb.sheetnames:
        print(f"Sheet {sheet_name!r} not found. Available sheets: {wb.sheetnames}")
        sys.exit(1)
    ws = wb[sheet_name]
    all_rows = list(ws.iter_rows(values_only=True))
    header = all_rows[0]

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing_cols:
        print(f"Sheet {sheet_name!r} is missing expected column(s): {missing_cols}")
        sys.exit(1)

    col_index = {name: header.index(name) for name in header if name}

    rows = []
    for excel_row_num, raw_row in enumerate(all_rows[1:], start=2):
        if all(cell is None for cell in raw_row):
            continue
        row = {name: raw_row[idx] for name, idx in col_index.items()}
        rows.append((excel_row_num, row))
    return rows


def clean_str(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_notes(row, warnings):
    notes = []

    listing = clean_str(row.get("Listing of Issues Held"))
    if listing:
        content = f"Includes {listing}"
        if not content.endswith("."):
            content += "."
        notes.append({
            "jsonmodel_type": "note_multipart",
            "type": "scopecontent",
            "publish": True,
            "subnotes": [{
                "jsonmodel_type": "note_text",
                "content": content,
                "publish": True,
            }],
        })

    general_notes = clean_str(row.get("Notes"))
    if general_notes:
        notes.append({
            "jsonmodel_type": "note_multipart",
            "type": "scopecontent",
            "publish": True,
            "subnotes": [{
                "jsonmodel_type": "note_text",
                "content": general_notes,
                "publish": True,
            }],
        })

    size = clean_str(row.get("Size"))
    if size:
        notes.append({
            "jsonmodel_type": "note_singlepart",
            "type": "physdesc",
            "publish": True,
            "content": [size],
        })

    return notes


def build_extents(row, warnings, title):
    issues = row.get("Issues")
    if issues is None or str(issues).strip() == "":
        return []
    try:
        number = str(int(float(issues)))
    except (TypeError, ValueError):
        warnings.append(f'Title "{title}": Issues value {issues!r} is not numeric -- extent skipped.')
        return []
    return [{
        "jsonmodel_type": "extent",
        "portion": "whole",
        "number": number,
        "extent_type": "items",
    }]


def build_dates(row, warnings, title):
    raw = row.get("Human readable dates updated final")
    if raw is None or str(raw).strip() == "":
        return []
    parsed = parse_date_cell(raw)
    if parsed is None:
        warnings.append(f'Title "{title}": could not parse date {raw!r} -- date subrecord skipped.')
        return []
    date_record = {
        "jsonmodel_type": "date",
        "label": "creation",
        "date_type": parsed.date_type,
        "expression": parsed.expression,
        "begin": parsed.begin,
    }
    if parsed.end:
        date_record["end"] = parsed.end
    return [date_record]


def build_instances(container_link):
    if not container_link:
        return []
    return [{
        "jsonmodel_type": "instance",
        "instance_type": "mixed_materials",
        "sub_container": {
            "jsonmodel_type": "sub_container",
            "top_container": {"ref": container_link["uri"]},
        },
    }]


def build_linked_agents(agent_link):
    if not agent_link:
        return []
    return [{
        "ref": agent_link["uri"],
        "role": "creator",
        "relator": "pbl",
    }]


def build_archival_object(row, resource_ref, agent_link, container_link, warnings, publish_default):
    title = clean_str(row.get("Title")) or "[Untitled]"
    return {
        "jsonmodel_type": "archival_object",
        "title": title,
        "level": "item",
        "publish": publish_default,
        "resource": {"ref": resource_ref},
        "dates": build_dates(row, warnings, title),
        "extents": build_extents(row, warnings, title),
        "notes": build_notes(row, warnings),
        "instances": build_instances(container_link),
        "linked_agents": build_linked_agents(agent_link),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", default="Copy_of_Peck_Comics.xlsx", help="Path to the spreadsheet.")
    parser.add_argument("--sheet", default="Comics", help="Sheet name to migrate.")
    parser.add_argument("--secrets", default="secrets.json", help="Path to secrets.json.")
    parser.add_argument("--resource", default=None,
                         help="Resource URL/ID (staff, public, API, or bare ID). "
                              "If omitted, you'll be prompted.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Preview only -- no records are created or modified.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N data rows.")
    parser.add_argument("--rows", default=None,
                         help="Comma-separated Excel row numbers to process, e.g. 45,217,388.")
    parser.add_argument("--force", action="store_true",
                         help="Reprocess rows even if state.json marks them as already successful.")
    parser.add_argument("--state-file", default="state.json")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--publish", action="store_true",
                         help="Create archival objects as published. Default is unpublished, "
                              "so you can review before making them public.")
    args = parser.parse_args()

    secrets = load_or_init_secrets(args.secrets)

    resource_input = args.resource or input(
        "Paste the resource's staff URL, public URL, API URI, or numeric ID: "
    ).strip()
    try:
        resource_id, url_repo_id = parse_resource_reference(resource_input)
    except ResourceUrlError as exc:
        print(str(exc))
        sys.exit(1)

    repository_id = secrets["repository_id"]
    if url_repo_id and str(url_repo_id) != str(repository_id):
        print(f"NOTE: the URL you pasted references repository {url_repo_id}, "
              f"but secrets.json has repository_id={repository_id}. "
              f"Using secrets.json's repository_id={repository_id}.")

    resource_ref = build_resource_ref(repository_id, resource_id)

    os.makedirs(args.log_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(args.log_dir, f"run-{timestamp}.log")
    log_file = open(log_path, "w", encoding="utf-8")

    def log(msg):
        print(msg)
        log_file.write(msg + "\n")
        log_file.flush()

    log(f"=== Run started {timestamp} ===")
    log(f"Spreadsheet: {args.xlsx} | Sheet: {args.sheet}")
    log(f"Target resource: {resource_ref}")
    log(f"Dry run: {args.dry_run} | Publish new archival objects: {args.publish}")

    try:
        client = ArchivesSpaceClient(
            api_url=secrets["api_url"],
            username=secrets["username"],
            password=secrets["password"],
            repository_id=repository_id,
            dry_run=args.dry_run,
            log=log,
        )
    except ArchivesSpaceError as exc:
        log(f"Could not connect/log in to ArchivesSpace: {exc}")
        sys.exit(1)

    state = RunState(args.state_file)
    rows = read_rows(args.xlsx, args.sheet)

    if args.rows:
        wanted = {int(x.strip()) for x in args.rows.split(",") if x.strip()}
        rows = [(n, r) for n, r in rows if n in wanted]
    elif args.limit:
        rows = rows[: args.limit]

    agent_cache = {}
    container_cache = {}

    succeeded, skipped, errored = 0, 0, 0
    all_warnings = []

    for excel_row_num, row in rows:
        title = clean_str(row.get("Title")) or "[Untitled]"

        if not args.force and state.was_successful(excel_row_num):
            log(f"Row {excel_row_num} ({title}): already succeeded previously -- skipping. "
                f"(use --force to redo)")
            skipped += 1
            continue

        row_warnings = []
        try:
            agent_link = resolve_publisher_agent(row.get("Publisher"), client, agent_cache, log)
            container_link = resolve_top_container(
                row.get("New Box Number"), client, container_cache, resource_ref, log
            )

            ao_payload = build_archival_object(
                row, resource_ref, agent_link, container_link, row_warnings,
                publish_default=args.publish,
            )

            result = client.post(f"{client.repo_prefix}/archival_objects", ao_payload)
            ao_uri = result.get("uri")

            for w in row_warnings:
                log(f"  WARNING: {w}")
            all_warnings.extend(row_warnings)

            log(f"Row {excel_row_num} ({title}): created {ao_uri}")
            state.record(excel_row_num, "success", title=title, archival_object_uri=ao_uri,
                          warnings=row_warnings)
            succeeded += 1

        except Exception as exc:  # noqa: BLE001
            log(f"Row {excel_row_num} ({title}): ERROR -- {exc}")
            state.record(excel_row_num, "error", title=title, error=str(exc))
            errored += 1

    log("")
    log("=== Summary ===")
    log(f"Succeeded: {succeeded}")
    log(f"Skipped (already done): {skipped}")
    log(f"Errored: {errored}")
    log(f"Total warnings: {len(all_warnings)}")
    log(f"Full log: {log_path}")
    log(f"State file: {args.state_file}")
    if errored:
        log("Re-run the same command to retry only the errored/unprocessed rows.")

    log_file.close()


if __name__ == "__main__":
    main()
