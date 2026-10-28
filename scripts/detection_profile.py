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
    "environment_allowlist",
    "image_basenames",
)
FEATURES = ("filesystem", "foundation", "urls", "environment", "images", "directories", "writes", "process")
MODULES = ("filesystem", "environment", "foundation", "urls", "directories", "writes",
           "preferences", "loader", "objc", "process", "sdk")
ADVANCED_FIELDS = {"image_fingerprints", "module_modes", "write_probes", "preference_domains",
                   "spawn_rules", "hidden_components", "sdk_adapters"}
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
LEGACY_APIS = frozenset(APIS)
APIS.update({"readlink": "filesystem", "fstatat": "filesystem",
             "opendir": "directories", "readdir": "directories", "closedir": "directories",
             "foundation_directory_paths": "directories", "foundation_directory_urls": "directories",
             "foundation_enumerator_paths": "directories", "foundation_enumerator_urls": "directories"})
APIS.update({"posix_spawn": "process", "sysctl": "process",
             "foundation_string_write_path": "writes", "foundation_string_write_url": "writes",
             "foundation_data_write_path": "writes", "foundation_data_write_url": "writes"})
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


def problems(data, app_version=None):
    if not isinstance(data, dict):
        return ["profile must be an object"]
    errors = []
    schema = data.get("schema_version")
    if set(data) - (FIELDS | (ADVANCED_FIELDS if schema == 2 else set())):
        errors.append("unknown profile fields")
    if type(schema) is not int or schema not in (1, 2):
        errors.append("schema_version must be 1 or 2")
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
        if not isinstance(apis, list) or len(apis) > min(64, len(APIS)):
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
                    if api not in LEGACY_APIS and schema != 2:
                        errors.append("advanced API entry points require schema 2")
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
            elif key in ("environment_names", "environment_allowlist") and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", value
            ):
                errors.append("invalid environment name")
            elif key == "environment_allowlist" and schema != 2:
                errors.append("environment_allowlist requires schema 2")
            elif key == "environment_allowlist" and value in (
                data.get("environment_names") if isinstance(data.get("environment_names"), list) else []
            ):
                errors.append("environment_allowlist entry is also in environment_names")
            elif key == "image_basenames" and (
                "/" in value or "\\" in value or value in (".", "..")
            ):
                errors.append("image_basenames cannot contain paths")
            elif key == "adapters" and value not in FEATURES:
                errors.append("unknown adapter")
            elif key == "adapters" and value in ("directories", "writes", "process") and schema != 2:
                errors.append("advanced adapter requires schema 2")
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
    if schema == 2:
        errors.extend(advanced_problems(data))
    return errors


