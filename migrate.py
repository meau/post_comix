#!/usr/bin/env python3
"""
migrate.py

Generalized version of migrate_comics.py: migrates one or more sheets
into ArchivesSpace as child archival objects of a resource, driven
entirely by a YAML mapping config (see mapping.py, configs/*.yaml,
and suggest_mapping.py for how to build one).

Usage examples:

  python migrate.py --config configs/peck_comics.yaml --xlsx Copy_of_Peck_Comics.xlsx --dry-run --limit 5
  python migrate.py --config configs/ciaraldi_sheet1.yaml --xlsx Ciaraldi_Standard_Size.xlsx --dry-run --limit 5

  # Override which sheet(s) the config applies to for this run:
  python migrate.py --config configs/ciaraldi_by_title.yaml --xlsx Ciaraldi_Standard_Size.xlsx \
      --sheets "Wizard,Flash" --dry-run

See README.md for the full flag list -- they're unchanged from
migrate_comics.py (--dry-run, --limit, --rows, --force, --publish,
--resource, --secrets, --state-file, --log-dir).

Row identity across MULTIPLE sheets: state.json keys are
"<sheet>::<excel row number>" rather than a bare row number, so
running several sheets in one config never collides.
"""

import argparse
import datetime
import json
import os
import sys

import openpyxl

from aspace_client import ArchivesSpaceClient, ArchivesSpaceError
from agents import resolve_publisher_agent
from containers import resolve_top_container_by_indicator
from generic_builders import build_title, build_extents, build_notes, build_dates, parse_box_value, clean_str
from mapping import MappingConfig, get_value
from resource_url import parse_resource_reference, build_resource_ref, ResourceUrlError
from state import RunState

SECRETS_TEMPLATE = {
    "api_url": "",
    "repository_id": "",
    "username": "",
    "password": "",
}


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
        sys.exit(1)
    return secrets


def read_sheet_rows(xlsx_path: str, sheet_name: str, has_header: bool):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if sheet_name not in wb.sheetnames:
        print(f"Sheet {sheet_name!r} not found. Available sheets: {wb.sheetnames}")
        sys.exit(1)
    ws = wb[sheet_name]
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return None, []

    if has_header:
        header = all_rows[0]
        header_index = {name: idx for idx, name in enumerate(header) if name}
        data_rows = list(enumerate(all_rows[1:], start=2))
    else:
        header_index = None
        data_rows = list(enumerate(all_rows, start=1))

    rows = [(n, r) for n, r in data_rows if any(c is not None for c in r)]
    return header_index, rows


def _validate_mapping_against_header(mapping: MappingConfig, header_index, sheet_name: str):
    """Catch a real failure mode early: a name-based `column` in the
    config that doesn't actually exist in THIS sheet's header row.
    Without this check, get_value() would just silently return None
    for every row -- e.g. two sheets sharing a config where one
    spells a header "Starting Date" and another "Start Date" would
    quietly produce empty dates for the whole second sheet, with no
    error and no warning. Headerless sheets (header_index is None)
    skip this -- column refs there are positional by construction.
    """
    if header_index is None:
        return
    missing = []

    def check(column_ref, field_label):
        if isinstance(column_ref, str) and column_ref not in header_index:
            missing.append(f'{field_label} -> column "{column_ref}"')

    for field_label, cfg in (("title", mapping.title), ("publisher", mapping.publisher),
                              ("extent", mapping.extent), ("date", mapping.date),
                              ("physdesc", mapping.physdesc), ("box", mapping.box)):
        if cfg:
            check(cfg.get("column"), field_label)
    for i, cfg in enumerate(mapping.scope_notes or []):
        check(cfg.get("column"), f"scope_notes[{i}]")

    if missing:
        actual = ", ".join(repr(h) for h in header_index if h)
        raise SystemExit(
            f'Config {mapping.path!r} references column(s) not present in sheet '
            f'{sheet_name!r}:\n  ' + "\n  ".join(missing) +
            f"\n\nThis sheet's actual headers: {actual}\n"
            f"(If sheets sharing this config have slightly different header text, "
            f"use column_index (0-based position) instead of column names.)"
        )


def build_archival_object(row_values, header_index, mapping: MappingConfig,
                           resource_ref, agent_link, container_link, warnings):
    title = build_title(row_values, header_index, mapping.title)
    return {
        "jsonmodel_type": "archival_object",
        "title": title,
        "level": mapping.level,
        "publish": mapping.publish_default,
        "resource": {"ref": resource_ref},
        "dates": build_dates(row_values, header_index, mapping.date, warnings, title),
        "extents": build_extents(row_values, header_index, mapping.extent, warnings, title),
        "notes": build_notes(row_values, header_index, mapping, warnings),
        "instances": _build_instances(container_link, mapping.instance_type),
        "linked_agents": _build_linked_agents(agent_link),
    }, title


