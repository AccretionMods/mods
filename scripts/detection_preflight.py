"""Bounded, read-only IPA import preflight and an observation-only Unity preset."""

import argparse
import hashlib
import json
import plistlib
import re
import struct
import sys
import zipfile
from pathlib import Path
from xml.parsers.expat import ExpatError

APIS = {
    b"_" + name.encode(): name
    for name in ("access", "stat", "lstat", "open", "fopen", "getenv")
}
MAX_IMAGE = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024


class LayoutError(ValueError):
    pass


def _plist(data):
    try:
        value = plistlib.loads(data)
    except (ValueError, plistlib.InvalidFileException, ExpatError) as error:
        raise ValueError("invalid application or framework plist") from error
    if not isinstance(value, dict):
        raise LayoutError("invalid_plist_root")
    return value


def _range(data, offset, size):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise LayoutError("malformed_layout")
    return data[offset : offset + size]


def _unpack(fmt, data, offset=0):
    return struct.unpack(fmt, _range(data, offset, struct.calcsize(fmt)))


def _thin(data):
    magic = bytes(data[:4])
    fats = {
        b"\xca\xfe\xba\xbe": (">", 20),
        b"\xbe\xba\xfe\xca": ("<", 20),
        b"\xca\xfe\xba\xbf": (">", 32),
        b"\xbf\xba\xfe\xca": ("<", 32),
    }
    if magic not in fats:
        return data
    endian, stride = fats[magic]
    (count,) = _unpack(endian + "I", data, 4)
    if count > 64:
        raise LayoutError("malformed_layout")
    _range(data, 8, count * stride)
    for i in range(count):
        arch = _unpack(
            endian + ("5I" if stride == 20 else "2I2Q2I"), data, 8 + i * stride
        )
        if arch[0] == 0x100000C and arch[1] & 0xFFFFFF != 2:
            return _range(data, arch[2], arch[3])
    raise LayoutError("architecture")


def _chain_encoding(data):
    version, starts, *_ = _unpack("<7I", data)
    if version:
        raise LayoutError("chained_fixups")
    if starts < 28:
        raise LayoutError("malformed_layout")
    (count,) = _unpack("<I", data, starts)
    if count > 128:
        raise LayoutError("malformed_layout")
    _range(data, starts + 4, count * 4)
    found = False
    for i in range(count):
        (offset,) = _unpack("<I", data, starts + 4 + i * 4)
        if not offset:
            continue
        if offset < 4 + count * 4:
            raise LayoutError("malformed_layout")
        size, page_size, format_, _, _, pages = _unpack(
            "<IHHQIH", data, starts + offset
        )
        segment = _range(data, starts + offset, size)
        if (
            size < 22
            or pages > (len(segment) - 22) // 2
            or page_size not in (4096, 16384)
        ):
            raise LayoutError("malformed_layout")
        if format_ in (1, 7, 9, 10, 12, 13, 14):
            raise LayoutError("authenticated_segments")
        if format_ not in (2, 6):
            raise LayoutError("chained_fixups")
        found = True
    if not found:
        raise LayoutError("chained_fixups")


