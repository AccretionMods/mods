"""Decrypted .ipa files from an unfaird site (github.com/34306/macOSAppstoreDecrypter) into intake/.

The default site, https://decrypt.34306.lol, is a third party's M1 Mac: free, not ours, can be paused or gone.
AM_DECRYPT_URL points at another copy of the same software (our own Mac one day). What the site sends is
checked here (right bundle ID, main binary not FairPlay-encrypted) and its SHA-256 and origin are written
next to it (<file>.source.json); nothing proves the site did not change the app, so mods built on these
copies are for testing on our own phones.

Cached apps download with no account. A new decrypt needs a site account the site's admin approved (a few a
day); the password goes to the site only and is never saved, the session cookie is kept in
<AM_ROOT>/decrypt-session.json.

When the site asks for a captcha (decrypt.34306.lol does, for scripts), nothing here tries to pass it: fetch
opens the site in the browser, the owner downloads the app there, and fetch picks the file up from Downloads,
checks it and moves it into intake/ (import_ipa; `dev.py fetch --import file.ipa` does the same by hand).

Other sources (SOURCES) have no API we use: decrypt.day sits behind a bot check and armconverter.com's store
needs the owner's sign-in, so for them fetch goes straight to that browser hand-off.
"""

import hashlib
import json
import os
import plistlib
import re
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

DEFAULT_SITE = "https://decrypt.34306.lol"
REGIONS = ("us", "jp", "cn", "vn")
PLATFORMS = ("iphone", "appletv", "vision")
BUNDLE_ID = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)+$")
USER_AGENT = "AccretionModStudio/1"
POLL_SECONDS = 5  # the site allows 30 requests a minute per address
WATCH_SECONDS = 3  # how often Downloads is looked at
# name -> (page to open, {region} filled in; True when it is an unfaird site whose API fetch uses)
SOURCES = {
    "34306": (None, True),  # AM_DECRYPT_URL or DEFAULT_SITE
    "decrypt.day": ("https://decrypt.day/home", False),
    "armconverter": ("https://armconverter.com/store/{region}", False),
}


class FetchError(Exception):
    pass


class CaptchaRequired(FetchError):
    """The site wants a person in a browser for this; fetch() hands it to the browser."""


class Vault:
    """The site login in Windows Credential Manager (generic credential "AccretionMods/decrypt/<host>").

    Saved with `dev.py fetch --save-login` from the owner's own console; never in a file or the repo."""

    def __init__(self, base):
        self.target = "AccretionMods/decrypt/" + (urllib.parse.urlparse(base).hostname or base)

    @staticmethod
    def _api():
        if os.name != "nt":
            raise FetchError("saved logins use Windows Credential Manager: run this on Windows")
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
            ]

        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        adv.CredFree.argtypes = [ctypes.c_void_p]
        return ctypes, adv, CREDENTIAL

    def save(self, email, password):
        ctypes, adv, CREDENTIAL = self._api()
        blob = password.encode("utf-16-le")
        cred = CREDENTIAL(
            Type=1, TargetName=self.target, UserName=email, Persist=2,  # GENERIC, LOCAL_MACHINE
            CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                                                     ctypes.POINTER(ctypes.c_char)),
        )
        if not adv.CredWriteW(ctypes.byref(cred), 0):
            raise FetchError(f"Credential Manager refused to save (error {ctypes.get_last_error()})")

    def load(self):
        """(email, password) or None."""
        if os.name != "nt":
            return None
        ctypes, adv, CREDENTIAL = self._api()
        p = ctypes.POINTER(CREDENTIAL)()
        if not adv.CredReadW(self.target, 1, 0, ctypes.byref(p)):
            return None
        try:
            c = p.contents
            password = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
            return c.UserName, password
        finally:
            adv.CredFree(p)

    def forget(self):
        if os.name == "nt":
            ctypes, adv, _ = self._api()
            adv.CredDeleteW(self.target, 1, 0)


def site():
    return (os.environ.get("AM_DECRYPT_URL") or DEFAULT_SITE).rstrip("/")


def is_bundle_id(text):
    return bool(BUNDLE_ID.match(text or ""))


