"""Compile shared minimal defaults or an immutable per-game detection override."""

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

LISTS = (
    "paths",
    "path_prefixes",
    "url_schemes",
    "environment_names",
    "image_basenames",
)
FEATURES = ("filesystem", "foundation", "urls", "environment", "images")
APIS = {
    "access": "filesystem",
    "stat": "filesystem",
    "lstat": "filesystem",
    "open": "filesystem",
    "fopen": "filesystem",
    "getenv": "environment",
    "foundation_exists": "foundation",
    "foundation_attributes": "foundation",
    "urls": "urls",
}
FIELDS = {
    "schema_version",
    "game_version",
    "mode",
    "images",
    "adapters",
    "apis",
    "diagnostics",
    *LISTS,
}
BROAD = {
    "/",
    "/private",
    "/var",
    "/var/mobile",
    "/private/var",
    "/private/var/mobile",
    "/Library",
    "/System",
    "/Applications",
    "/usr",
    "/usr/lib",
    "/bin",
    "/sbin",
    "/etc",
    "/private/etc",
}
FRAMEWORK_ROOTS = ("/System/Library/Frameworks", "/System/Library/PrivateFrameworks")
CONTAINER_ROOTS = (
    "/var/containers",
    "/private/var/containers",
    "/var/mobile/Containers",
    "/private/var/mobile/Containers",
)

DEFAULT_PATHS = (
    "/Applications/Cydia.app",
    "/Applications/RockApp.app",
    "/Applications/Icy.app",
    "/usr/sbin/sshd",
    "/usr/bin/sshd",
    "/usr/libexec/sftp-server",
    "/Applications/WinterBoard.app",
    "/Applications/SBSettings.app",
    "/Applications/MxTube.app",
    "/Applications/IntelliScreen.app",
    "/Library/MobileSubstrate/DynamicLibraries/Veency.plist",
    "/Applications/FakeCarrier.app",
    "/Library/MobileSubstrate/DynamicLibraries/LiveClock.plist",
    "/private/var/lib/apt",
    "/Applications/blackra1n.app",
    "/private/var/stash",
    "/private/var/mobile/Library/SBSettings/Themes",
    "/System/Library/LaunchDaemons/com.ikey.bbot.plist",
    "/System/Library/LaunchDaemons/com.saurik.Cydia.Startup.plist",
    "/private/var/tmp/cydia.log",
    "/private/var/lib/cydia",
    "/var/jb/Applications/Sileo.app",
)


def default_profile(meta):
    """Shared minimal policy for compatible builds; explicit profiles always override it."""
    version = meta.get("tested_app_version")
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,3}", version
    ):
        return None
    return {
        "schema_version": 1,
        "game_version": version,
        "mode": "filter",
        "diagnostics": False,
        "images": ["@executable", "Frameworks/UnityFramework.framework/UnityFramework"],
        "adapters": ["filesystem", "foundation"],
        "apis": ["stat", "foundation_exists"],
        "paths": list(DEFAULT_PATHS),
    }