def advanced_problems(data):
    from detection_identity import identity_problems

    errors = []
    rank = {"off": 0, "observe": 1, "filter": 2}
    modes = data.get("module_modes", {})
    if not isinstance(modes, dict) or set(modes) - set(MODULES):
        errors.append("unknown or invalid module_modes")
    else:
        global_rank = rank.get(data.get("mode"), -1) if isinstance(data.get("mode"), str) else -1
        for mode in modes.values():
            if not isinstance(mode, str) or mode not in rank or rank[mode] > global_rank:
                errors.append("module modes can only reduce global mode")
        if (data.get("mode") == "observe" or "observe" in modes.values()) and not data.get("diagnostics", True):
            errors.append("observation modules require diagnostics enabled")
    fingerprints = data.get("image_fingerprints", {})
    if not isinstance(fingerprints, dict) or len(fingerprints) > 64:
        errors.append("image_fingerprints must be an object of at most 64 images")
    else:
        images = data.get("images", [])
        total = 0
        for image, identity in fingerprints.items():
            if not isinstance(images, list) or image not in images:
                errors.append("fingerprint image must be selected")
            invalid = identity_problems(identity)
            errors.extend(invalid)
            if not invalid:
                total += sum(r["size"] for r in identity["ranges"])
        if total > 1024 * 1024 * 1024:
            errors.append("aggregate fingerprint read budget exceeds 1 GiB")
    probes = data.get("write_probes", [])
    if not isinstance(probes, list) or len(probes) > 64:
        errors.append("write_probes must be a list of at most 64 exact probes")
    else:
        seen = set()
        for probe in probes:
            if isinstance(probe, dict) and set(probe)=={"token"} and probe["token"]=="private_display_name":
                if "@private_display_name" in seen: errors.append("duplicate write probe")
                seen.add("@private_display_name");continue
            if not isinstance(probe, dict) or set(probe) != {"path"} or not isinstance(probe["path"], str):
                errors.append("write probe requires an exact path")
                continue
            path = probe["path"]
            if path in seen:
                errors.append("duplicate write probe")
            seen.add(path)
            base = {"schema_version": 1, "game_version": data.get("game_version"), "mode": "off",
                    "images": [], "adapters": [], "paths": [path]}
            if problems(base):
                errors.append("broad, ambiguous or protected write probe")
    selected = {api for api in selected_apis(data) if isinstance(api, str)}
    directory_group = {"opendir", "readdir", "closedir"}
    if selected & directory_group and not directory_group <= selected:
        errors.append("native directory filtering requires opendir, readdir and closedir together")
    if probes and not selected.intersection(("open", "fopen", "foundation_string_write_path", "foundation_string_write_url", "foundation_data_write_path", "foundation_data_write_url")):
        errors.append("write probes require an explicit write-capable entry point")
    spawns = data.get("spawn_rules", [])
    if not isinstance(spawns, list) or len(spawns)>64:
        errors.append("spawn_rules must contain at most 64 exact executable paths")
    else:
        seen = set()
        for rule in spawns:
            if (not isinstance(rule, dict) or set(rule) not in ({"path"},{"path","error"}) or
                    not isinstance(rule.get("path"),str) or rule.get("error","EACCES")!="EACCES"):
                errors.append("spawn rule requires an exact path and EACCES denial");continue
            path=rule["path"]
            if path in seen: errors.append("duplicate spawn rule")
            seen.add(path)
            base={"schema_version":1,"game_version":data.get("game_version"),"mode":"off","images":[],"adapters":[],"paths":[path]}
            if problems(base): errors.append("broad, ambiguous or protected spawn rule")
        if spawns and "posix_spawn" not in selected:
            errors.append("spawn rules require posix_spawn selection")
    if (selected - LEGACY_APIS or probes) and data.get("mode") != "off":
        images = data.get("images", [])
        if (not isinstance(fingerprints, dict) or not isinstance(images, list) or
                any(not isinstance(image, str) for image in images) or set(fingerprints) != set(images)):
            errors.append("advanced entry points require fingerprints for all selected images")
    # Reserve remaining fields without silently accepting unimplemented policy.
    # Their owning adapter task replaces this rejection with its exact contract.
    for name in ("preference_domains", "hidden_components", "sdk_adapters"):
        value = data.get(name, [])
        if not isinstance(value, list) or value:
            errors.append(f"{name} requires its supported adapter; currently unavailable")
    return errors


def selected_apis(data):
    if "apis" in data:
        return data["apis"] if isinstance(data["apis"], list) else []
    adapters = data.get("adapters", [])
    if not isinstance(adapters, list):
        return []
    # New entry points are always explicit, including schema-2 profiles.
    return [api for api, feature in APIS.items() if api in LEGACY_APIS and feature in adapters]


def c_string(value):
    return (
        '"'
        + "".join(
            chr(b) if 32 <= b < 127 and b not in (34, 92) else f"\\{b:03o}"
            for b in value.encode("utf-8")
        )
        + '"'
    )


