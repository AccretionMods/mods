"""Which engine an App Store game uses, from its .ipa's file list, without downloading the game.

The App Store serves an app's .ipa as a zip whose file list sits at the end; two range requests read it (the
end-of-zip record, then the central directory: a few hundred KB to a few MB), and the names say the engine:
Frameworks/UnityFramework.framework plus Data/Managed/Metadata/global-metadata.dat is Unity IL2CPP, which is
what our mods support. The game's code stays encrypted and is never downloaded.

Getting the download address needs the owner's Apple ID. Apple's sign-in accepts only SAP-signed requests
(2026), so the Apple calls go through tools/appstore-helper (ipatool's library, built by the Mods: App Store
helper workflow; install_helper() fetches it into AM_ROOT/tools/appstore-helper/):
- login(email, password, code): the password (and the two-factor code) go to the helper on stdin, then to Apple,
  and are never saved; Apple's session token is kept in AM_ROOT/studio/appstore.json, encrypted with Windows' data
  protection (this user on this PC only), with the helper's cookies beside it;
- a game the account never had needs a license first. For a free game that is get_license(), which adds it to
  the account's purchases like tapping Get; Studio asks the owner before every one. Paid games are never bought.

check(app_id, bundle) saves each verdict in AM_ROOT/studio/engines.json (per bundle and version), which the
Games tab shows as a badge and filters on.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "Configurator/2.17 (Macintosh; OS X 15.2; 24C101) AppleWebKit/0620.1.16.11.6"  # range requests
TAIL = 1 << 16  # the end-of-zip record and its comment fit in the last 64 KB
HELPER_REPO = "AccretionMods/mods"
HELPER_WORKFLOW = "appstore-helper.yml"
HELPER_ARTIFACT = "appstore-helper-windows-amd64"


class AppStoreError(Exception):
    pass


class NeedsLicense(AppStoreError):
    """The account doesn't have this app: a free license (get_license) comes first."""


def state_dir():
    root = os.environ.get("AM_ROOT") or ("C:/AccretionMods" if sys.platform == "win32" else str(Path.home() / "AccretionMods"))
    path = Path(root) / "studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------ the token, protected for this Windows user