def _build_instances(container_link, instance_type):
    if not container_link:
        return []
    return [{
        "jsonmodel_type": "instance",
        "instance_type": instance_type,
        "sub_container": {
            "jsonmodel_type": "sub_container",
            "top_container": {"ref": container_link["uri"]},
        },
    }]


def _build_linked_agents(agent_link):
    if not agent_link:
        return []
    return [{"ref": agent_link["uri"], "role": "creator", "relator": "pbl"}]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Path to a mapping YAML config.")
    parser.add_argument("--xlsx", required=True, help="Path to the spreadsheet.")
    parser.add_argument("--sheets", default=None,
                         help="Comma-separated sheet names to process, overriding the config's own `sheets:` list.")
    parser.add_argument("--secrets", default="secrets.json")
    parser.add_argument("--resource", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N data rows PER SHEET.")
    parser.add_argument("--rows", default=None,
                         help="Comma-separated Excel row numbers to process (applies within each sheet processed).")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--state-file", default="state.json")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--publish", action="store_true",
                         help="Override the config's publish_default to True.")
    args = parser.parse_args()

    mapping = MappingConfig.load(args.config)
    if args.publish:
        mapping.publish_default = True

    sheets = [s.strip() for s in args.sheets.split(",")] if args.sheets else mapping.sheets
    if not sheets:
        print("No sheets to process -- pass --sheets or set `sheets:` in the config.")
        sys.exit(1)

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
              f"but secrets.json has repository_id={repository_id}. Using secrets.json's value.")
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
    log(f"Config: {args.config} ({mapping.name})")
    log(f"Spreadsheet: {args.xlsx} | Sheets: {sheets}")
    log(f"Target resource: {resource_ref}")
    log(f"Dry run: {args.dry_run} | Publish new archival objects: {mapping.publish_default}")

    try:
        client = ArchivesSpaceClient(
            api_url=secrets["api_url"], username=secrets["username"], password=secrets["password"],
            repository_id=repository_id, dry_run=args.dry_run, log=log,
        )
    except ArchivesSpaceError as exc:
        log(f"Could not connect/log in to ArchivesSpace: {exc}")
        sys.exit(1)

    state = RunState(args.state_file)
    agent_cache, container_cache = {}, {}
    wanted_rows = None
    if args.rows:
        wanted_rows = {int(x.strip()) for x in args.rows.split(",") if x.strip()}

    succeeded, skipped, errored = 0, 0, 0
    all_warnings = []

    for sheet_name in sheets:
        header_index, rows = read_sheet_rows(args.xlsx, sheet_name, mapping.has_header)
        _validate_mapping_against_header(mapping, header_index, sheet_name)
        if wanted_rows is not None:
            rows = [(n, r) for n, r in rows if n in wanted_rows]
        elif args.limit:
            rows = rows[: args.limit]

        for excel_row_num, row_values in rows:
            state_key = f"{sheet_name}::{excel_row_num}"

            if not args.force and state.was_successful(state_key):
                log(f"{state_key}: already succeeded previously -- skipping. (use --force to redo)")
                skipped += 1
                continue

            row_warnings = []
            try:
                publisher_raw = get_value(row_values, header_index, mapping.publisher.get("column")) if mapping.publisher else None
                agent_link = resolve_publisher_agent(publisher_raw, client, agent_cache, log) if publisher_raw else None

                container_link = None
                if mapping.box:
                    box_raw = get_value(row_values, header_index, mapping.box.get("column"))
                    indicator, barcode = parse_box_value(box_raw, mapping.box)
                    if indicator:
                        container_link = resolve_top_container_by_indicator(
                            indicator, client, container_cache, resource_ref, log, barcode=barcode,
                        )

                ao_payload, title = build_archival_object(
                    row_values, header_index, mapping, resource_ref, agent_link, container_link, row_warnings,
                )

                result = client.post(f"{client.repo_prefix}/archival_objects", ao_payload)
                ao_uri = result.get("uri")

                for w in row_warnings:
                    log(f"  WARNING: {w}")
                all_warnings.extend(row_warnings)

                log(f"{state_key} ({title}): created {ao_uri}")
                state.record(state_key, "success", title=title, archival_object_uri=ao_uri, warnings=row_warnings)
                succeeded += 1

            except Exception as exc:  # noqa: BLE001
                title_guess = build_title(row_values, header_index, mapping.title)
                log(f"{state_key} ({title_guess}): ERROR -- {exc}")
                state.record(state_key, "error", title=title_guess, error=str(exc))
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
