"""Local, per-launch diagnostic phase commands; never controls gameplay."""

import hashlib
import json
import re
import subprocess
import sys
import time
import uuid

PHASES = ("startup", "login", "menu", "fight", "results")
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_TEXT = 256
MAX_ROWS = 4096
MAX_COUNT = 2**63 - 1
STATES = ("disabled", "observing", "observation-only", "filtering", "unsupported", "failed")
# Keys of the two dynamic maps. These mirror runtime/AMDetectionRegistry.c entry
# names plus the two concealment markers, and AMImportFailureName in
# runtime/AMImportBindings.c; a new registry entry must be added here too.
ADAPTER_NAMES = (
    "access", "stat", "lstat", "open", "fopen", "getenv", "foundation", "urls",
    "foundation_exists", "foundation_attributes", "can_open_url", "readlink", "fstatat",
    "opendir", "readdir", "closedir", "foundation_directory_paths", "foundation_directory_urls",
    "foundation_enumerator_paths", "foundation_enumerator_urls", "posix_spawn",
    "foundation_string_write_path", "foundation_string_write_url", "foundation_data_write_path",
    "foundation_data_write_url", "sysctl", "image_concealment", "class_concealment",
)
FAILURE_REASONS = (
    "architecture", "chained_fixups", "authenticated_segments", "classic_tables_missing",
    "malformed_layout", "unknown",
)

_HEX32 = re.compile(r"[0-9a-f]{32}")
_TEXT = re.compile(r"[ -~]*")

# Field allowlists for each report schema. Every key the runtime emits is listed
# with its shape; anything else is rejected before the report is printed or
# used, so an unexpected field can never reach standard output. Schema 4 is
# `AMDetectionReport` in runtime/AMDetection.m; schema 3 is the layout that
# commit 713448d replaced. No schema 2 emitter exists in this repository's
# history, so schema 2 reuses the schema 3 allowlist.

_COUNTS = {"calls": "count", "matched": "count", "filtered": "count"}
_EVIDENCE = {
    "implemented": "bool",
    "statically_supported": "flag",
    "host_contract_tested": "flag",
    "arm64_built": "flag",
    "device_compatible": "flag",
    "positive_artifact_filtering": "flag",
}
_ENTRY = {
    "entry_id": "count",
    "name": "text",
    "module": "text",
    "state": ("enum", STATES),
    "reason": "text",
    "aggregate": "bool",
    "attribution_unit": "count",
    "evidence": ("dict", _EVIDENCE),
    **_COUNTS,
}
_ADAPTER_V3 = {"state": ("enum", STATES), **_COUNTS}
_ATTRIBUTION = {
    "entry_id": "count",
    "module": "text",
    "image_id": "text",
    "phase": ("enum", PHASES),
    "api": "text",
    "rule_id": ("optional", "text"),
    **_COUNTS,
}
_RESULTS = {key: "count" for key in ("unknown", "success", "negative", "error", "original_not_called")}
_CALLSITE = {
    **_ATTRIBUTION,
    "build_uuid": "text",
    "caller_id": "text",
    "results": ("dict", _RESULTS),
}
_COVERAGE = {
    "incomplete": "bool",
    "saturated": "bool",
    "allocation_failures": "count",
    "operational_gaps": "count",
}
_ROOT_V3 = {
    "schema_version": ("enum", (2, 3, 4)),
    "profile_mode": ("enum", (0, 1, 2)),
    "profile_id": "text",
    "callsite_id_algorithm": "text",
    "callsites": ("list", ("dict", _CALLSITE)),
    "callsite_capacity": "count",
    "callsites_dropped": "count",
    "session_id": "hex32_or_empty",
    "phase": ("enum", PHASES),
    "phase_request_id": "hex32_or_empty",
    "attribution": ("list", ("dict", _ATTRIBUTION)),
    "attribution_capacity": "count",
    "attribution_dropped": "count",
    "elapsed_ms": "number",
    "version_match": "bool",
    "image_layout_failures": "count",
    "image_layout_failure_reasons": ("map", (FAILURE_REASONS, "count")),
    "loaded_image_count": "count",
    "adapters": ("map", (ADAPTER_NAMES, ("dict", _ADAPTER_V3))),
}
_ROOT_V4 = {
    **_ROOT_V3,
    "adapters": ("map", (ADAPTER_NAMES, ("dict", _ENTRY))),
    "entries": ("list", ("dict", _ENTRY)),
    "coverage": ("dict", _COVERAGE),
    "fingerprints_status": "text",
}
REPORT_SCHEMAS = {2: _ROOT_V3, 3: _ROOT_V3, 4: _ROOT_V4}


