"""Versioned identities of file-backed executable sections, independent of signing."""
import hashlib
import struct

ALGORITHM = "sha256-executable-sections-v1"
DOMAIN = b"AMExecutableRangesV1\0"
MAX_RANGES = 128
MAX_IMAGE = 512 * 1024 * 1024


def executable_identity(macho: bytes) -> dict:
    from detection_preflight import _thin, _range, _unpack, LayoutError

    try:
        if len(macho) > MAX_IMAGE:
            raise ValueError("executable file exceeds identity budget")
        data = _thin(macho)
        header = _unpack("<8I", data)
        if (len(data) > MAX_IMAGE or header[0] != 0xFEEDFACF or
                header[1] != 0x100000C or header[2] & 0xFFFFFF == 2):
            raise ValueError("unsupported executable architecture or size")
        if header[4] > 4096 or header[5] > 16 * 1024 * 1024:
            raise ValueError("executable identity inspection budget exceeded")
        _range(data, 32, header[5])
        cursor, end = 32, 32 + header[5]
        ranges, signatures, uuid = [], [], None
        for _ in range(header[4]):
            cmd, size = _unpack("<II", data, cursor)
            if size < 8 or size % 8 or size > end - cursor:
                raise ValueError("invalid executable load command")
            command = _range(data, cursor, size)
            if cmd == 25:
                segment = _unpack("<II16s4Q4I", command)
                if (segment[9] > (size - 72) // 80 or segment[6] > segment[4] or
                        segment[3] + segment[4] > 0xFFFFFFFFFFFFFFFF):
                    raise ValueError("invalid executable segment")
                _range(data, segment[5], segment[6])
                for i in range(segment[9]):
                    section = _unpack("<16s16s2Q8I", command, 72 + i * 80)
                    if not segment[8] & 4 or section[8] & 255 in (1, 12, 18) or not section[3]:
                        continue
                    addr, length, offset = section[2:5]
                    if (addr < segment[3] or addr - segment[3] > segment[4] or
                            length > segment[4] - (addr - segment[3]) or
                            offset < segment[5] or offset - segment[5] > segment[6] or
                            length > segment[6] - (offset - segment[5])):
                        raise ValueError("executable section outside segment")
                    _range(data, offset, length)
                    ranges.append({"offset": offset, "size": length})
                    if len(ranges) > MAX_RANGES:
                        raise ValueError("too many executable ranges")
            elif cmd == 0x1B:
                if size != 24 or uuid is not None:
                    raise ValueError("invalid executable UUID")
                uuid = command[8:24].hex()
            elif cmd == 0x1D:
                if size != 16 or signatures:
                    raise ValueError("invalid signature command")
                _, _, offset, length = _unpack("<4I", command)
                _range(data, offset, length)
                signatures.append((offset, offset + length))
            elif cmd in (0x21, 0x2C) and _unpack("<5I", command)[4]:
                raise ValueError("encrypted executable identity unavailable")
            cursor += size
        if cursor != end or uuid is None or not ranges:
            raise ValueError("executable UUID or code sections unavailable")
        ranges.sort(key=lambda r: r["offset"])
        previous_end = 0
        digest = hashlib.sha256(DOMAIN + struct.pack("<I", len(ranges)))
        for r in ranges:
            offset, length = r["offset"], r["size"]
            if offset < end or offset < previous_end or any(offset < b and a < offset + length for a, b in signatures):
                raise ValueError("overlapping executable or signature ranges")
            previous_end = offset + length
            digest.update(struct.pack("<QQ", offset, length))
            digest.update(_range(data, offset, length))
        return {"algorithm": ALGORITHM, "uuid": uuid, "sha256": digest.hexdigest(), "ranges": ranges}
    except (LayoutError, struct.error) as error:
        raise ValueError("malformed executable identity layout") from error


def identity_problems(identity):
    import re

    if not isinstance(identity, dict) or set(identity) != {"algorithm", "uuid", "sha256", "ranges"}:
        return ["invalid image fingerprint fields"]
    if (identity["algorithm"] != ALGORITHM or not isinstance(identity["uuid"], str) or
            not re.fullmatch(r"[0-9a-f]{32}", identity["uuid"]) or
            not isinstance(identity["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", identity["sha256"])):
        return ["invalid image fingerprint algorithm, UUID or digest"]
    ranges = identity["ranges"]
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= MAX_RANGES:
        return ["invalid image fingerprint range count"]
    end = 0
    for r in ranges:
        if (not isinstance(r, dict) or set(r) != {"offset", "size"} or
                type(r["offset"]) is not int or type(r["size"]) is not int or
                r["offset"] < end or r["size"] <= 0 or r["offset"] > MAX_IMAGE - r["size"]):
            return ["invalid or overlapping image fingerprint ranges"]
        end = r["offset"] + r["size"]
    return []