def build_sources(folder, sources):
    """Migrate only the known uncustomized legacy implementation at build time."""
    tokens = sources.split()
    legacy = Path(folder) / "SideloadCompat.m"
    if "SideloadCompat.m" not in tokens or not legacy.is_file():
        return sources
    if (
        hashlib.sha256(legacy.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        != "0ac7bf91a8594e540813290758370b07c0a70cb82d9e0640832eb07946b6c888"
    ):
        return sources
    return " ".join(
        "runtime/SideloadCompat.m" if token == "SideloadCompat.m" else token
        for token in tokens
    )


def problems(data, app_version=None):
    if not isinstance(data, dict):
        return ["profile must be an object"]
    errors = []
    if set(data) - FIELDS:
        errors.append("unknown profile fields")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        errors.append("schema_version must be 1")
    version = data.get("game_version")
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,3}", version
    ):
        errors.append("game_version must be a numeric app version")
    if app_version is not None and version != str(app_version):
        errors.append("game_version does not match the mod's tested_app_version")
    if data.get("mode") not in ("off", "observe", "filter"):
        errors.append("mode must be off, observe or filter")
    if "diagnostics" in data and type(data["diagnostics"]) is not bool:
        errors.append("diagnostics must be true or false")
    if "apis" in data:
        apis = data["apis"]
        if not isinstance(apis, list) or len(apis) > len(APIS):
            errors.append("apis must be a list of supported entry points")
        else:
            seen_apis = set()
            for api in apis:
                if not isinstance(api, str) or api not in APIS:
                    errors.append("unknown API entry point")
                elif api in seen_apis:
                    errors.append("duplicate API entry point")
                else:
                    seen_apis.add(api)
                    if (
                        not isinstance(data.get("adapters"), list)
                        or APIS[api] not in data["adapters"]
                    ):
                        errors.append("API entry point requires its adapter")
    for key in ("images", "adapters", *LISTS):
        values = data.get(key, [] if key in LISTS else None)
        if not isinstance(values, list) or len(values) > 64:
            errors.append(f"{key} must be a list of at most 64 strings")
            continue
        seen = set()
        for value in values:
            limit = 4095 if key in ("paths", "path_prefixes") else 255
            if (
                not isinstance(value, str)
                or not value
                or "\0" in value
                or len(value.encode("utf-8", errors="replace")) > limit
            ):
                errors.append(f"invalid {key} entry")
                continue
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                errors.append(f"invalid UTF-8 in {key}")
                continue
            normalized = value.lower() if key == "url_schemes" else value
            if normalized in seen:
                errors.append(f"duplicate {key} entry")
            seen.add(normalized)
            if key in ("paths", "path_prefixes"):
                if (
                    not value.startswith("/")
                    or value in BROAD
                    or "//" in value
                    or any(part in (".", "..") for part in value.split("/"))
                    or value.endswith(("/", "/Containers"))
                    or "/Containers/" in value
                    or any(
                        value == root
                        or value.startswith(root + "/")
                        or (
                            key == "path_prefixes"
                            and root.startswith(value.rstrip("/") + "/")
                        )
                        for root in (*FRAMEWORK_ROOTS, *CONTAINER_ROOTS)
                    )
                ):
                    errors.append(f"broad or ambiguous {key} entry")
            elif key == "url_schemes" and not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9+.-]*", value
            ):
                errors.append("invalid URL scheme")
            elif key == "environment_names" and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", value
            ):
                errors.append("invalid environment name")
            elif key == "image_basenames" and (
                "/" in value or "\\" in value or value in (".", "..")
            ):
                errors.append("image_basenames cannot contain paths")
            elif key == "adapters" and value not in FEATURES:
                errors.append("unknown adapter")
            elif (
                key == "images"
                and value != "@executable"
                and (
                    not value.startswith("Frameworks/")
                    or "\\" in value
                    or "//" in value
                    or any(part in (".", "..") for part in value.split("/"))
                    or value.endswith("/")
                    or str(PurePosixPath(value)) != value
                )
            ):
                errors.append(
                    "images must name @executable or a relative Frameworks image"
                )
        if key == "images" and not values and data.get("mode") != "off":
            errors.append("an active profile needs explicit images")
    if data.get("mode") == "filter" and data.get("image_basenames"):
        errors.append("image concealment is unavailable; use observe mode")
    return errors


def c_string(value):
    return (
        '"'
        + "".join(
            chr(b) if 32 <= b < 127 and b not in (34, 92) else f"\\{b:03o}"
            for b in value.encode("utf-8")
        )
        + '"'
    )


def profile_identity(data):
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def rule_identity(kind, value):
    if kind == "url_schemes":
        value = value.lower()
    return hashlib.sha256((kind + ":" + value).encode("utf-8")).hexdigest()[:16]


