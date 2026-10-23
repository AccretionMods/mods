"""Write a copy of a hand-written Tweak.x with every AMS("Name") encoded (runtime/am_hide.h).

Usage: python3 scripts/hide_strings.py <Tweak.x> <out>

scripts/build.sh compiles the copy (Tweak.hidden.x, git-ignored) instead of the source, so the source keeps
plain names and the .dylib doesn't.
"""

import re
import sys

from hide import ams_bytes

AMS = re.compile(r'AMS\("((?:[^"\\]|\\.)*)"\)')


def hide(source):
    def one(m):
        text = m.group(1)
        if "\\" in text:
            text = bytes(text, "utf-8").decode("unicode_escape")
        return ams_bytes(text)
    return AMS.sub(one, source)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.exit(__doc__)
    with open(argv[0], encoding="utf-8") as f:
        source = f.read()
    out = hide(source)
    with open(argv[1], "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    print(f"{len(AMS.findall(source))} names hidden -> {argv[1]}", file=sys.stderr)


if __name__ == "__main__":
    main()