def inspect_macho(data):
    """Predict metadata support; never claims runtime binding or protection writes work."""
    result = {"state": "unsupported", "reason": None, "imports": {}}
    try:
        data = _thin(data)
        header = _unpack("<8I", data)
        if (
            header[0] != 0xFEEDFACF
            or header[1] != 0x100000C
            or header[2] & 0xFFFFFF == 2
        ):
            raise LayoutError("architecture")
        result["architecture"] = "arm64"
        if (
            header[4] > 4096
            or header[5] > 16 * 1024 * 1024
            or header[4] > header[5] // 8
        ):
            raise LayoutError("malformed_layout")
        _range(data, 32, header[5])
        cursor, end = 32, 32 + header[5]
        segments, sections = [], []
        sym = dysym = link = chain = None
        encrypted = False
        for _ in range(header[4]):
            cmd, size = _unpack("<II", data, cursor)
            if size < 8 or size % 8 or cursor + size > end:
                raise LayoutError("malformed_layout")
            command = _range(data, cursor, size)
            if cmd == 25:
                segment = _unpack("<II16s4Q4I", command)
                if (
                    segment[9] > (size - 72) // 80
                    or segment[6] > segment[4]
                    or len(segments) == 128
                ):
                    raise LayoutError("malformed_layout")
                if segment[3] + segment[4] > 0xFFFFFFFFFFFFFFFF:
                    raise LayoutError("malformed_layout")
                _range(data, segment[5], segment[6])
                segments.append(segment)
                if segment[2].startswith(b"__AUTH"):
                    raise LayoutError("authenticated_segments")
                if segment[2].rstrip(b"\0") == b"__LINKEDIT":
                    if link:
                        raise LayoutError("malformed_layout")
                    link = segment
                for i in range(segment[9]):
                    section = _unpack("<16s16s2Q8I", command, 72 + i * 80)
                    if section[8] & 255 in (6, 7):
                        sections.append((segment, section))
            elif cmd == 2:
                if sym:
                    raise LayoutError("malformed_layout")
                sym = _unpack("<6I", command)
            elif cmd == 11:
                if dysym:
                    raise LayoutError("malformed_layout")
                dysym = _unpack("<20I", command)
            elif cmd == 0x80000034:
                if chain or size != 16:
                    raise LayoutError("malformed_layout")
                chain = _unpack("<4I", command)
            elif cmd in (0x21, 0x2C):
                encrypted |= _unpack("<5I", command)[4] != 0
            cursor += size
        if cursor != end:
            raise LayoutError("malformed_layout")
        if encrypted:
            raise LayoutError("encrypted")
        if (
            not link
            or not sym
            or not dysym
            or not link[8] & 1
            or (chain and not dysym[15])
        ):
            raise LayoutError("classic_tables_missing")

        def linked(offset, size):
            if (
                offset < link[5]
                or offset - link[5] > link[6]
                or size > link[6] - (offset - link[5])
            ):
                raise LayoutError("malformed_layout")
            return _range(data, offset, size)

        if chain:
            _chain_encoding(linked(chain[2], chain[3]))
        symbols = linked(sym[2], sym[3] * 16)
        strings = linked(sym[4], sym[5])
        indirect = linked(dysym[14], dysym[15] * 4)
        imports = {}
        examined_slots = 0
        for segment, section in sections:
            start, size = section[2:4]
            slots = size // 8
            examined_slots += slots
            if examined_slots > 200000:
                raise LayoutError("inspection_budget")
            if (
                not segment[8] & 1
                or start % 8
                or size % 8
                or start < segment[3]
                or start - segment[3] > segment[4]
                or size > segment[4] - (start - segment[3])
            ):
                raise LayoutError("malformed_layout")
            if section[9] > dysym[15] or slots > dysym[15] - section[9]:
                raise LayoutError("malformed_layout")
            for i in range(slots):
                (index,) = _unpack("<I", indirect, 4 * (section[9] + i))
                if index & 0xC0000000:
                    continue
                if index >= sym[3]:
                    raise LayoutError("malformed_layout")
                (strindex,) = _unpack("<I", symbols, index * 16)
                if strindex >= len(strings):
                    raise LayoutError("malformed_layout")
                stop = strings.find(b"\0", strindex, min(strindex + 4096, len(strings)))
                if stop < 0:
                    raise LayoutError(
                        "inspection_budget"
                        if len(strings) - strindex > 4096
                        else "malformed_layout"
                    )
                name = APIS.get(strings[strindex:stop])
                if name:
                    imports[name] = imports.get(name, 0) + 1
        result.update(
            state="supported",
            imports=imports,
            layout="chained-retained-imports" if chain else "classic-indirect",
        )
    except (LayoutError, struct.error) as error:
        result["reason"] = (
            str(error) if isinstance(error, LayoutError) else "malformed_layout"
        )
        result["state"] = (
            "malformed" if result["reason"] == "malformed_layout" else "unsupported"
        )
    return result


def inspect_ipa(path):
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive entries")
        plists = [
            n for n in names if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)
        ]
        if len(plists) != 1 or archive.getinfo(plists[0]).file_size > 1024 * 1024:
            raise ValueError("one bounded application Info.plist required")
        info = _plist(archive.read(plists[0]))
        root = plists[0][:-10]
        executable = info.get("CFBundleExecutable", "")
        if (
            not isinstance(executable, str)
            or not executable
            or "/" in executable
            or "\\" in executable
            or executable in (".", "..")
        ):
            raise ValueError("invalid main executable")
        main = root + executable
        candidates = [main]
        declared, inventory_complete = set(), True
        framework_plists = [
            name
            for name in names
            if name.startswith(root + "Frameworks/")
            and name.endswith(".framework/Info.plist")
        ]
        if len(framework_plists) > 128:
            inventory_complete = False
        for name in framework_plists[:128]:
            if archive.getinfo(name).file_size > 64 * 1024:
                inventory_complete = False
                continue
            try:
                framework = _plist(archive.read(name))
                binary = framework.get("CFBundleExecutable")
                if (
                    not isinstance(binary, str)
                    or not binary
                    or "/" in binary
                    or "\\" in binary
                    or "\0" in binary
                    or binary in (".", "..")
                ):
                    inventory_complete = False
                    continue
                declared.add(name[:-10] + binary)
            except (
                ValueError,
                TypeError,
                AttributeError,
                plistlib.InvalidFileException,
            ):
                inventory_complete = False
        for name in names:
            if name.startswith(root + "Frameworks/") and not name.endswith("/"):
                relative = name[len(root) :]
                parent, basename = relative.rsplit("/", 1)
                standard = (
                    parent.endswith(".framework")
                    and basename == parent.rsplit("/", 1)[-1][:-10]
                )
                if relative.endswith(".dylib") or name in declared or standard:
                    candidates.append(name)
        candidates.extend(sorted(declared - set(candidates)))
        unity = any("/UnityFramework.framework/" in n for n in candidates)
        metadata = any(
            n.startswith(root)
            and n.endswith("/Data/Managed/Metadata/global-metadata.dat")
            for n in names
        )
        mono = any(
            n.startswith(root + "Data/Managed/") and n.endswith(".dll") for n in names
        )
        engine = (
            "unity-il2cpp"
            if metadata
            else "unity-mono"
            if unity and mono
            else "unity-unknown"
            if unity
            else "unknown"
        )
        images, total, complete = [], 0, inventory_complete
        for name in candidates[:64]:
            relative = "@executable" if name == main else name[len(root) :]
            if name not in names:
                images.append(
                    {
                        "image": relative,
                        "state": "unsupported",
                        "reason": "missing_image",
                        "imports": {},
                    }
                )
                continue
            size = archive.getinfo(name).file_size
            if size > MAX_IMAGE or total + size > MAX_TOTAL:
                complete = False
                images.append(
                    {
                        "image": relative,
                        "state": "unsupported",
                        "reason": "inspection_budget",
                        "imports": {},
                    }
                )
                continue
            total += size
            result = inspect_macho(archive.read(name))
            complete &= result["reason"] != "inspection_budget"
            images.append(
                {
                    "image": relative,
                    "image_id": hashlib.sha256(
                        ("images:" + relative).encode()
                    ).hexdigest()[:16],
                    **result,
                }
            )
        complete &= len(candidates) <= 64
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {
        "schema_version": 1,
        "artifact_sha256": digest,
        "engine": engine,
        "app_version": info.get("CFBundleShortVersionString"),
        "complete": complete,
        "images": images,
        "runtime_verified": False,
    }


