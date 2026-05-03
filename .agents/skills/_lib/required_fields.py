"""Required custom fields harness helper.

Parses the `## Required custom fields (<project>)` section of
`.agents/jira-conventions.md` and injects the chosen value(s) into the
`additional_fields` payload of `atlassian.createJiraIssue` action plans.

Why a helper: PLTPM marks several Jira custom fields as required at issue
creation time (e.g. `customfield_12881` -- Activity Type). gru's emitter
scripts (`write_jira_prd.py`, `write_jira_children.py`) must inject those
fields into every `createJiraIssue` action they emit, otherwise the agent
in chat eats an HTTP 400 per attempt. Defaults live in the conventions
file (single source of truth, never hardcoded); per-PRD overrides flow in
through the `overrides` keyword argument.

The companion gate `validate_required_fields.py` (in skill `scripts/`
folders) handles bootstrap (one-time PM interview to populate defaults)
and explicit drift checks (compares conventions to live Jira metadata).
This module is purely the read + inject path used on every emitter run.

This file is read-only with respect to the conventions text; it never
writes back.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

VALID_WIRE_SHAPES = ("object", "scalar")
SECTION_HEADING_RE = re.compile(
    r"^## Required custom fields.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
BOOTSTRAP_MARKER_RE = re.compile(r"<TBA\s*[-\u2013\u2014]+\s*bootstrap", re.IGNORECASE)
FIELD_HEADER_RE = re.compile(
    r"^- \*\*`(?P<id>customfield_\d+)`\*\*\s*[-\u2013\u2014]+\s*(?P<name>.+?)\s*$",
    re.MULTILINE,
)
PROPERTY_RE = re.compile(
    r"^[ \t]+- (?P<key>Default|Allowed|Applies to|Wire shape)\s*:\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")


@dataclasses.dataclass(frozen=True)
class FieldSpec:
    """Specification for a single required custom field, parsed from conventions."""

    field_id: str
    display_name: str
    default_value: str
    allowed_values: tuple[str, ...]
    applies_to: tuple[str, ...]
    wire_shape: str

    def to_payload(self, value: str) -> object:
        """Render `value` in the wire shape Jira expects on createIssue.

        `object` shape -> `{"value": value}` (the standard for
        single-select custom fields like Activity Type).
        `scalar` shape -> the bare string (used for free-text customfields).
        """
        if self.wire_shape == "object":
            return {"value": value}
        if self.wire_shape == "scalar":
            return value
        raise ValueError(f"unknown wire shape '{self.wire_shape}'")


def parse_required_fields(conventions_text: str) -> dict[str, FieldSpec]:
    """Parse the `## Required custom fields` section of the conventions markdown.

    Returns a dict keyed by `customfield_<n>` -> FieldSpec. Raises ValueError
    if the section is missing, marked with the bootstrap placeholder, or any
    individual field block is malformed.
    """
    section_match = SECTION_HEADING_RE.search(conventions_text)
    if not section_match:
        raise ValueError(
            "could not find '## Required custom fields' section in conventions; "
            "run `validate_required_fields.py --bootstrap` to populate it"
        )

    section = section_match.group(0)

    if BOOTSTRAP_MARKER_RE.search(section):
        raise ValueError(
            "Required custom fields section is still in bootstrap state "
            "(`<TBA -- bootstrap>` placeholder present). "
            "Run `validate_required_fields.py --bootstrap` to populate defaults."
        )

    fields: dict[str, FieldSpec] = {}
    headers = list(FIELD_HEADER_RE.finditer(section))
    for i, header in enumerate(headers):
        field_id = header.group("id")
        display_name = header.group("name").strip()
        block_start = header.end()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(section)
        block = section[block_start:block_end]

        props: dict[str, str] = {}
        for prop in PROPERTY_RE.finditer(block):
            props[prop.group("key").strip()] = prop.group("value").strip()

        missing = {"Default", "Allowed", "Applies to", "Wire shape"} - set(props)
        if missing:
            raise ValueError(
                f"field {field_id} ({display_name}) missing properties: {sorted(missing)}"
            )

        default_value = _strip_backticks(props["Default"])
        allowed_values = _parse_backtick_list(props["Allowed"])
        applies_to = _parse_backtick_list(props["Applies to"])
        wire_shape = _strip_backticks(props["Wire shape"])

        if wire_shape not in VALID_WIRE_SHAPES:
            raise ValueError(
                f"field {field_id} ({display_name}) has unknown wire shape "
                f"'{wire_shape}'; must be one of {VALID_WIRE_SHAPES}"
            )

        if default_value not in allowed_values:
            raise ValueError(
                f"field {field_id} ({display_name}) default '{default_value}' "
                f"not in allowed values {list(allowed_values)}"
            )

        fields[field_id] = FieldSpec(
            field_id=field_id,
            display_name=display_name,
            default_value=default_value,
            allowed_values=allowed_values,
            applies_to=applies_to,
            wire_shape=wire_shape,
        )

    return fields


def inject_required_fields(
    args: Mapping[str, object],
    fields: Mapping[str, FieldSpec],
    issue_type: str,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a NEW args dict with required-field values added to additional_fields.

    Behaviour:
      * For every `field` in `fields` whose `applies_to` includes `issue_type`,
        insert `field.field_id` into `args["additional_fields"]` using
        `field.to_payload(value)` -- with `value` taken from `overrides` when
        present, else from `field.default_value`.
      * If a value already exists at `additional_fields[<field_id>]`, the
        caller's value WINS -- the helper does not overwrite. This lets PMs
        plumb ad-hoc values through fixtures or custom action plans.
      * Existing entries in `additional_fields` for OTHER field IDs are
        preserved.
      * The helper never mutates the input args dict; callers can rely on
        functional behaviour.

    Raises:
      ValueError if `overrides` references an unknown `field_id` (typo guard)
      or if any override value is outside the field's `allowed_values` list.
    """
    overrides = dict(overrides or {})

    unknown = set(overrides) - set(fields)
    if unknown:
        raise ValueError(
            f"unknown override field id(s): {sorted(unknown)}; "
            f"known fields: {sorted(fields)}"
        )
    for fid, value in overrides.items():
        spec = fields[fid]
        if value not in spec.allowed_values:
            raise ValueError(
                f"override value '{value}' for {fid} ({spec.display_name}) "
                f"not in allowed values {list(spec.allowed_values)}"
            )

    out: dict[str, object] = json.loads(json.dumps(dict(args)))
    existing_additional: dict[str, object] = dict(out.get("additional_fields", {}))

    added_any = False
    for fid, spec in fields.items():
        if issue_type not in spec.applies_to:
            continue
        if fid in existing_additional:
            added_any = True
            continue
        value = overrides.get(fid, spec.default_value)
        existing_additional[fid] = spec.to_payload(value)
        added_any = True

    if added_any:
        out["additional_fields"] = existing_additional
    return out


def _strip_backticks(value: str) -> str:
    """Strip a single pair of surrounding backticks; return inner string."""
    match = BACKTICK_TOKEN_RE.fullmatch(value.strip())
    if match:
        return match.group(1)
    return value.strip()


def _parse_backtick_list(value: str) -> tuple[str, ...]:
    """Extract every backtick-quoted token from a comma-separated string."""
    return tuple(BACKTICK_TOKEN_RE.findall(value))


# ----- CLI dispatcher (manual inspection / smoke checks) -----


def _cli_parse(args: argparse.Namespace) -> int:
    text = args.conventions_path.read_text(encoding="utf-8")
    try:
        fields = parse_required_fields(text)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = {
        fid: dataclasses.asdict(spec) for fid, spec in fields.items()
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    parse_cmd = sub.add_parser(
        "parse",
        help="Parse a conventions file and print parsed FieldSpecs as JSON.",
    )
    parse_cmd.add_argument(
        "--conventions-path",
        type=Path,
        required=True,
        help="Path to .agents/jira-conventions.md (or fixture).",
    )
    parse_cmd.set_defaults(func=_cli_parse)

    parsed = parser.parse_args(argv)
    return parsed.func(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
