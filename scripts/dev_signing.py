"""Signing, installing and the phone, for scripts/dev.py (dev.py sign, install, install-mode; Studio's phone
panel): the owner's certificate through zsign in WSL, the Apple ID through plumesign, pymobiledevice3 over USB.

dev.py re-exports every name here, so `dev.cmd_sign` and the rest keep working. This module reaches dev.py's
shared basics (mod_dir, load_state, am_root, WINDOWS...) as `dev.<name>` when a function runs, so a test or
Studio that sets dev.ROOT or patches dev.mod_dir reaches this code too. Patch this module's own functions
(phone_apps, plumesign...) on dev_signing, not on dev.
"""

from pathlib import Path
import json
import os
import plistlib
import re
import shutil
import subprocess


# ---------------------------------------------------------------- sign / install / log


def plumesign():
    for candidate in (
        os.environ.get("PLUMESIGN_PATH"),
        Path(os.environ.get("APPDATA", "")) / "AccretionLink" / "bin" / "plumesign.exe",
        shutil.which("plumesign"),
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise dev.DevError(
        "plumesign not found: set PLUMESIGN_PATH (Accretion Link keeps one in %APPDATA%\\AccretionLink\\bin)"
    )


def tool_path(name):
    """A tool on PATH, or in uv's bin folder (~/.local/bin), which a server started outside a login shell may lack."""
    found = shutil.which(name)
    if found:
        return found
    exe = Path.home() / ".local" / "bin" / (name + (".exe" if dev.WINDOWS else ""))
    return str(exe) if exe.is_file() else None


def pymobiledevice3_cli():
    exe = tool_path("pymobiledevice3")
    if not exe:
        raise dev.DevError("pymobiledevice3 not found (uv tool install pymobiledevice3)")
    return exe


def pymobiledevice3_python():
    """The Python of the installed pymobiledevice3 (uv tool, or the one next to the pymobiledevice3 on PATH)."""
    candidates = []
    uv = tool_path("uv")
    r = (
        subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, check=False)
        if uv
        else None
    )
    if r and r.returncode == 0 and r.stdout.strip():
        candidates.append(
            Path(r.stdout.strip())
            / "pymobiledevice3"
            / ("Scripts" if dev.WINDOWS else "bin")
            / ("python.exe" if dev.WINDOWS else "python")
        )
    if dev.WINDOWS:
        candidates.append(
            Path(os.environ.get("APPDATA", ""))
            / "uv"
            / "tools"
            / "pymobiledevice3"
            / "Scripts"
            / "python.exe"
        )
    exe = tool_path("pymobiledevice3")
    if exe:
        candidates.append(
            Path(exe).resolve().parent / ("python.exe" if dev.WINDOWS else "python")
        )
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def usbmux_device_id():
    """usbmux's numeric DeviceID of the phone on USB (what plumesign --udid wants), via pymobiledevice3."""
    python = pymobiledevice3_python()
    if not python:
        raise dev.DevError("pymobiledevice3 not found (uv tool install pymobiledevice3)")
    # one line on purpose: multi-line string literals have been mangled on the owner's PC (handbook rule 8)
    code = (
        "import asyncio, inspect; from pymobiledevice3 import usbmux; r = usbmux.list_devices(); "
        "r = asyncio.run(r) if inspect.isawaitable(r) else r; "
        "usb = [d for d in r if str(getattr(d, 'connection_type', 'USB')).upper() == 'USB']; "
        "print(usb[0].devid if usb else '')"
    )
    r = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    devid = r.stdout.strip()
    if r.returncode != 0 or not devid:
        detail = (" " + r.stderr.strip().splitlines()[-1]) if r.stderr.strip() else ""
        raise dev.DevError(
            "no iPhone on USB (pymobiledevice3 sees no device: unlock the phone, trust this PC, check the cable)"
            + detail
        )
    return devid


INSTALL_MODES = {
    "original": "Original game ID: recommended for compatibility",
    "separate": "Separate copy: installs alongside the original",
}


def install_mode(mod):
    """How the mod installs on the owner's phone: "original" (the game's own bundle ID) or "separate" (mod.json's
    bundle_id beside the original). Kept in the dev state, never in mod.json: store entries, published releases
    and CI builds keep using mod.json's bundle_id. New mods start on "original" (cmd_new); projects from before
    this setting keep "separate", what they always built, until it is changed."""
    return dev.load_state(mod).get("install_mode") or "separate"