def _protect(data, unprotect=False):
    if sys.platform != "win32":
        return data  # elsewhere the file's own permissions protect it (state dir is the owner's)
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if unprotect else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise AppStoreError("Windows could not protect the App Store session (sign in again)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _session_path():
    return state_dir() / "appstore.json"


def session():
    """The saved sign-in, or None: {email, name, dsid, storefront, token}."""
    try:
        data = json.loads(_session_path().read_text(encoding="utf-8"))
        data["token"] = _protect(base64.b64decode(data.pop("token_protected")), unprotect=True).decode()
        return data
    except (OSError, ValueError, KeyError, AppStoreError):
        return None


def account():
    """What the page shows: signed in or not, and the email half-hidden."""
    s = session()
    if not s:
        return {"signed_in": False}
    user, _, host = s["email"].partition("@")
    return {"signed_in": True, "email": f"{user[:1]}•••@{host}", "name": s.get("name", ""),
            "storefront": s.get("storefront", "")}


def logout():
    for name in ("appstore.json", "appstore.cookies", "appstore-helper.cookies"):
        try:
            (state_dir() / name).unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ Apple's store calls, through the helper

def helper_path():
    root = state_dir().parent
    return root / "tools" / "appstore-helper" / ("appstore-helper.exe" if sys.platform == "win32" else "appstore-helper")


def install_helper(run_id=None):
    """The helper .exe from the newest successful Mods: App Store helper run (or run_id), with its sha256 checked.
    Needs `gh` signed in; the .exe is built in GitHub Actions from tools/appstore-helper, never on this PC."""
    folder = helper_path().parent
    if run_id is None:
        out = subprocess.run(["gh", "run", "list", "-R", HELPER_REPO, "--workflow", HELPER_WORKFLOW, "--status",
                              "success", "--limit", "1", "--json", "databaseId", "-q", ".[0].databaseId"],
                             capture_output=True, text=True, check=False)
        run_id = out.stdout.strip()
        if not run_id:
            raise AppStoreError(f"no successful {HELPER_WORKFLOW} run yet ({out.stderr.strip()[-200:]})")
    tmp = folder.with_name("appstore-helper.new")
    shutil.rmtree(tmp, ignore_errors=True)
    r = subprocess.run(["gh", "run", "download", str(run_id), "-R", HELPER_REPO, "-n", HELPER_ARTIFACT, "-D", str(tmp)],
                       capture_output=True, text=True, check=False)
    exe = tmp / "appstore-helper.exe"
    if r.returncode != 0 or not exe.is_file():
        raise AppStoreError(f"could not download the helper from run {run_id}: {(r.stderr or r.stdout).strip()[-300:]}")
    want = (tmp / "appstore-helper.exe.sha256").read_text(encoding="utf-8").split()[0]
    if hashlib.sha256(exe.read_bytes()).hexdigest() != want:
        shutil.rmtree(tmp, ignore_errors=True)
        raise AppStoreError("the downloaded helper doesn't match its sha256: not installed")
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        # Windows can keep a helper .exe that ran a moment ago unremovable (still mapped, or being scanned); it
        # can still be renamed, so the old one steps aside and the new files go in next to it
        for old in folder.glob("appstore-helper*.exe"):
            if old.name != "appstore-helper.exe":
                continue
            old.rename(old.with_name(f"appstore-helper.old-{int(time.time())}.exe"))
        for item in tmp.iterdir():
            os.replace(item, folder / item.name)
        shutil.rmtree(tmp, ignore_errors=True)
        for stale in folder.glob("appstore-helper.old-*.exe"):
            try:
                stale.unlink()  # gone once Windows lets go of it; a later install tries again
            except OSError:
                pass
    else:
        tmp.rename(folder)
    if hashlib.sha256(helper_path().read_bytes()).hexdigest() != want:
        raise AppStoreError("the installed helper doesn't match the downloaded one")
    return {"path": str(helper_path()), "run": str(run_id), "sha256": want}


def _helper(request, timeout=300):
    """One request to the helper (JSON on stdin, so the password is never on a command line); its JSON answer."""
    exe = helper_path()
    if not exe.is_file():
        raise AppStoreError("the App Store helper isn't installed yet (python scripts/appstore.py install-helper)")
    # kbsync: the blob the store's downloads send, kept between runs (making one takes the emulator ~40 s)
    request = {**request, "cookies": str(state_dir() / "appstore-helper.cookies"),
               "kbsync": str(state_dir() / "appstore-helper.kbsync")}
    try:
        r = subprocess.run([str(exe)], input=json.dumps(request), capture_output=True, text=True, timeout=timeout,
                           check=False)
    except subprocess.TimeoutExpired as e:
        raise AppStoreError(f"the App Store didn't answer within {timeout // 60} minutes") from e
    try:
        answer = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        raise AppStoreError(f"the helper answered nothing readable ({(r.stderr or r.stdout).strip()[-300:]})") from e
    if "error" in answer:
        raise AppStoreError(answer["error"])
    return answer


def _account(s):
    """The saved sign-in in the shape ipatool's Account has (no password)."""
    return {"email": s["email"], "passwordToken": s["token"], "directoryServicesIdentifier": s["dsid"],
            "name": s.get("name", ""), "storeFront": s.get("storefront", ""), "pod": s.get("pod", "")}


def login(email, password, code=""):
    """Sign in. Returns account() when done, or {"needs_code": True} when Apple sent a two-factor code."""
    if not email or not password:
        raise AppStoreError("Apple ID and password, please")
    answer = _helper({"cmd": "login", "email": email, "password": password, "code": code or ""})
    if answer.get("needs_code"):
        return {"needs_code": True}
    acc = answer.get("account") or {}
    if not acc.get("passwordToken") or not acc.get("directoryServicesIdentifier"):
        raise AppStoreError("Apple accepted the sign-in but sent no session")
    data = {"email": acc.get("email") or email, "dsid": str(acc["directoryServicesIdentifier"]),
            "name": acc.get("name", ""), "storefront": acc.get("storeFront", ""), "pod": acc.get("pod", ""),
            "token_protected": base64.b64encode(_protect(acc["passwordToken"].encode())).decode()}
    _session_path().write_text(json.dumps(data), encoding="utf-8")
    return account()


def _signed():
    s = session()
    if not s:
        raise AppStoreError("sign in to the App Store first (Games tab: App Store sign-in)")
    return s


def download_url(app_id):
    """The .ipa's download address for an app the account has. NeedsLicense when it doesn't."""
    answer = _helper({"cmd": "url", "account": _account(_signed()), "app_id": int(app_id)})
    if answer.get("needs_license"):
        raise NeedsLicense("this Apple ID doesn't have this game yet")
    if answer.get("expired"):
        raise AppStoreError("the App Store sign-in expired: sign in again")
    if not answer.get("url"):
        raise AppStoreError("no download for this game")
    return {"url": answer["url"], "version": answer.get("version", ""), "bundle": answer.get("bundle", "")}


def get_license(app_id):
    """A free game into the account's purchases, like tapping Get. Only after the owner said yes (Studio asks).
    The helper refuses paid apps."""
    answer = _helper({"cmd": "license", "account": _account(_signed()), "app_id": int(app_id)})
    if answer.get("expired"):
        raise AppStoreError("the App Store sign-in expired: sign in again")
    return bool(answer.get("ok"))


# ------------------------------------------------------------------ the file list, by range requests

def _range(url, start, end, opener=None):
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT})
    with (opener or urllib.request.urlopen)(req, timeout=60) as r:
        total = r.headers.get("Content-Range", "").rpartition("/")[2]
        return r.read(), int(total) if total.isdigit() else None


