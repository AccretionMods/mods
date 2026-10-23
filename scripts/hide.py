"""Encode a name the way runtime/am_hide.h decodes it (XOR with 0xA7 + 31 * i, a byte at a time)."""

KEY = 0xA7


def encode(text):
    return [b ^ ((KEY + 31 * i) & 0xFF) for i, b in enumerate(text.encode("utf-8"))]


def ams_bytes(text):
    """AMS_BYTES(n, 0x.., ...): a C expression giving the decoded const char *."""
    data = encode(text)
    return f"AMS_BYTES({len(data)}, {', '.join(f'0x{b:02x}' for b in data) or '0'})"


def c_bytes(text):
    """{0x.., ..., 0}: an encoded, 0-terminated char array initializer (decoded in place at run time)."""
    data = encode(text)
    return "{" + ", ".join([f"0x{b:02x}" for b in data] + ["0"]) + "}"