class Client:
    def __init__(self, root, base=None):
        self.base = (base or site()).rstrip("/")
        self.session_file = Path(root) / "decrypt-session.json"
        saved = {}
        if self.session_file.is_file():
            try:
                saved = json.loads(self.session_file.read_text())
            except ValueError:
                saved = {}
        self.cookie = saved.get("cookie") if saved.get("site") == self.base else None

    # ------------------------------------------------------------ http

    def _open(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        return urllib.request.urlopen(req, timeout=timeout)

    def call(self, method, path, body=None):
        """(HTTP status, JSON reply); errors with a JSON body come back too, not as exceptions."""
        try:
            with self._open(method, path, body) as r:
                status, raw, cookie = r.status, r.read(), r.headers.get("Set-Cookie")
        except urllib.error.HTTPError as e:
            status, raw, cookie = e.code, e.read(), None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(f"{self.base} did not answer: {e}") from e
        if cookie and cookie.startswith("session="):
            self.cookie = cookie.split(";", 1)[0]
        try:
            reply = json.loads(raw or b"{}")
        except ValueError:
            reply = {"error": f"HTTP {status}, not JSON"}
        if not isinstance(reply, dict):
            reply = {"result": reply}
        said = str(reply.get("error") or "")
        if reply.get("captcha") or re.search(r"captcha|robot", said, re.IGNORECASE):
            raise CaptchaRequired(f"{self.base} asks for a captcha in a browser (site: {said})")
        return status, reply

    # ------------------------------------------------------------ account

    def save_session(self):
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(json.dumps({"site": self.base, "cookie": self.cookie}))
        try:
            os.chmod(self.session_file, 0o600)
        except OSError:
            pass

    def login(self, email, password):
        self.cookie = None
        status, reply = self.call("POST", "/api/v1/login", {"email": email, "password": password})
        if status != 200 or not self.cookie:
            raise FetchError(f"sign-in refused by {self.base} (HTTP {status}): {reply.get('error', '')}")
        self.save_session()
        return reply.get("user") or {}

    def logout(self):
        if self.cookie:
            self.call("POST", "/api/v1/logout", {})
        self.cookie = None
        self.session_file.unlink(missing_ok=True)

    def account(self):
        """Who we are on the site and whether it takes new decrypts right now."""
        _, health = self.call("GET", "/health")
        _, me = self.call("GET", "/api/v1/me")
        user = me.get("user") or None
        return {
            "site": self.base,
            "up": health.get("status") == "ok",
            "paused": bool(health.get("paused")),
            "open_mode": bool(me.get("open_mode")),
            "signed_in": bool(user),
            "user": {k: user.get(k) for k in ("email", "name", "approved", "remaining", "limit") if k in user} if user else None,
        }

    # ------------------------------------------------------------ apps

    def lookup(self, bundle, region="us", platform="iphone"):
        q = urllib.parse.urlencode({"id": bundle, "region": region, "platform": platform})
        _, reply = self.call("GET", f"/api/v1/lookup?{q}")
        rows = reply.get("results") or []
        if not rows:
            raise FetchError(reply.get("error") or f"{bundle} is not in the {region.upper()} App Store")
        return rows[0]

    def search(self, text, region="us", platform="iphone"):
        if is_bundle_id(text):
            return [self.lookup(text, region, platform)]
        q = urllib.parse.urlencode({"q": text, "region": region, "platform": platform})
        _, reply = self.call("GET", f"/api/v1/search?{q}")
        if reply.get("error"):
            raise FetchError(reply["error"])
        return reply.get("results") or []

    def request(self, bundle, name, region="us", force=False, vault=None):
        body = {"bundle_id": bundle, "app_name": name, "region": region, "force": bool(force)}
        status, reply = self.call("POST", "/api/v1/request", body)
        saved = (vault or Vault(self.base)).load() if status == 401 else None
        if saved:  # not signed in, or the session ran out: sign in with the saved login once
            self.login(*saved)
            status, reply = self.call("POST", "/api/v1/request", body)
        if status == 200:
            return reply
        said = f" (site: {reply.get('error')})" if reply.get("error") else ""
        if status == 401:
            raise FetchError("a new decrypt needs a site account: `am fetch --save-login` (kept for next time) or `am fetch --login`" + said)
        if status == 403:
            raise FetchError("the site refused: account waiting for approval, or this app is blocked" + said)
        if status == 503:
            raise FetchError("the site is paused and takes no new apps right now; try later" + said)
        if status == 429:
            raise FetchError("the site's limit is reached (daily quota, full queue or too many tries)" + said)
        raise FetchError(f"request refused, HTTP {status}" + said)

    def wait(self, job_id, say=print, limit=3 * 3600):
        """Poll a decrypt job until it is completed; prints status changes."""
        last, end = None, time.time() + limit
        while time.time() < end:
            status, job = self.call("GET", f"/api/v1/jobs/{job_id}")
            if status == 404:
                raise FetchError("the site lost the job (restarted?); run fetch again")
            line = " · ".join(str(x) for x in (job.get("status"), job.get("detail"), job.get("progress")) if x)
            if line != last:
                say(f"  {line}")
                last = line
            if job.get("status") == "completed":
                return job
            if job.get("status") == "failed":
                raise FetchError(f"the site could not decrypt it: {job.get('error') or job.get('detail')}")
            time.sleep(POLL_SECONDS)
        raise FetchError("gave up waiting for the decrypt")

    def download(self, bundle, region, folder, say=print):
        """Stream the site's cached copy into folder; returns the file path."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        q = urllib.parse.urlencode({"region": region})
        path = f"/api/v1/download/{urllib.parse.quote(bundle)}?{q}"
        try:
            r = self._open("GET", path, timeout=120)
        except urllib.error.HTTPError as e:
            try:
                said = json.loads(e.read() or b"{}").get("error", "")
            except ValueError:
                said = ""
            raise FetchError(f"download refused, HTTP {e.code} {said}".strip()) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(f"{self.base} did not answer: {e}") from e
        with r:
            name = safe_name(filename_from(r.headers.get("Content-Disposition")) or f"{bundle}_decrypted.ipa")
            total = int(r.headers.get("Content-Length") or 0)
            out = folder / name
            part = out.with_name(out.name + ".part")
            done, shown = 0, 0
            with open(part, "wb") as f:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    f.write(chunk)
                    done += len(chunk)
                    if done - shown >= 50 << 20:
                        shown = done
                        say(f"  {done >> 20} MB" + (f" of {total >> 20} MB" if total else ""))
        if total and done != total:
            part.unlink(missing_ok=True)
            raise FetchError(f"download cut short ({done} of {total} bytes)")
        part.replace(out)
        return out


# ---------------------------------------------------------------- checks


def filename_from(disposition):
    if not disposition:
        return None
    m = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    if m:
        return urllib.parse.unquote(m.group(1))
    m = re.search(r'filename="?([^";]+)"?', disposition)
    return m.group(1) if m else None


def safe_name(name):
    name = re.sub(r"[^\w.\-+]+", "_", Path(name).name, flags=re.UNICODE).strip("._") or "app"
    return name if name.lower().endswith(".ipa") else name + ".ipa"


def encrypted(binary):
    """True when a Mach-O (thin or fat) has LC_ENCRYPTION_INFO(_64) with cryptid 1."""
    for cmd, size in ((0x21, 20), (0x2C, 24)):
        for m in re.finditer(re.escape(struct.pack("<II", cmd, size)), binary):
            if struct.unpack_from("<I", binary, m.start() + 16)[0] == 1:
                return True
    return False


def check_ipa(path, want_bundle=None):
    """Info.plist of a fetched .ipa, after checking it is the app asked for and decrypted."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise FetchError(f"{path} is not an .ipa (zip): {e}") from e
    with z:
        plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
        if not plists:
            raise FetchError(f"{path} has no Payload/*.app")
        info = plistlib.loads(z.read(plists[0]))
        if want_bundle and info.get("CFBundleIdentifier") != want_bundle:
            raise FetchError(f"the site sent {info.get('CFBundleIdentifier')}, not {want_bundle}")
        main = plists[0].rsplit("/", 1)[0] + "/" + info.get("CFBundleExecutable", "")
        if encrypted(z.read(main)):
            raise FetchError("the site sent a copy whose main binary is still FairPlay-encrypted")
    return info


def download_folders():
    home = Path.home()
    return [p for p in (home / "Downloads", home / "OneDrive" / "Downloads") if p.is_dir()]


def bundle_of(path):
    try:
        with zipfile.ZipFile(path) as z:
            plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
            return plistlib.loads(z.read(plists[0])).get("CFBundleIdentifier") if plists else None
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None


def watch_downloads(bundle, since, folders=None, limit=1800):
    """The first .ipa of bundle that lands in Downloads after since, once its size stops changing."""
    folders = folders or download_folders()
    sizes, others, end = {}, set(), time.time() + limit
    while time.time() < end:
        for folder in folders:
            for ipa in folder.glob("*.ipa"):
                try:
                    st = ipa.stat()
                except OSError:
                    continue
                if st.st_mtime < since or ipa in others:
                    continue
                if sizes.get(ipa) != st.st_size:  # new, or still being written
                    sizes[ipa] = st.st_size
                    continue
                if bundle_of(ipa) == bundle:
                    return ipa
                others.add(ipa)
        time.sleep(WATCH_SECONDS)
    raise FetchError(f"no .ipa of {bundle} arrived in {', '.join(map(str, folders))} in {limit // 60} min")


def record_origin(path, site_url, bundle, region, info, via):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    record = {
        "site": site_url,
        "via": via,
        "bundle_id": bundle,
        "region": region,
        "version": info.get("CFBundleShortVersionString"),
        "sha256": h.hexdigest(),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.with_name(path.name + ".source.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def import_ipa(root, path, bundle=None, site_url=None, region=None, say=print):
    """Check a downloaded .ipa and move it into intake/ with its origin record; a bad file stays where it is."""
    path = Path(path)
    if not path.is_file():
        raise FetchError(f"no file at {path}")
    info = check_ipa(path, bundle)
    intake = Path(root) / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    out, n = intake / safe_name(path.name), 2
    while out.exists() and out.resolve() != path.resolve():
        out = intake / f"{safe_name(path.name)[:-4]}~{n}.ipa"
        n += 1
    if out.resolve() != path.resolve():
        shutil.move(str(path), str(out))
    record = record_origin(out, site_url or site(), info.get("CFBundleIdentifier"), region, info, "browser")
    say(f"saved {out} ({record['version']}, decrypted, sha256 {record['sha256'][:12]}…)")
    return out


def by_hand(root, page, target, region, say, wait_limit):
    """Open page in the owner's browser and take the .ipa of target they download there."""
    since = time.time()
    say(f"Opening {page} in your browser: find {target} ({region.upper()} store) and download it.")
    say(f"  Waiting up to {wait_limit // 60} min for it in Downloads (Stop / Ctrl+C ends this)")
    webbrowser.open(page)
    got = watch_downloads(target, since, limit=wait_limit)
    say(f"  found {got}")
    return import_ipa(root, got, target, page, region, say)


def fetch(root, target, region="us", platform="iphone", force=False, folder=None, say=print, client=None,
          browser=True, wait_limit=1800, source="34306"):
    """Lookup, decrypt on the site if needed, download into intake/, check. Returns the .ipa path.

    When the site wants a captcha, or the source has no API, it opens in the browser and this waits for the
    owner's download."""
    if source not in SOURCES:
        raise FetchError(f"no source {source!r}: {', '.join(SOURCES)}")
    page, api = SOURCES[source]
    if not api:
        if not is_bundle_id(target):
            raise FetchError(f"{source} has no API here: give the bundle ID, so the download can be recognised")
        if not browser:
            raise FetchError(f"{source} is browser only: download it there, then run fetch --import <file.ipa>")
        return by_hand(root, page.format(region=region), target, region, say, wait_limit)
    c = client or Client(root)
    try:
        return _fetch_api(c, root, target, region, platform, force, folder, say)
    except CaptchaRequired as e:
        if not is_bundle_id(target):
            raise CaptchaRequired(f"{e}; run fetch with the bundle ID and it opens the site for you") from e
        if not browser:
            raise CaptchaRequired(f"{e}; download it on the site, then run fetch --import <file.ipa>") from e
        say(f"{c.base} wants a captcha, which only a person can answer.")
        return by_hand(root, c.base + "/", target, region, say, wait_limit)


def _fetch_api(c, root, target, region, platform, force, folder, say):
    if not is_bundle_id(target):
        rows = c.search(target, region, platform)
        if len(rows) != 1:
            listing = "\n".join(f"  {r.get('bundle_id')}  {r.get('name')}  {r.get('version')}" for r in rows[:15])
            raise FetchError(
                f"{len(rows)} apps match {target!r}; run again with the bundle ID:\n{listing}"
                if rows
                else f"no app matches {target!r} in the {region.upper()} store"
            )
        target = rows[0]["bundle_id"]
    app = c.lookup(target, region, platform)
    say(f"{app.get('name')} {app.get('version')} ({target}, {region.upper()}) from {c.base}")
    if app.get("is_cached") and not force:
        say("  already decrypted on the site")
    else:
        try:
            job = c.request(target, app.get("name") or target, region, force)
        except FetchError as e:
            if not app.get("cached_version") or force:
                raise
            say(f"  {e}")
            say(f"  using the site's older copy {app['cached_version']} instead")
        else:
            if job.get("job_id"):
                say("  decrypting on the site's Mac (this takes minutes)")
                c.wait(job["job_id"], say)
    out = c.download(target, region, folder or Path(root) / "intake", say)
    try:
        info = check_ipa(out, target)
    except FetchError:
        out.unlink(missing_ok=True)
        raise
    record = record_origin(out, c.base, target, region, info, "api")
    say(f"saved {out} ({record['version']}, decrypted, sha256 {record['sha256'][:12]}…)")
    return out