def zip_names(read):
    """Names in a zip from read(start, end) -> (bytes, total size or None): its end, then its central directory."""
    _, size = read(0, 0)
    if not size:
        raise AppStoreError("the download server didn't say the file's size (no range support?)")
    start = max(0, size - TAIL)
    tail, _ = read(start, size - 1)
    at = tail.rfind(b"PK\x05\x06")
    if at < 0:
        raise AppStoreError("no end-of-zip record in the last 64 KB")
    entries, cd_size, cd_offset = struct.unpack_from("<10xHII", tail, at)
    z64 = tail.rfind(b"PK\x06\x07", 0, at)
    if z64 >= 0 and (cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or entries == 0xFFFF):
        (eocd64,) = struct.unpack_from("<8xQ", tail, z64)
        rec = tail[eocd64 - start:] if eocd64 >= start else read(eocd64, eocd64 + 55)[0]
        entries, cd_size, cd_offset = struct.unpack_from("<32xQQQ", rec)
    if cd_offset >= start:
        cd = tail[cd_offset - start:cd_offset - start + cd_size]
    else:
        cd, _ = read(cd_offset, cd_offset + cd_size - 1)
    names, pos = [], 0
    while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
        n, extra, comment = struct.unpack_from("<HHH", cd, pos + 28)
        names.append(cd[pos + 46:pos + 46 + n].decode("utf-8", "replace"))
        pos += 46 + n + extra + comment
    return names, size