def _reject(path, why):
    raise ValueError("diagnostic report field " + path + " " + why + "; rebuild the runtime and local client together")


def _project(value, spec, path):
    """Return a copy of `value` holding only the fields `spec` allows."""
    if isinstance(spec, tuple):
        kind, detail = spec
    else:
        kind, detail = spec, None
    if kind == "optional":
        return None if value is None else _project(value, detail, path)
    if kind == "dict" or kind == "map":
        if not isinstance(value, dict):
            _reject(path, "is not an object")
        if len(value) > MAX_ROWS:
            _reject(path, "has too many fields")
        out = {}
        names, item_spec = detail if kind == "map" else (detail, None)
        for key, item in value.items():
            # An unknown key is unvalidated content: name the parent, not the key.
            if not isinstance(key, str) or key not in names:
                _reject(path, "holds a field that is not allowed")
            child = path + "." + key
            out[key] = _project(item, item_spec if kind == "map" else detail[key], child)
        return out
    if kind == "list":
        if not isinstance(value, list):
            _reject(path, "is not a list")
        if len(value) > MAX_ROWS:
            _reject(path, "has too many rows")
        return [_project(item, detail, path + "[" + str(i) + "]") for i, item in enumerate(value)]
    if kind == "enum":
        if isinstance(value, bool) or value not in detail:
            _reject(path, "has an unsupported value")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            _reject(path, "is not a boolean")
        return value
    if kind == "count":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
            _reject(path, "is not a bounded count")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_COUNT:
            _reject(path, "is not a bounded number")
        return value
    if kind == "flag":
        if isinstance(value, bool):
            return value
        kind = "text"
    if kind == "text":
        if not isinstance(value, str) or len(value) > MAX_TEXT or not _TEXT.fullmatch(value):
            _reject(path, "is not bounded printable text")
        return value
    if kind == "hex32_or_empty":
        if not isinstance(value, str) or (value and not _HEX32.fullmatch(value)):
            _reject(path, "is not a session token")
        return value
    raise AssertionError("unknown field spec " + str(kind))


def validate_report(report):
    """Return an export projection of `report`, or raise ValueError.

    Only fields on the schema's allowlist survive, each checked for shape and
    bounds at every nesting level. Error text names the field path and never
    repeats the offending value.
    """
    if not isinstance(report, dict) or report.get("schema_version") not in REPORT_SCHEMAS:
        raise ValueError("unsupported diagnostic report schema; update the local client/runtime")
    return _project(report, ("dict", REPORT_SCHEMAS[report["schema_version"]]), "report")


def phase_request(report, phase):
    session = report.get("session_id")
    if (
        report.get("schema_version") not in (2, 3, 4)
        or report.get("profile_mode") not in (1, 2)
        or not isinstance(session, str)
        or not re.fullmatch(r"[0-9a-f]{32}", session)
    ):
        raise ValueError(
            "a current running observation report is required; rebuild older runtimes"
        )
    if phase not in PHASES:
        raise ValueError("unknown diagnostic phase")
    return {"session_id": session, "request_id": uuid.uuid4().hex, "phase": phase}


def acknowledged(report, request):
    return all(
        report.get(key) == value
        for key, value in {
            "session_id": request["session_id"],
            "phase_request_id": request["request_id"],
            "phase": request["phase"],
        }.items()
    )