def render(data):
    errors = problems(data)
    if errors:
        raise ValueError("; ".join(errors))
    out = [
        "/* Compiled from detection.json. Restart required after profile changes. */",
        '#include "AMDetectionPolicy.h"',
        f"#define AM_DETECTION_PROFILE_ID {c_string(profile_identity(data))}",
        f"#define AM_DETECTION_GAME_VERSION {c_string(data['game_version'])}",
        f"#define AM_DETECTION_PROFILE_MODE AM_DETECTION_{data['mode'].upper()}",
        f"#define AM_DETECTION_DIAGNOSTICS_ENABLED {int(data.get('diagnostics', True))}",
    ]
    for feature in FEATURES:
        out.append(
            f"#define AM_DETECTION_FEATURE_{feature.upper()} {int(feature in data.get('adapters', []))}"
        )
    selected = data.get(
        "apis",
        [api for api, feature in APIS.items() if feature in data.get("adapters", [])],
    )
    for api in APIS:
        out.append(f"#define AM_DETECTION_API_{api.upper()} {int(api in selected)}")
    for key in ("images", *LISTS):
        values = data.get(key, [])
        out.append(
            f"static const char *const am_detection_{key}[] = {{"
            + (", ".join(c_string(v) for v in values) or "NULL")
            + "};"
        )
        out.append(f"#define AM_DETECTION_{key.upper()}_COUNT {len(values)}")
    out.append(
        "static const char *const am_detection_image_ids[] = {"
        + (
            ", ".join(c_string(rule_identity("images", v)) for v in data["images"])
            or "NULL"
        )
        + "};"
    )
    out.append("static const char *const am_detection_rule_ids[5][64] = {{NULL},")
    for key in ("paths", "path_prefixes", "url_schemes", "environment_names"):
        out.append(
            "    {"
            + (
                ", ".join(c_string(rule_identity(key, v)) for v in data.get(key, []))
                or "NULL"
            )
            + "},"
        )
    out.append("};")
    out += [
        "static const AMDetectionProfile am_detection_profile = {",
        "    .mode = AM_DETECTION_PROFILE_MODE, .rules = {",
        "        .paths = am_detection_paths, .path_count = AM_DETECTION_PATHS_COUNT,",
        "        .path_prefixes = am_detection_path_prefixes, .prefix_count = AM_DETECTION_PATH_PREFIXES_COUNT,",
        "        .url_schemes = am_detection_url_schemes, .scheme_count = AM_DETECTION_URL_SCHEMES_COUNT,",
        "        .environment_names = am_detection_environment_names, .environment_count = AM_DETECTION_ENVIRONMENT_NAMES_COUNT",
        "    }",
        "};",
        "",
    ]
    return "\n".join(out)


def generate(folder):
    folder = Path(folder)
    source = folder / "detection.json"
    dest = folder / "runtime/AMDetectionProfile.gen.h"
    meta = json.loads((folder / "mod.json").read_text(encoding="utf-8"))
    data = (
        json.loads(source.read_text(encoding="utf-8"))
        if source.exists()
        else default_profile(meta)
    )
    if data is None:
        dest.unlink(missing_ok=True)
        print("detection: no valid tested_app_version; shared defaults unavailable")
        return False
    if not source.exists():
        print(
            "detection: configured shared minimal defaults (22 exact paths; stat + fileExistsAtPath)"
        )
    errors = problems(data, meta.get("tested_app_version"))
    if errors:
        raise ValueError("; ".join(errors))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(data), encoding="utf-8")
    return True


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 3 and args[0] == "--build-sources":
        print(build_sources(args[1], args[2]))
        return
    if len(args) != 1:
        raise SystemExit("usage: detection_profile.py <mod folder>")
    try:
        generate(args[0])
    except (ValueError, OSError) as error:
        raise SystemExit(f"detection.json: {error}") from None


if __name__ == "__main__":
    main()