ENGINES = [  # first match wins
    ("Unity IL2CPP", lambda n: any(x.endswith("UnityFramework.framework/UnityFramework") for x in n)
                               and any(x.endswith("global-metadata.dat") for x in n)),
    # Unity before 2019.3 builds the game into the main app with the IL2CPP functions unexported: dumpable, but the
    # runtime finds those functions by name, so no switch can run (Dustoff II 1.6.2, 2026-10-27)
    ("Unity IL2CPP, old (code in the main app)", lambda n: any(x.endswith("global-metadata.dat") for x in n)),
    ("Unity (no IL2CPP metadata)", lambda n: any("UnityFramework.framework/" in x for x in n)),
    # Unreal's own markers only: a bare case-blind "ue4" also matched Minecraft's "become_statue4.fsb"
    ("Unreal", lambda n: any(re.search(r"(?:^|/)UE[45]|UnrealGame|(?i:cookeddata)|/Paks/[^/]+\.pak$", x) for x in n)),
    ("Godot", lambda n: any(x.endswith(".pck") or "godot" in x.lower() for x in n)),
    ("Cocos", lambda n: any("cocos" in x.lower() for x in n)),
    ("Flutter", lambda n: any("Flutter.framework/" in x for x in n)),
    ("React Native", lambda n: any(x.endswith("main.jsbundle") for x in n)),
]


def engine_of(names):
    """(engine, compatible): compatible means our IL2CPP runtime can mod it."""
    for name, test in ENGINES:
        if test(names):
            return name, name == "Unity IL2CPP"
    return "Native or other", False


# ------------------------------------------------------------------ verdicts, remembered

def _engines_path():
    return state_dir() / "engines.json"


def known():
    """{bundle: {engine, compatible, version, checked, files, size}} from earlier checks."""
    try:
        return json.loads(_engines_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(bundle, verdict):
    data = known()
    data[bundle] = verdict
    tmp = _engines_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, _engines_path())


def check(app_id, bundle, license_ok=False):
    """The engine of one game, remembered. NeedsLicense when the account lacks it and license_ok is False."""
    try:
        where = download_url(app_id)
    except NeedsLicense:
        if not license_ok:
            raise
        get_license(app_id)
        where = download_url(app_id)
    names, size = zip_names(lambda a, b: _range(where["url"], a, b))
    engine, ok = engine_of(names)
    verdict = {"engine": engine, "compatible": ok, "version": where["version"], "files": len(names),
               "size": size, "checked": time.strftime("%Y-%m-%d"),
               "hot_update": any(re.search(r"hybridclr|hotfix|hotupdate", x, re.IGNORECASE) for x in names)}
    remember(bundle or where["bundle"], verdict)
    return verdict


# ------------------------------------------------------------------ keeping the .ipa (Studio's Keep the .ipa choice)
# An App Store .ipa stays encrypted to the Apple ID that got it: a backup or an older version for that account's
# phones, never a mod base (the mod tools need cryptid 0), so kept copies live in AM_ROOT/appstore/, apart from intake.

KEEP_CHOICES = ("none", "pc", "servers")


def _prefs_path():
    return state_dir() / "appstore-prefs.json"


def keep_choice():
    """Where a kept .ipa goes by default: none (only check the engine), pc, or servers."""
    try:
        choice = json.loads(_prefs_path().read_text(encoding="utf-8")).get("keep", "none")
    except (OSError, ValueError):
        choice = "none"
    return choice if choice in KEEP_CHOICES else "none"


def set_keep_choice(choice):
    if choice not in KEEP_CHOICES:
        raise AppStoreError(f"keep: one of {', '.join(KEEP_CHOICES)}")
    _prefs_path().write_text(json.dumps({"keep": choice}), encoding="utf-8")
    return choice


def kept_dir():
    path = state_dir().parent / "appstore"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _kept_index():
    return kept_dir() / "kept.json"


