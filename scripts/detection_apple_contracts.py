"""Run host Foundation contracts with version-bound evidence; never enables a game profile."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("tests/detection_foundation_directory_contract.m", "runtime/AMDetectionDirectories.m", "runtime/AMDetectionWrites.m",
           "runtime/AMDetectionPolicy.h", "runtime/AMDetectionPolicy.c", "runtime/AMDetectionRegistry.h",
           "runtime/AMDetectionRegistry.c", "runtime/AMDetectionHooks.h", "runtime/AMDetectionHooks.c",
           "runtime/AMDetectionFiles.h", "runtime/AMDetectionFiles.c", "runtime/AMDetectionDiagnostics.h",
           "runtime/AMDetectionDiagnostics.c", "scripts/detection_apple_contracts.py")


def run_contracts(output):
    source_data = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in SOURCES}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in source_data.items()}
    report = {"schema_version": 1, "fixture": "foundation-file-contract-v2",
              "source_digest_algorithm": "sha256-lf-source-map-v1", "sources": hashes,
              "source_digest": hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "status": "unavailable", "executed": False, "device_verified": False,
              "positive_artifact_verified": False, "scope": "direct-wrapper-macos-host",
              "passed_modes": [], "unsupported": ["foundation_enumerator_paths", "foundation_enumerator_urls"]}
    if sys.platform != "darwin":
        report["reason"] = "apple_foundation_execution_unavailable"
        return report, 2
    compiler = shutil.which("clang")
    if not compiler:
        report["reason"] = "apple_clang_unavailable"
        return report, 2
    report["os_version"] = platform.mac_ver()[0]
    report["architecture"] = platform.machine()
    with tempfile.TemporaryDirectory(prefix="foundation-contract-") as directory:
        work = Path(directory)
        snapshot = work / "source"
        for name, data in source_data.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        objects = []
        commands = []
        for name in ("AMDetectionPolicy", "AMDetectionRegistry", "AMDetectionHooks"):
            obj = work / (name + ".o")
            commands.append([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-UNDEBUG",
                             "-I", str(snapshot / "runtime"), "-c", str(snapshot / "runtime" / (name + ".c")), "-o", str(obj)])
            objects.append(str(obj))
        executable = work / "directory-contract"
        commands.append([compiler, "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-UNDEBUG",
                         "-I", str(snapshot / "runtime"), str(snapshot / SOURCES[0]), *objects,
                         "-framework", "Foundation", "-o", str(executable)])
        log = output.parent / "foundation-contract-build.log"
        with log.open("wb") as stream:
            for command in commands:
                try:
                    result = subprocess.run(command, stdout=stream, stderr=stream, timeout=60, check=False)
                except (OSError, subprocess.TimeoutExpired) as error:
                    report.update(status="failed", reason="compile_" + type(error).__name__)
                    return report, 1
                if result.returncode:
                    report.update(status="failed", reason="compile_failed", return_code=result.returncode)
                    return report, 1
            for mode in ("filter", "observe"):
                root = work / (mode + "-data")
                try:
                    result = subprocess.run([str(executable), mode, str(root)], capture_output=True, timeout=30, check=False)
                except subprocess.TimeoutExpired:
                    report.update(status="failed", executed=True, reason="execution_timeout", mode=mode)
                    return report, 1
                except OSError:
                    report.update(status="failed", reason="execution_start_failed", mode=mode)
                    return report, 1
                report["executed"] = True
                stream.write(result.stdout);stream.write(result.stderr)
                if result.returncode or result.stdout.strip() != b"PASS foundation-file-contract-v2":
                    report.update(status="failed", reason="contract_failed", mode=mode, return_code=result.returncode)
                    return report, 1
                report["passed_modes"].append(mode)
    report.update(status="passed", reason="host_contracts_passed_not_device_verification")
    return report, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="local evidence JSON path")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report, code = run_contracts(args.output)
    except (OSError, subprocess.TimeoutExpired) as error:
        report = {"schema_version": 1, "status": "failed", "executed": None,
                  "device_verified": False, "positive_artifact_verified": False,
                  "reason": "contract_runner_" + type(error).__name__}
        code = 1
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Foundation directory contracts: " + report["status"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
