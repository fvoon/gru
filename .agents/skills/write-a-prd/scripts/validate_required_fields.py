"""Validate that gru's `## Required custom fields` conventions match live PLTPM.

Two modes (mutually exclusive):

  * ``--bootstrap`` -- emits a JSON action plan instructing the agent in chat
    to fetch issue-type create-screen metadata for every gru-relevant PLTPM
    issue type. The agent runs the plan, the PM uses the response to
    populate the ``## Required custom fields (PLTPM)`` section in
    ``.agents/jira-conventions.md`` (at minimum: each required customfield's
    id, display name, allowed values, and the issue types it applies to).

  * ``--drift-check --metadata-fixture <path>`` -- compares the conventions
    section against a fetched metadata fixture and reports drift.

Drift is any of:

  - **new** -- live Jira marks a customfield as required for an issue type,
    but the conventions don't declare that field at all (or don't include
    that issue type in its ``Applies to`` list). gru is not injecting it
    on createIssue; the next demo will eat HTTP 400s.
  - **missing** -- the conventions declare a customfield as required, but
    live Jira no longer marks it as required. gru is injecting a value that
    Jira may reject as unknown / readonly.
  - **allowed_values_changed** -- conventions allowed-values list differs
    from live Jira's. The conventions default may no longer be valid.
  - **applies_to_changed** -- conventions ``Applies to`` list does not
    cover every issue type for which Jira marks the field as required.

Exit codes:
  0 - bootstrap plan emitted, OR drift-check passed.
  1 - drift-check found drift (see stdout JSON for details).
  2 - usage / IO / parse error.

The script never calls Atlassian APIs itself; the agent in chat is the
executor. This keeps the script pure and unit-testable in isolation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
_LIB_PATH = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

from required_fields import (  # noqa: E402
    FieldSpec,
    parse_required_fields,
)

from validate_components import find_conventions  # noqa: E402

GRU_RELEVANT_ISSUE_TYPES = (
    "Story",
    "Technical Story",
    "Task",
    "Sub-task",
    "Research ",
    "Design ",
)


def emit_bootstrap_action_plan(
    project_key: str = "PLTPM",
    issue_type_names: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Return a JSON action plan that fetches issue-type metadata for the PM.

    Plan shape:
      step 1: getJiraProjectIssueTypesMetadata (sanity check / id lookup).
      steps 2..N: getJiraIssueTypeMetaWithFields, one per issue type.
    """
    types = list(issue_type_names) if issue_type_names else list(GRU_RELEVANT_ISSUE_TYPES)
    actions: list[dict] = [
        {
            "step": 1,
            "tool": "atlassian.getJiraProjectIssueTypesMetadata",
            "args": {"projectKey": project_key},
            "purpose": (
                "Confirm the PLTPM issue-type ids; sanity-check the names "
                "we're about to fetch field metadata for."
            ),
        }
    ]
    for i, name in enumerate(types, start=2):
        actions.append(
            {
                "step": i,
                "tool": "atlassian.getJiraIssueTypeMetaWithFields",
                "args": {
                    "projectKey": project_key,
                    "issueTypeName": name,
                },
                "purpose": (
                    f"Fetch create-screen metadata for {name!r} so the PM "
                    "can see every customfield Jira marks as required."
                ),
            }
        )

    return {
        "status": "needs_fetch",
        "issue_types": types,
        "actions": actions,
        "next_step": (
            "Save the union of responses to a JSON file (one top-level key "
            "per issue-type name, value = the metadata response). Then "
            "re-invoke: validate_required_fields.py --drift-check "
            "--metadata-fixture <path>. Or: hand-edit "
            ".agents/jira-conventions.md `## Required custom fields (PLTPM)` "
            "to declare each required customfield (id, display name, default, "
            "allowed values, applies-to issue types, wire shape)."
        ),
    }


def parse_metadata_fixture(raw: Mapping[str, object]) -> dict[str, dict[str, dict]]:
    """Normalise the live metadata fixture into a per-issue-type required-fields map.

    Input shape (canonical Jira REST):
        {
          "<issuetype_name>": {
            "fields": [
              {"fieldId": "customfield_X", "name": "...",
               "required": true, "allowedValues": [{"value": "..."}, ...]},
              ...
            ]
          },
          ...
        }

    Output shape:
        {
          "<issuetype_name>": {
            "<custom_field_id>": {
              "name": "...", "required": True,
              "allowed_values": ["...", ...],
            },
            ...
          },
          ...
        }

    Built-in fields (those whose fieldId does NOT start with ``customfield_``)
    are skipped because gru doesn't inject them. Required-but-non-customfield
    fields like ``summary`` and ``description`` are part of every createIssue
    payload anyway.
    """
    out: dict[str, dict[str, dict]] = {}
    for issue_type, payload in raw.items():
        if not isinstance(payload, Mapping):
            continue
        fields = payload.get("fields", [])
        per_type: dict[str, dict] = {}
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            field_id = field.get("fieldId")
            if not isinstance(field_id, str) or not field_id.startswith("customfield_"):
                continue
            if not field.get("required"):
                continue
            allowed_values: list[str] = []
            for entry in field.get("allowedValues", []) or []:
                if isinstance(entry, Mapping):
                    val = entry.get("value")
                    if isinstance(val, str):
                        allowed_values.append(val)
                elif isinstance(entry, str):
                    allowed_values.append(entry)
            per_type[field_id] = {
                "name": field.get("name", ""),
                "required": True,
                "allowed_values": allowed_values,
            }
        out[issue_type] = per_type
    return out