def kept():
    """Kept copies, newest first: {file, bundle, version, version_id, size, sha256, where, kept}."""
    try:
        rows = json.loads(_kept_index().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    return [r for r in rows if (kept_dir() / r.get("file", "")).is_file() or r.get("where") == "servers"]


def _save_kept(rows):
    tmp = _kept_index().with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    os.replace(tmp, _kept_index())


def versions(app_id):
    """The store's version ids for an app the account has (oldest first) and the newest one."""
    answer = _helper({"cmd": "versions", "account": _account(_signed()), "app_id": int(app_id)})
    if answer.get("needs_license"):
        raise NeedsLicense("this Apple ID doesn't have this game yet")
    if answer.get("expired"):
        raise AppStoreError("the App Store sign-in expired: sign in again")
    return {"ids": [str(i) for i in answer.get("ids") or []], "latest": str(answer.get("latest") or "")}


def version_info(app_id, version_id):
    """The version number and release date behind one version id."""
    if not re.fullmatch(r"\d{1,15}", str(version_id or "")):
        raise AppStoreError("version: a version id from versions()")
    answer = _helper({"cmd": "version_info", "account": _account(_signed()), "app_id": int(app_id),
                      "version_id": str(version_id)})
    if answer.get("expired"):
        raise AppStoreError("the App Store sign-in expired: sign in again")
    return {"id": str(version_id), "version": answer.get("version", ""), "released": answer.get("released", "")}


def keep(app_id, bundle="", version_id="", where=None):
    """Downloads the whole .ipa (newest, or version_id) and keeps it where the choice says. NeedsLicense when the
    account lacks the game (Studio asks before any free license). "servers" is not built yet."""
    where = where or keep_choice()
    if where == "none":
        raise AppStoreError("Keep the .ipa is off: choose This PC (or Our servers) first")
    if where == "servers":
        raise AppStoreError("Our servers: the private upload for kept copies isn't built yet; keep it on This PC")
    if version_id and not re.fullmatch(r"\d{1,15}", str(version_id)):
        raise AppStoreError("version: a version id from versions()")
    tmp = kept_dir() / ".downloading"
    shutil.rmtree(tmp, ignore_errors=True)
    answer = _helper({"cmd": "download", "account": _account(_signed()), "app_id": int(app_id),
                      "version_id": str(version_id or ""), "output": str(tmp)}, timeout=3600)
    if answer.get("needs_license"):
        shutil.rmtree(tmp, ignore_errors=True)
        raise NeedsLicense("this Apple ID doesn't have this game yet")
    if answer.get("expired"):
        shutil.rmtree(tmp, ignore_errors=True)
        raise AppStoreError("the App Store sign-in expired: sign in again")
    got = Path(answer.get("path") or "")
    if not got.is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise AppStoreError("the download left no .ipa")
    bundle = bundle or answer.get("bundle") or "app"
    version = answer.get("version") or "unknown"
    if version_id:
        version = version_info(app_id, version_id).get("version") or version
    name = re.sub(r"[^\w.-]", "_", f"{bundle}-{version}" + (f"-{version_id}" if version_id else "")) + ".ipa"
    final = kept_dir() / name
    os.replace(got, final)
    shutil.rmtree(tmp, ignore_errors=True)
    digest = hashlib.sha256()
    with final.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    row = {"file": name, "bundle": bundle, "version": version, "version_id": str(version_id or ""),
           "app_id": int(app_id), "size": final.stat().st_size, "sha256": digest.hexdigest(), "where": "pc",
           "kept": time.strftime("%Y-%m-%d %H:%M")}
    _save_kept([row] + [r for r in kept() if r.get("file") != name])
    return row


def forget(name):
    """Deletes one kept copy from this PC (Studio asks first)."""
    rows = kept()
    row = next((r for r in rows if r.get("file") == name), None)
    if not row or "/" in name or "\\" in name:
        raise AppStoreError("no kept copy by that name")
    path = kept_dir() / name
    if path.is_file():
        path.unlink()
    _save_kept([r for r in rows if r.get("file") != name])
    return {"forgot": name}


# ------------------------------------------------------------------ regions: where an app is sold (public, no account)

# The App Store's storefront countries (ISO codes Apple's lookup takes); one a lookup refuses counts as "no answer".
STOREFRONTS = (  # noqa: SIM905 - one readable block of codes
    "ae ag ai al am ao ar at au az bb be bf bg bh bj bm bn bo br bs bt bw by bz ca cg ch cl cn co cr cv cy cz de dk "
    "dm do dz ec ee eg es fi fj fm fr gb gd gh gm gr gt gw gy hk hn hr hu id ie il in is it jm jo jp ke kg kh kn kr "
    "kw ky kz la lb lc lk lr lt lu lv md mg mk ml mn mo mr ms mt mu mw mx my mz na ne ng ni nl no np nz om pa pe pg "
    "ph pk pl pt pw py qa ro ru sa sb sc se sg si sk sl sn sr st sv sz tc td th tj tm tn tr tt tw tz ua ug us uy uz "
    "vc ve vg vn ye za zw"
).split()
REGIONS_TTL = 24 * 3600


def _region_lookup(bundle, country):
    q = urllib.parse.urlencode({"bundleId": bundle, "country": country, "entity": "software"})
    req = urllib.request.Request(f"https://itunes.apple.com/lookup?{q}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            found = [x for x in json.loads(r.read()).get("results", []) if x.get("kind") == "software"]
    except (OSError, ValueError):
        return country, None
    if not found:
        return country, {}
    x = found[0]
    return country, {"name": x.get("trackName", ""), "version": x.get("version", ""), "price": x.get("price", 0),
                     "currency": x.get("currency", ""), "id": x.get("trackId"), "url": x.get("trackViewUrl", "")}


def regions(bundle, refresh=False):
    """Where an app is on the App Store, from Apple's public lookup in every storefront (cached a day):
    {bundle, checked, sold: {country: {name, version, price, currency, id, url}}, missing: [...], unknown: [...]}."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{1,150}", bundle or ""):
        raise AppStoreError("regions: an app's bundle id")
    cache = state_dir() / "regions" / f"{bundle}.json"
    if not refresh:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - data.get("at", 0) < REGIONS_TTL:
                return data
        except (OSError, ValueError):
            pass
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=12) as pool:
        answers = dict(pool.map(lambda c: _region_lookup(bundle, c), STOREFRONTS))
    data = {"bundle": bundle, "at": time.time(), "checked": time.strftime("%Y-%m-%d %H:%M"),
            "sold": {c: a for c, a in sorted(answers.items()) if a},
            "missing": sorted(c for c, a in answers.items() if a == {}),
            "unknown": sorted(c for c, a in answers.items() if a is None)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def _keep_main(argv):
    """appstore.py keep <app id> <bundle> [version id] [--license]: Studio's Keep job (prints its steps)."""
    license_ok = "--license" in argv
    args = [a for a in argv if a != "--license"]
    if len(args) < 2:
        sys.exit("usage: appstore.py keep <app id> <bundle> [version id] [--license]")
    app_id, bundle, version_id = args[0], args[1], (args[2] if len(args) > 2 else "")
    print(f"==> {bundle}: downloading the App Store copy" + (f" (version id {version_id})" if version_id else " (newest)"),
          flush=True)
    try:
        try:
            row = keep(app_id, bundle, version_id)
        except NeedsLicense:
            if not license_ok:
                sys.exit("this Apple ID doesn't have the game: Studio asks before getting a free license")
            print("==> getting the free license first", flush=True)
            get_license(app_id)
            row = keep(app_id, bundle, version_id)
    except AppStoreError as e:
        sys.exit(f"error: {e}")
    print(f"==> kept {row['file']}: version {row['version']}, {row['size'] / 1e6:.1f} MB, sha256 {row['sha256'][:16]}...")
    print("    encrypted to this Apple ID: installs as is on its phones, not a mod base")


if __name__ == "__main__":
    # python scripts/appstore.py install-helper [run id]: the helper .exe from GitHub Actions
    if sys.argv[1:2] == ["install-helper"]:
        print(json.dumps(install_helper(sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
    elif sys.argv[1:2] == ["keep"]:
        _keep_main(sys.argv[2:])
    else:
        sys.exit("usage: appstore.py install-helper [run id] | keep <app id> <bundle> [version id] [--license]")
