"""Read dump.cs, the C# listing Il2CppDumper writes for a Unity IL2CPP game.

Il2CppDumper (https://github.com/Perfare/Il2CppDumper) takes the game's code
(Payload/*.app/Frameworks/UnityFramework.framework/UnityFramework) and its metadata
(Payload/*.app/Data/Managed/Metadata/global-metadata.dat) and writes dump.cs: a "// Image N: X.dll - T"
header per assembly, every type after a "// Namespace: X" comment, and every method with code after a
"// RVA: 0x... Offset: 0x... VA: 0x..." comment. methods() returns those methods with the names
scripts/intake_auto.py checks and the signature a Tweak.x hook needs.

Used by scripts/find_cheats.py and scripts/check_hooks.py.
"""

import re
from dataclasses import dataclass

IMAGE = re.compile(r"^// Image \d+: (.+?)(?:\.dll)? - (\d+)\s*$")
NAMESPACE = re.compile(r"^\s*// Namespace: ?(\S*)")
MODIFIERS = (
    r"(?:(?:public|private|protected|internal|static|sealed|abstract|readonly|partial|virtual"
    r"|override|extern|new|unsafe|async)\s+)*"
)
# the whole name: a nested type is "Outer.Inner", a generic one "Name<T, U>" (never cut at the dot,
# or the nested type's methods would be read as the outer class's)
TYPE = re.compile(r"^\s*" + MODIFIERS + r"(?:class|struct|interface|enum)\s+(\S+(?:<[^>]*>)?\S*)")
TYPE_INDEX = re.compile(r"// TypeDefIndex: (\d+)")
# only a method's own address line; attribute lines ("[Attr] // RVA: ...") carry addresses too
# "Slot: N" marks a method with a vtable slot (virtual, override or an interface method)
ADDRESS = re.compile(r"^\s*// RVA: (0x[0-9A-Fa-f]+) Offset: 0x[0-9A-Fa-f]+ VA: 0x[0-9A-Fa-f]+(?: Slot: (\d+))?")
SIGNATURE = re.compile(
    r"^" + MODIFIERS + r"([\w.]+(?:<[^>]*>)?(?:\[\])*)\s+([.\w]+(?:<[^>]*>)?)\s*\(([^)]*)\)"
)

# Methods Unity itself calls on scripts: through the method's pointer, so pointer hooks catch them.
UNITY_MESSAGES = {
    "Awake", "Start", "Update", "FixedUpdate", "LateUpdate", "OnEnable", "OnDisable", "OnDestroy", "OnGUI",
    "OnTriggerEnter", "OnTriggerStay", "OnTriggerExit", "OnTriggerEnter2D", "OnTriggerStay2D", "OnTriggerExit2D",
    "OnCollisionEnter", "OnCollisionStay", "OnCollisionExit", "OnCollisionEnter2D", "OnCollisionStay2D",
    "OnCollisionExit2D", "OnApplicationPause", "OnApplicationFocus",
}

INTEGERS = {"int", "long", "short", "byte", "sbyte", "uint", "ulong", "ushort"}
FLOATS = {"float", "double"}


@dataclass
class Method:
    assembly: str
    namespace: str
    class_name: str
    name: str
    return_type: str
    params: list  # parameter declarations, like "float amount"
    rva: str
    signature: str  # the declaration as dumped, without the "{ }"
    slot: int | None = None  # vtable slot, when the method has one

    @property
    def needs_jit(self):
        """True when only a code hook (JIT on) catches calls to it: no vtable slot and not a Unity message.
        Without JIT the runtime swaps pointers, which a direct call from other game code bypasses."""
        return self.slot is None and self.name not in UNITY_MESSAGES

    @property
    def args(self):
        return len(self.params)

    @property
    def key(self):
        """What hooks.json names a method by."""
        return (self.namespace, self.class_name, self.name)

    @property
    def full_name(self):
        return ".".join(p for p in (self.namespace, self.class_name, self.name) if p)

    @property
    def kind(self):
        """void, bool, int, float or other: what a replacement for the method's result can be."""
        if self.return_type == "void":
            return "void"
        if self.return_type == "bool":
            return "bool"
        if self.return_type in INTEGERS:
            return "int"
        if self.return_type in FLOATS:
            return "float"
        return "other"

    def hook(self):
        """The hooks.json entry for this method."""
        return {
            "assembly": self.assembly,
            "namespace": self.namespace,
            "class": self.class_name,
            "method": self.name,
            "args": self.args,
            "return_type": self.return_type,
            "signature": self.signature,
            "needs_jit": self.needs_jit,
        }


def split_params(text):
    """Parameter declarations of a "(...)" body, split at the commas outside < >."""
    params, depth, current = [], 0, ""
    for ch in text:
        if ch == "," and depth == 0:
            params.append(current.strip())
            current = ""
            continue
        depth += (ch == "<") - (ch == ">")
        current += ch
    if current.strip():
        params.append(current.strip())
    return params


def methods(path):
    """Every method with an address in dump.cs, in file order."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.read().split("\n")
    images = []  # (first TypeDefIndex, assembly), as the header lists them
    namespace, class_name, assembly = "", "", ""
    found = []
    for i, line in enumerate(lines):
        image = IMAGE.match(line)
        if image:
            images.append((int(image.group(2)), image.group(1)))
            continue
        ns = NAMESPACE.match(line)
        if ns:
            namespace = ns.group(1)
            continue
        typ = TYPE.match(line)
        if typ:
            class_name = typ.group(1)
            index = TYPE_INDEX.search(line)
            if index:
                start = int(index.group(1))
                assembly = next((n for s, n in sorted(images, reverse=True) if s <= start), "")
            continue
        address = ADDRESS.match(line)
        if not address:
            continue
        declaration = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            if ADDRESS.match(lines[j]):  # the next method's address: this one has no declaration
                break
            if lines[j].strip() and not lines[j].lstrip().startswith(("//", "[")):
                declaration = lines[j].strip()
                break
        sig = SIGNATURE.match(declaration)
        if not sig:
            continue
        return_type, name, params = sig.groups()
        found.append(
            Method(
                assembly=assembly,
                namespace=namespace,
                class_name=class_name,
                name=name,
                return_type=return_type,
                params=split_params(params),
                rva=address.group(1),
                signature=declaration.split("{")[0].rstrip().rstrip(";"),
                slot=int(address.group(2)) if address.group(2) else None,
            )
        )
    return found