def expand_write_probe(display_name):
    if (not isinstance(display_name, str) or not display_name or display_name in (".", "..") or
            any(c in display_name for c in ("/", "\\", "\0")) or
            display_name.lower() in ("var", "etc", "tmp", "preboot") or
            len(display_name.encode("utf-8")) > 4086):
        raise ValueError("display name must be a bounded nonprotected path component")
    return "/private/" + display_name


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
        '#include "AMDetectionIdentity.h"',
        '#include "AMDetectionRegistry.h"',
        f"#define AM_DETECTION_PROFILE_ID {c_string(profile_identity(data))}",
        f"#define AM_DETECTION_GAME_VERSION {c_string(data['game_version'])}",
        f"#define AM_DETECTION_PROFILE_MODE AM_DETECTION_{data['mode'].upper()}",
        f"#define AM_DETECTION_DIAGNOSTICS_ENABLED {int(data.get('diagnostics', True))}",
    ]
    for feature in FEATURES:
        out.append(
            f"#define AM_DETECTION_FEATURE_{feature.upper()} {int(feature in data.get('adapters', []))}"
        )
    selected = selected_apis(data)
    for api in APIS:
        out.append(f"#define AM_DETECTION_API_{api.upper()} {int(api in selected)}")
    modes = data.get("module_modes", {})
    out.append("static const AMDetectionMode am_detection_module_modes[AM_DETECTION_MODULE_COUNT] = {"
               + ",".join("AM_DETECTION_" + modes.get(m, data["mode"]).upper() for m in MODULES) + "};")
    fingerprints = data.get("image_fingerprints", {})
    out.append(f"#define AM_DETECTION_FINGERPRINT_COUNT {len(fingerprints)}")
    out.append("static const AMDetectionImageIdentity am_detection_image_fingerprints[] = {")
    for image in data["images"]:
        identity = fingerprints.get(image)
        if not identity:
            out.append("    {.range_count=0},")
            continue
        def byte_array(value):
            return "{" + ",".join(str(b) for b in bytes.fromhex(value)) + "}"
        ranges = ",".join("{" + str(r["offset"]) + "," + str(r["size"]) + "}" for r in identity["ranges"])
        out.append("    {.uuid=" + byte_array(identity["uuid"]) + ",.sha256=" + byte_array(identity["sha256"])
                   + ",.range_count=" + str(len(identity["ranges"])) + ",.ranges={" + ranges + "}},")
    if not data["images"]:
        out.append("    {.range_count=0},")
    out.append("};")
    for key in ("images", *LISTS):
        values = data.get(key, [])
        out.append(
            f"static const char *const am_detection_{key}[] = {{"
            + (", ".join(c_string(v) for v in values) or "NULL")
            + "};"
        )
        out.append(f"#define AM_DETECTION_{key.upper()}_COUNT {len(values)}")
    write_paths = [probe.get("path") for probe in data.get("write_probes", [])]
    out.append("static const char *am_detection_write_paths[] = {"
               + (", ".join(c_string(v) if v is not None else "NULL" for v in write_paths) or "NULL") + "};")
    out.append(f"#define AM_DETECTION_WRITE_PATHS_COUNT {len(write_paths)}")
    dynamic = [i for i,value in enumerate(write_paths) if value is None]
    out.append(f"#define AM_DETECTION_WRITE_DYNAMIC_COUNT {len(dynamic)}")
    out.append("static const unsigned am_detection_write_dynamic_indices[] = {" + (",".join(map(str,dynamic)) or "0") + "};")
    spawn_paths = [rule["path"] for rule in data.get("spawn_rules", [])]
    out.append("static const char *const am_detection_spawn_paths[] = {" + (",".join(c_string(p) for p in spawn_paths) or "NULL") + "};")
    out.append(f"#define AM_DETECTION_SPAWN_PATHS_COUNT {len(spawn_paths)}")
    out.append(
        "static const char *const am_detection_image_ids[] = {"
        + (
            ", ".join(c_string(rule_identity("images", v)) for v in data["images"])
            or "NULL"
        )
        + "};"
    )
    out.append("static const char *const am_detection_rule_ids[AM_DETECTION_RULE_COUNT][64] = {{NULL},")
    for key in ("paths", "path_prefixes", "url_schemes", "environment_names"):
        out.append(
            "    {"
            + (
                ", ".join(c_string(rule_identity(key, v)) for v in data.get(key, []))
                or "NULL"
            )
            + "},"
        )
    out.append("    {" + (", ".join(c_string(rule_identity("write_probes", v if v is not None else "@private_display_name")) for v in write_paths) or "NULL") + "},")
    out.append("    {" + (", ".join(c_string(rule_identity("spawn_rules", v)) for v in spawn_paths) or "NULL") + "},")
    out.append("};")
    out += [
        "static const AMDetectionProfile am_detection_profile = {",
        "    .mode = AM_DETECTION_PROFILE_MODE, .rules = {",
        "        .write_paths = am_detection_write_paths, .write_count = AM_DETECTION_WRITE_PATHS_COUNT,",
        "        .spawn_paths = am_detection_spawn_paths, .spawn_count = AM_DETECTION_SPAWN_PATHS_COUNT,",
        "        .paths = am_detection_paths, .path_count = AM_DETECTION_PATHS_COUNT,",
        "        .path_prefixes = am_detection_path_prefixes, .prefix_count = AM_DETECTION_PATH_PREFIXES_COUNT,",
        "        .url_schemes = am_detection_url_schemes, .scheme_count = AM_DETECTION_URL_SCHEMES_COUNT,",
        "        .environment_names = am_detection_environment_names, .environment_count = AM_DETECTION_ENVIRONMENT_NAMES_COUNT,",
        "        .environment_allowlist = am_detection_environment_allowlist, .allowlist_count = AM_DETECTION_ENVIRONMENT_ALLOWLIST_COUNT",
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
    if len(args) != 1:
        raise SystemExit("usage: detection_profile.py <mod folder>")
    try:
        generate(args[0])
    except (ValueError, OSError) as error:
        raise SystemExit(f"detection.json: {error}") from None


if __name__ == "__main__":
    main()