def observation_profile(report):
    if not report["engine"].startswith("unity") or not report["complete"]:
        raise ValueError("a complete Unity preflight is required")
    images = [
        image["image"] for image in report["images"] if image["state"] == "supported"
    ]
    if not images:
        raise ValueError("no images with supported import metadata")
    if not isinstance(report.get("app_version"), str):
        raise TypeError("application version unavailable")
    return {
        "schema_version": 1,
        "game_version": report["app_version"].split("-mod", 1)[0],
        "mode": "observe",
        "images": images,
        "adapters": ["filesystem", "foundation", "urls", "environment", "images"],
        "paths": [
            "/Applications/Cydia.app",
            "/Applications/Sileo.app",
            "/private/jailbreak.txt",
            "/var/jb",
        ],
        "url_schemes": ["cydia", "sileo", "zbra", "filza"],
        "environment_names": [
            "DYLD_INSERT_LIBRARIES",
            "DYLD_FRAMEWORK_PATH",
            "DYLD_LIBRARY_PATH",
        ],
    }


def profile_problems(report, profile):
    import detection_profile

    if not isinstance(report.get("app_version"), str):
        return ["application version unavailable"]
    errors = detection_profile.problems(
        profile, report["app_version"].split("-mod", 1)[0]
    )
    if errors:
        return errors
    images = {image["image"]: image for image in report["images"]}
    for name in profile.get("images", []):
        if name not in images or images[name]["state"] != "supported":
            errors.append(
                "selected image has no verified static import coverage: " + name
            )
    if not report["complete"]:
        errors.append("inspection budget exceeded")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ipa")
    parser.add_argument(
        "--profile", help="write a new observation profile; refuses overwrites"
    )
    parser.add_argument(
        "--check-profile", help="validate an existing profile against this package"
    )
    parser.add_argument(
        "--output", help="write the private preflight JSON instead of stdout"
    )
    args = parser.parse_args(argv)
    try:
        if args.output:
            output = Path(args.output)
            for name in (args.ipa, args.check_profile, args.profile):
                if name:
                    source = Path(name)
                    if output.resolve() == source.resolve() or (
                        output.exists() and source.exists() and output.samefile(source)
                    ):
                        raise ValueError(
                            "report output must not replace an IPA or profile"
                        )
        report = inspect_ipa(args.ipa)
        if args.check_profile:
            errors = profile_problems(
                report, json.loads(Path(args.check_profile).read_text(encoding="utf-8"))
            )
            if errors:
                raise ValueError("; ".join(errors))
            supported = sum(image["state"] == "supported" for image in report["images"])
            print(
                f"preflight: {supported}/{len(report['images'])} images have supported static metadata; device verification required",
                file=sys.stderr,
            )
        if args.profile:
            profile = observation_profile(report)
            if errors := profile_problems(report, profile):
                raise ValueError("; ".join(errors))
            with Path(args.profile).open("x", encoding="utf-8") as output:
                json.dump(profile, output, indent=2)
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except (
        ValueError,
        TypeError,
        OSError,
        KeyError,
        zipfile.BadZipFile,
        plistlib.InvalidFileException,
    ) as error:
        print("preflight: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
