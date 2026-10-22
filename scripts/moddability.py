"""Is a game worth modding with our tools? A quick report from its decrypted .ipa (and its dump, if made).

From the .ipa alone: decrypted or not, the engine (our tools need Unity IL2CPP: UnityFramework.framework plus
Data/Managed/Metadata/global-metadata.dat), the metadata version, and which network, multiplayer, ads and
in-app purchase code it ships (framework names and strings in the metadata). With a dump, the scan's counts
(scripts/dump_scan.py): how many no-JIT candidates, how many flagged. Then a verdict: good, limited, or not
supported. Names and strings only: an online game can still have single-player parts, and the other way round.
"""

import re
import struct
import zipfile

# what each marker in a framework name or the metadata strings means
MARKERS = {
    "multiplayer": [b"Photon", b"PhotonNetwork", b"Mirror.NetworkBehaviour", b"Unity.Netcode", b"Nakama",
                    b"FishNet", b"Colyseus", b"Fusion.NetworkRunner"],
    "online services": [b"PlayFab", b"Firebase.Database", b"Firebase.Firestore", b"GameSparks", b"Unity.Services.CloudSave",
                        b"Unity.Services.Authentication", b"GooglePlayGames"],
    "ads": [b"GoogleMobileAds", b"AppLovin", b"IronSource", b"UnityEngine.Advertisements", b"Yodo1", b"AdColony",
            b"Vungle", b"Chartboost", b"Adinmo"],
    "in-app purchases": [b"UnityEngine.Purchasing", b"SKPaymentQueue", b"RevenueCat"],
}


def _main_binary(z, app):
    info_name = f"{app}/Info.plist"
    import plistlib

    info = plistlib.loads(z.read(info_name))
    return info, f"{app}/{info.get('CFBundleExecutable', '')}"


def _encrypted(binary):
    for cmd, size in ((0x21, 20), (0x2C, 24)):
        for m in re.finditer(re.escape(struct.pack("<II", cmd, size)), binary):
            if struct.unpack_from("<I", binary, m.start() + 16)[0] == 1:
                return True
    return False


def inspect_ipa(path):
    """Facts from the .ipa: {"bundle", "version", "decrypted", "engine", "metadata_version", "markers", "size"}."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        apps = sorted({n.split("/")[1] for n in names if n.startswith("Payload/") and n.count("/") >= 2 and n.split("/")[1].endswith(".app")})
        if not apps:
            raise ValueError(f"{path} has no Payload/*.app")
        app = f"Payload/{apps[0]}"
        info, main = _main_binary(z, app)
        decrypted = not _encrypted(z.read(main)) if main in names else None
        unity = any(n.startswith(f"{app}/Frameworks/UnityFramework.framework/") for n in names)
        meta_name = f"{app}/Data/Managed/Metadata/global-metadata.dat"
        il2cpp = meta_name in names
        mono = any(n.startswith(f"{app}/Data/Managed/") and n.endswith(".dll") for n in names)
        unreal = any("UE4" in n or "UnrealEngine" in n or n.endswith(".pak") for n in names)
        engine = ("Unity IL2CPP" if unity and il2cpp else "Unity Mono" if unity and mono else "Unity (unknown backend)"
                  if unity else "Unreal" if unreal else "native or another engine")
        meta_version, blob = None, b""
        if il2cpp:
            blob = z.read(meta_name)
            if len(blob) >= 8 and struct.unpack_from("<I", blob, 0)[0] == 0xFAB11BAF:
                meta_version = struct.unpack_from("<i", blob, 4)[0]
        frameworks = " ".join(n for n in names if "/Frameworks/" in n and n.endswith("/Info.plist")).encode()
        markers = {}
        for kind, needles in MARKERS.items():
            found = sorted({n.decode() for n in needles if n in blob or n in frameworks})
            if found:
                markers[kind] = found
        size = sum(i.file_size for i in z.infolist())
    return {
        "bundle": info.get("CFBundleIdentifier"),
        "name": info.get("CFBundleDisplayName") or info.get("CFBundleName"),
        "version": info.get("CFBundleShortVersionString"),
        "decrypted": decrypted,
        "engine": engine,
        "metadata_version": meta_version,
        "markers": markers,
        "size": size,
    }


def verdict(facts, scan=None):
    """(level, lines): level is "good", "limited" or "not supported"."""
    lines = []
    if facts["decrypted"] is False:
        return "not supported", ["The main binary is still FairPlay-encrypted: get a decrypted copy (am fetch)."]
    if facts["engine"] != "Unity IL2CPP":
        return "not supported", [f"Engine: {facts['engine']}. Our dump, scan, pins and hooks need Unity IL2CPP."]
    level = "good"
    m = facts["markers"]
    if "multiplayer" in m:
        lines.append(f"Multiplayer code ({', '.join(m['multiplayer'])}): informational marker; "
                     "check which state is local and which is controlled by the server.")
    if "online services" in m:
        lines.append(f"Online services ({', '.join(m['online services'])}): values the server keeps can't be "
                     "changed on the phone, and cloud saves may undo local changes.")
    if "in-app purchases" in m or "ads" in m:
        lines.append("Purchases or ads detected: scan labels are informational; these candidates remain available.")
    if scan is not None:
        n, f = len(scan["rows"]), len(scan["flagged"])
        strong = sum(1 for r in scan["rows"] if r.get("score", 0) >= 6 and not r["has"])
        lines.append(f"Scan: {n} no-JIT candidates in {scan['classes']} game classes, {strong} strong, {f} flagged.")
        if strong < 5:
            level = "limited"
            lines.append("Few strong candidates: little game state sits on singletons; switches will need more digging.")
    else:
        lines.append("No dump yet: am new <name> <ipa> makes one, then this report adds the candidate counts.")
    if level == "good":
        lines.insert(0, "Unity IL2CPP and decrypted: our whole toolchain applies.")
    return level, lines


def report_text(facts, scan=None):
    level, lines = verdict(facts, scan)
    head = [
        f"{facts['name']} {facts['version']} ({facts['bundle']}), {facts['size'] / 1e6:.0f} MB unpacked",
        f"  engine: {facts['engine']}" + (f", metadata v{facts['metadata_version']}" if facts["metadata_version"] else ""),
        f"  decrypted: {'yes' if facts['decrypted'] else 'no' if facts['decrypted'] is False else 'unknown'}",
    ]
    for kind, found in facts["markers"].items():
        head.append(f"  {kind}: {', '.join(found)}")
    return "\n".join(head + [f"verdict: {level.upper()}"] + [f"  - {l}" for l in lines])