def verify_drift(
    conventions_fields: Mapping[str, FieldSpec],
    metadata_raw: Mapping[str, object],
) -> dict:
    """Compare conventions to live Jira metadata and report drift."""
    parsed = parse_metadata_fixture(metadata_raw)
    drifts: list[dict] = []

    # Build a reverse map: customfield_id -> set of issue types Jira requires it for.
    live_required_per_field: dict[str, set[str]] = {}
    for issue_type, fields in parsed.items():
        for field_id in fields:
            live_required_per_field.setdefault(field_id, set()).add(issue_type)

    # 1. NEW drift: Jira requires a field-on-issue-type that conventions don't know about.
    for field_id, live_types in live_required_per_field.items():
        spec = conventions_fields.get(field_id)
        if spec is None:
            for issue_type in sorted(live_types):
                drifts.append(
                    {
                        "kind": "new",
                        "field_id": field_id,
                        "issue_type": issue_type,
                        "message": (
                            f"Jira requires {field_id} on {issue_type!r} but the "
                            f"conventions don't declare it. Add it to the "
                            f"`## Required custom fields (PLTPM)` section."
                        ),
                    }
                )
            continue
        # Conventions know about the field. Are all live-required issue types covered?
        not_in_applies_to = live_types - set(spec.applies_to)
        if not_in_applies_to:
            drifts.append(
                {
                    "kind": "applies_to_changed",
                    "field_id": field_id,
                    "missing_issue_types": sorted(not_in_applies_to),
                    "message": (
                        f"Jira requires {field_id} on issue types not listed in "
                        f"the conventions `Applies to` list: "
                        f"{sorted(not_in_applies_to)}. Update conventions."
                    ),
                }
            )

    # 2. MISSING drift, two flavours:
    #   2a. Conventions declares a field that no live issue type requires.
    #   2b. Conventions's `Applies to` includes an issue type whose live
    #       metadata is in the fixture but does NOT require this field. gru
    #       is over-injecting on that type (probably tolerated by Jira but
    #       counts as drift / noise).
    for field_id, spec in conventions_fields.items():
        live_types = live_required_per_field.get(field_id, set())
        if not live_types:
            drifts.append(
                {
                    "kind": "missing",
                    "field_id": field_id,
                    "applies_to": list(spec.applies_to),
                    "message": (
                        f"Conventions declare {field_id} as required for "
                        f"{list(spec.applies_to)!r}, but no live issue type metadata "
                        f"marks it required. Either Jira changed (drop from "
                        f"conventions), or the metadata fixture is incomplete "
                        f"(re-fetch with --bootstrap)."
                    ),
                }
            )
            continue
        # 2b: types where conventions claim the field is required, but the
        # fixture covers that type and the field is NOT required there.
        for issue_type in spec.applies_to:
            if issue_type not in parsed:
                continue  # fixture doesn't cover this type; can't tell
            if field_id in parsed[issue_type]:
                continue  # field IS required there; consistent
            drifts.append(
                {
                    "kind": "missing",
                    "field_id": field_id,
                    "issue_type": issue_type,
                    "message": (
                        f"Conventions declare {field_id} as required on {issue_type!r}, "
                        f"but the live metadata for that type does not mark it required. "
                        f"Drop {issue_type!r} from the conventions `Applies to` list."
                    ),
                }
            )

    # 3. ALLOWED_VALUES drift: conventions vs live values diverge.
    for field_id, spec in conventions_fields.items():
        for issue_type in spec.applies_to:
            live_field = parsed.get(issue_type, {}).get(field_id)
            if not live_field:
                continue  # already reported as missing/applies_to drift
            live_values = set(live_field.get("allowed_values", []))
            conv_values = set(spec.allowed_values)
            if live_values != conv_values:
                drifts.append(
                    {
                        "kind": "allowed_values_changed",
                        "field_id": field_id,
                        "issue_type": issue_type,
                        "conventions_values": sorted(conv_values),
                        "live_values": sorted(live_values),
                        "message": (
                            f"{field_id} on {issue_type!r}: allowed values diverge "
                            f"between conventions and live Jira."
                        ),
                    }
                )

    if drifts:
        return {"status": "drift", "drifts": drifts}
    return {"status": "ok"}


# ----- CLI -----


def _cli_bootstrap(args: argparse.Namespace) -> int:
    plan = emit_bootstrap_action_plan(project_key=args.project_key)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


def _cli_drift_check(args: argparse.Namespace) -> int:
    if args.metadata_fixture is None:
        print(
            "--drift-check requires --metadata-fixture <path> "
            "(re-run with --bootstrap to emit the fetch plan first).",
            file=sys.stderr,
        )
        return 2

    try:
        raw = json.loads(args.metadata_fixture.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        print(f"could not read --metadata-fixture: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"--metadata-fixture is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if args.conventions_path is None:
        try:
            conventions_path = find_conventions(SCRIPT_DIR)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        conventions_path = args.conventions_path

    try:
        conventions_text = conventions_path.read_text(encoding="utf-8")
        fields = parse_required_fields(conventions_text)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = verify_drift(fields, raw)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--bootstrap",
        action="store_true",
        help="Emit a JSON action plan to fetch issue-type metadata.",
    )
    mode.add_argument(
        "--drift-check",
        action="store_true",
        help="Compare conventions against a fetched metadata fixture.",
    )
    parser.add_argument("--project-key", default="PLTPM", help="Jira project key.")
    parser.add_argument(
        "--metadata-fixture",
        type=Path,
        default=None,
        help="Path to the JSON file holding the metadata response (drift-check mode).",
    )
    parser.add_argument(
        "--conventions-path",
        type=Path,
        default=None,
        help="Path to .agents/jira-conventions.md (default: walk up from script).",
    )
    args = parser.parse_args(argv)

    if args.bootstrap:
        return _cli_bootstrap(args)
    return _cli_drift_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
