"""Local, per-launch diagnostic phase commands; never controls gameplay."""

import hashlib
import json
import re
import subprocess
import time
import uuid

PHASES = ("startup", "login", "menu", "fight", "results")


def phase_request(report, phase):
    session = report.get("session_id")
    if (
        report.get("schema_version") not in (2, 3)
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
        if result.returncode or not local.exists() or local.stat().st_size > 512 * 1024:
            raise dev.DevError(
                "bounded local diagnostic report unavailable; enable diagnostics in the profile, rebuild and keep the game open"
            )
        try:
            report = json.loads(local.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise dev.DevError("local diagnostic report unreadable") from error
        if not isinstance(report, dict):
            raise dev.DevError("invalid local diagnostic report")
        return report

    report = read()
    if args.action == "report":
        print(json.dumps(report, indent=2))
        return 0
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