def summarize(report):
    """Short human verdict for a validated report; one fact per line."""
    modes = {0: "off", 1: "observe", 2: "filter"}
    lines = [
        "schema " + str(report.get("schema_version", "?"))
        + ", mode " + modes.get(report.get("profile_mode"), "unknown")
        + ", phase " + str(report.get("phase", "unknown"))
        + ", elapsed " + str(int(report.get("elapsed_ms", 0) // 1000)) + " s"
    ]
    facts = []
    if report.get("version_match") is False:
        facts.append("game version mismatch: modules disabled")
    status = report.get("fingerprints_status")
    if status not in (None, "not_requested", "matched"):
        facts.append("fingerprints " + status)
    coverage = report.get("coverage") or {}
    if coverage.get("incomplete") or coverage.get("saturated"):
        facts.append("telemetry incomplete")
    dropped = report.get("attribution_dropped", 0) + report.get("callsites_dropped", 0)
    if dropped:
        facts.append("rows dropped " + str(dropped))
    if facts:
        lines.append("; ".join(facts))
    by_state = {}
    for entry in report.get("entries") or []:
        state = entry.get("state", "unknown")
        label = entry.get("name", str(entry.get("entry_id", "?")))
        if state in ("unsupported", "failed") and entry.get("reason", "none") != "none":
            label += " (" + entry["reason"] + ")"
        by_state.setdefault(state, []).append(label)
    for state in ("filtering", "observing", "unsupported", "failed", "disabled"):
        if state in by_state:
            lines.append(state + ": " + ", ".join(by_state[state]))
    matched = sum(entry.get("matched", 0) for entry in report.get("entries") or [])
    filtered = sum(entry.get("filtered", 0) for entry in report.get("entries") or [])
    lines.append("matched calls " + str(matched) + ", filtered " + str(filtered))
    return lines


def command(dev, args):
    if args.action == "preflight":
        import detection_preflight

        path = dev.Path(args.target)
        if not path.is_file():
            meta = dev.read_json(dev.mod_dir(args.target) / "mod.json")
            path = dev.find_base(args.target, meta)
        cli = [str(path)]
        for name in ("profile", "output"):
            if value := getattr(args, name, None):
                cli += ["--" + name, value]
        return detection_preflight.main(cli)
    target = dev.Path(args.target)
    folder = target if target.is_dir() else dev.mod_dir(args.target)
    meta = dev.read_json(folder / "mod.json")
    bundles = dev.installed_bundles(meta["bundle_id"])
    if len(bundles) != 1:
        raise dev.DevError("one installed copy is required")
    cli, bundle = dev.pymobiledevice3_cli(), bundles[0]
    key = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:16]
    work = dev.am_root() / "work" / "detection" / key
    work.mkdir(parents=True, exist_ok=True)
    local = work / "report.json"

    def transfer(arguments):
        try:
            return subprocess.run(
                [cli, "apps", *arguments], capture_output=True, timeout=20, check=False
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            raise dev.DevError("USB diagnostic transfer unavailable") from error

    def read():
        result = transfer(["pull", bundle, "Documents/am_detection.json", str(local)])
        if result.returncode or not local.exists() or local.stat().st_size > MAX_REPORT_BYTES:
            raise dev.DevError(
                "bounded local diagnostic report unavailable; enable diagnostics in the profile, rebuild and keep the game open"
            )
        try:
            report = json.loads(local.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise dev.DevError("local diagnostic report unreadable") from error
        try:
            return validate_report(report)
        except ValueError as error:
            raise dev.DevError(str(error)) from error

    if args.action == "report":
        # The runtime writes its first report 30 s after launch; poll until it is pullable.
        until = time.monotonic() + max(0, getattr(args, "wait", 0))
        while True:
            try:
                report = read()
                break
            except dev.DevError as error:
                if "unavailable" not in str(error) or time.monotonic() >= until:
                    raise
                time.sleep(2)
        print(json.dumps(report, indent=2))
        print("\n".join(summarize(report)), file=sys.stderr)
        return 0
    report = read()
    try:
        request = phase_request(report, args.phase)
    except ValueError as error:
        raise dev.DevError(str(error)) from error
    control = work / "phase.json"
    control.write_text(json.dumps(request), encoding="utf-8")
    result = transfer(
        [
            "push",
            bundle,
            str(control),
            "Documents/am_detection_phase.json",
        ]
    )
    if result.returncode:
        raise dev.DevError("diagnostic phase request could not be pushed")
    until = time.monotonic() + args.wait
    while time.monotonic() < until:
        report = read()
        if acknowledged(report, request):
            print(
                "Phase acknowledged: "
                + args.phase
                + ". Subsequent calls use this phase; gameplay is unchanged."
            )
            return 0
        time.sleep(0.5)
    raise dev.DevError("phase not acknowledged; keep the game open and retry")