def install_bundle(mod, meta=None):
    """The bundle ID a local build of mod gets (before signing)."""
    meta = meta or dev.read_json(dev.mod_dir(mod) / "mod.json")
    return meta["target_bundle_id"] if install_mode(mod) == "original" else meta["bundle_id"]


def cmd_install_mode(args):
    if not args.mode:
        mode = install_mode(args.mod)
        print(f"{args.mod}: {INSTALL_MODES[mode]} ({install_bundle(args.mod)})")
        return 0
    state = dev.load_state(args.mod)
    state["install_mode"] = args.mode
    dev.save_state(args.mod, state)
    print(f"==> {args.mod}: {INSTALL_MODES[args.mode]} ({install_bundle(args.mod)}); the next build uses it")
    return 0


def phone_apps():
    """{bundle ID: info} of the apps on the USB phone."""
    r = subprocess.run([pymobiledevice3_cli(), "apps", "list"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, check=False)
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise dev.DevError(f"could not list the phone's apps: {detail}")
    try:
        return json.loads(r.stdout[r.stdout.index("{"):])
    except ValueError:
        raise dev.DevError("pymobiledevice3 apps list printed no JSON") from None


def installed_id(mod, meta=None, apps=None):
    """The mod's app on the USB phone, or None: the ID Studio verified after its last install when it is still
    there, else a copy of the install-mode ID with a signer's team suffix, else a copy under mod.json's
    bundle_id (installs from before the setting). The original game itself never counts as the mod."""
    meta = meta or dev.read_json(dev.mod_dir(mod) / "mod.json")
    apps = apps if apps is not None else phone_apps()
    known = dev.load_state(mod).get("installed_bundle")
    if known and known in apps:
        return known
    for prefix, exact in ((install_bundle(mod, meta), False), (meta["bundle_id"], True)):
        found = sorted(b for b in apps if (exact and b == prefix) or b.startswith(prefix + "."))
        if found:
            return found[0]
    return None


def replacing_original(mod, bundle, apps):
    """True when installing bundle would replace an app Studio did not install: the original game."""
    return bundle in apps and bundle != dev.load_state(mod).get("installed_bundle")


# The owner's distribution certificate (INVENTORY.yaml signing.own_certificate). zsign without -b keeps each app's
# own bundle ID, so games tied to it (Firebase, a vendor login) keep working; Apple ID signing appends the team and
# broke Last Day on Earth's account switch (playbook/ios.md). Neither gives push or Sign in with Apple; only Apple ID
# signing gives get-task-allow (JIT).
CERT_DIR = "~/.accretion-signing"


def bash(script, *args):
    """bash -lc script with positional arguments ($1, $2...): through WSL on Windows, directly elsewhere."""
    cmd = ["bash", "-lc", script, "_", *args]
    return ["wsl", "-e", *cmd] if dev.WINDOWS else cmd


def certificate_ready():
    """Whether the certificate, its password file, its profile and zsign are all there."""
    script = f"cd {CERT_DIR} 2>/dev/null && test -f cert.p12 -a -f password -a -f profile.mobileprovision && command -v zsign"
    return subprocess.run(bash(script), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          check=False).returncode == 0


def signer_for(args):
    """"certificate" (the default) or "apple-id": --apple-id, or no certificate set up."""
    if getattr(args, "apple_id", False):
        return "apple-id"
    if certificate_ready():
        return "certificate"
    print(f"==> no certificate in WSL {CERT_DIR} (cert.p12, password, profile.mobileprovision) or no zsign: "
          "signing with the Apple ID instead")
    return "apple-id"


def minimal_entitlements(profile, bundle, jit=False):
    """The fewest entitlements a mod needs, from the provisioning profile's own set: its application identifier (a
    wildcard profile's "TEAM.*" becomes TEAM.<bundle>), its team, one keychain group (that identifier), and
    get-task-allow only for a mod with JIT hooks when the profile grants it. Without this zsign passes the profile's
    whole set through (HealthKit, HomeKit, VPN, NFC...), capabilities the game never had."""
    app_id = profile.get("application-identifier")
    team = profile.get("com.apple.developer.team-identifier")
    if not isinstance(app_id, str) or not isinstance(team, str) or not app_id.startswith(team + "."):
        raise dev.DevError("the provisioning profile has no usable application-identifier/team-identifier")
    if app_id.endswith("*"):
        app_id = app_id[:-1] + bundle
    out = {"application-identifier": app_id, "com.apple.developer.team-identifier": team,
           "keychain-access-groups": [app_id]}
    if jit and profile.get("get-task-allow") is True:
        out["get-task-allow"] = True
    return out


def profile_entitlements():
    """The Entitlements dictionary of the certificate's provisioning profile (a signed plist: the XML is read out)."""
    r = subprocess.run(bash(f"cat {CERT_DIR}/profile.mobileprovision"), capture_output=True,
                       stdin=subprocess.DEVNULL, check=False)
    found = re.search(rb"<\?xml.*?</plist>", r.stdout, re.S)
    if r.returncode != 0 or not found:
        raise dev.DevError(f"can't read {CERT_DIR}/profile.mobileprovision")
    return plistlib.loads(found.group(0)).get("Entitlements", {})


def certificate_sign(ipa, out, entitlements=None):
    """zsign with the owner's certificate, keeping the app's bundle ID. The password goes from its file straight to
    zsign: it is never printed or passed through this process. `entitlements` (a dict) replaces the profile's full
    set with exactly those keys (zsign -e)."""
    script = f'cd {CERT_DIR} && zsign -k cert.p12 -p "$(cat password)" -m profile.mobileprovision -o "$1" "$2"'
    extra = []
    if entitlements is not None:
        plist = dev.am_root() / "work" / f"entitlements-{Path(out).stem}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(plistlib.dumps(entitlements))
        script += ' -e "$3"'
        extra = [dev.wsl_path(plist)]
        print(f"==> minimal entitlements: {', '.join(sorted(entitlements))}")
    print(f"==> zsign with the certificate in {CERT_DIR} (keeps the app's own ID)")
    r = subprocess.run(bash(script, dev.wsl_path(out), dev.wsl_path(ipa), *extra), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, check=False)
    if r.returncode != 0 or not Path(out).is_file():
        tail = [line for line in (r.stdout + r.stderr).splitlines() if line.strip()][-3:]
        raise dev.DevError(f"zsign failed ({r.returncode}): {' / '.join(tail)}")


def cmd_sign(args):
    signer = signer_for(args)
    if signer == "apple-id" and not dev.WINDOWS:
        raise dev.DevError("Apple ID signing runs on Windows (plumesign with the saved Apple ID)")
    ipa = Path(args.ipa)
    if not ipa.is_file():
        raise dev.DevError(f"no file at {ipa}")
    out = (
        Path(args.out)
        if args.out
        else ipa.with_name(ipa.stem.replace("_accretion", "") + "_signed.ipa")
    )
    mod = getattr(args, "mod", None)
    chosen = dev.ipa_info(ipa)[0]["CFBundleIdentifier"]
    if mod and signer == "certificate" and any(h.get("needs_jit") for h in dev.load_hooks(dev.mod_dir(mod))["hooks"]):
        print("==> note: this mod has hooks that need JIT; a distribution certificate can't give get-task-allow, so "
              "they won't run (pins do). --apple-id signs for JIT.")
    # a kept ID can replace the game, and so can an "original" install: sign first, look at the phone, then install
    careful = bool(args.install and (signer == "certificate" or (mod and install_mode(mod) == "original")))
    minimal = getattr(args, "minimal_entitlements", False)
    if minimal and signer != "certificate":
        raise dev.DevError("--minimal-entitlements needs the certificate (zsign); Apple ID signing sets its own")
    if signer == "certificate":
        entitlements = None
        if minimal:
            jit = bool(mod) and any(h.get("needs_jit") for h in dev.load_hooks(dev.mod_dir(mod))["hooks"])
            entitlements = minimal_entitlements(profile_entitlements(), chosen, jit)
            if jit and "get-task-allow" not in entitlements:
                print("==> note: the profile has no get-task-allow, so JIT hooks can't run with this certificate")
        certificate_sign(ipa, out, entitlements)
    else:
        cmd = [plumesign(), "sign", "-p", str(ipa), "--apple-id", "-o", str(out)]
        if args.install and not careful:
            cmd += ["--register-and-install", "--udid", usbmux_device_id()]
        print("==> " + " ".join(cmd))
        r = subprocess.run(
            cmd, check=False
        )  # interactive: Apple ID prompts (2FA) go to this console
        if r.returncode != 0:
            raise dev.DevError(f"plumesign failed ({r.returncode})")
    print(f"==> {out}")
    bundle = dev.ipa_info(out)[0]["CFBundleIdentifier"]
    if bundle != chosen:
        print(f"==> the signer changed the app ID: {chosen} -> {bundle} (Apple ID signing adds the team ID when it "
              "can't register the original; the phone tools follow the signed ID)")
    else:
        print(f"==> app ID kept: {bundle}")
    if mod:
        state = dev.load_state(mod)
        state.update(signed_bundle=bundle, signed_from=chosen)
        dev.save_state(mod, state)
    if args.install:
        apps = phone_apps()
        # without --mod there's no record of what Studio installed: an app already under this ID counts as the game
        theirs = bundle in apps and (not mod or replacing_original(mod, bundle, apps))
        if careful and theirs and not getattr(args, "replace_original", False):
            raise dev.DevError(
                f"{bundle} is already on the phone and Studio did not install it: this is the original game. "
                "Installing replaces it (its app and possibly its data). Run again with --replace-original to "
                "replace it" + (f", or choose \"separate\" (dev.py install-mode {mod} separate)." if mod else
                                ", or pass --mod so Studio knows its own install.")
            )
        if careful or bundle not in apps:
            if not careful:
                # plumesign's install has left the app missing minutes later (2026-10-21, FBSApplicationLibrary
                # NotFound); install the signed copy over USB if it is not there
                print(f"==> {bundle} is not on the phone after plumesign's install; installing the signed copy over USB")
            elif bundle in apps:
                print(f"==> replacing {bundle} on the phone (confirmed)")
            if subprocess.run([pymobiledevice3_cli(), "apps", "install", str(out)],
                              stdin=subprocess.DEVNULL, check=False).returncode != 0:
                raise dev.DevError("pymobiledevice3 apps install failed (" + (
                    "is this phone in the certificate's profile? --apple-id signs with the Apple ID instead)"
                    if signer == "certificate" else
                    "a phone the Apple ID has not registered yet: install once with install-mode separate, or "
                    "plumesign --register-and-install)"))
            apps = phone_apps()
        if bundle not in apps:
            raise dev.DevError(f"{bundle} is not on the phone after the install")
        print(f"==> installed as {bundle}")
        if mod:
            state = dev.load_state(mod)
            state["installed_bundle"] = bundle  # verified on the phone: the phone tools use it
            dev.save_state(mod, state)
        if getattr(args, "launch", False):
            launch_app(bundle)
    return 0


def installed_bundles(prefix):
    """Bundle IDs on the USB phone that start with prefix (an Apple ID-signed copy carries a team suffix)."""
    r = subprocess.run(
        [pymobiledevice3_cli(), "apps", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise dev.DevError(f"could not list the phone's apps: {detail}")
    try:
        apps = json.loads(r.stdout[r.stdout.index("{") :])
    except ValueError:
        raise dev.DevError("pymobiledevice3 apps list printed no JSON") from None
    return [b for b in apps if b.startswith(prefix)]


def launch_app(bundle):
    """Opens an app on the USB phone through the developer tunnel (Developer Mode on, phone unlocked)."""
    print(f"==> launching {bundle}")
    r = subprocess.run(
        [pymobiledevice3_cli(), "developer", "dvt", "launch", bundle],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise dev.DevError(
            f"launch failed ({detail}); open the app by hand, then dev.py log"
        )
    return True


def cmd_install(args):
    ipa = Path(args.ipa)
    if not ipa.is_file():
        raise dev.DevError(f"no file at {ipa}")
    r = subprocess.run(
        [pymobiledevice3_cli(), "apps", "install", str(ipa)],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return r.returncode


# Imported last: dev.py imports this module while it loads, so the names above must exist by then; this
# module only uses dev's names when a function runs.
import dev  # noqa: E402
