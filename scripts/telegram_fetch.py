"""Decrypted .ipa files from the owner's Telegram decrypt bot into intake/ (fetch's main source, "telegram").

The bot takes an App Store link and answers with a download: a direct link (in its text or a button), a button to
press first, or the .ipa itself as a file. This sends the game's App Store link to that one chat as the owner's own
Telegram account, waits for the answer, follows it, and hands the file to ipa_fetch.import_ipa (an .ipa of the app
asked for, decrypted: cryptid 0), so it lands in intake/ with its <file>.source.json like the other sources.

Runs with Telethon through uv (`uv run --no-project --python 3.12 --with "telethon>=1.40,<2" python ...`), which is
how ipa_fetch calls it, so Studio and dev.py need nothing installed:
  telegram_fetch.py login              once, in a console: api_id and api_hash (my.telegram.org), phone, code
  telegram_fetch.py fetch <bundle|name> [--region us] [--wait 1800]   prints {"path": ...} as its last line
  telegram_fetch.py status | logout

The sign-in (a Telethon session: full access to the account) is kept in AM_ROOT/studio/telegram.json encrypted for
this Windows user (appstore._protect), and api_id/api_hash in Windows Credential Manager
("AccretionMods/telegram/api"). Only the bot's chat (AM_TELEGRAM_CHAT, default 7019185679) is ever written to.
"""

import argparse
import base64
import getpass
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appstore  # noqa: E402 - _protect (Windows data protection) and state_dir
import ipa_fetch  # noqa: E402 - import_ipa, safe_name, FetchError

CHAT = int(os.environ.get("AM_TELEGRAM_CHAT") or 7019185679)
CREDENTIAL = "AccretionMods/telegram/api"
POLL = 3  # seconds between looks at the chat
DOWNLOAD_WORDS = re.compile(r"download|get|ipa|decrypt|descargar", re.IGNORECASE)


def _session_path():
    return appstore.state_dir() / "telegram.json"


def _vault():
    vault = ipa_fetch.Vault("telegram")
    vault.target = CREDENTIAL
    return vault


def _api():
    """(api_id, api_hash) from Windows Credential Manager."""
    saved = _vault().load()
    if not saved:
        raise ipa_fetch.FetchError("Telegram isn't set up: run `telegram_fetch.py login` once in a console")
    api_id, api_hash = saved
    return int(api_id), api_hash


def _client(session=""):
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    api_id, api_hash = _api()
    return TelegramClient(StringSession(session), api_id, api_hash, device_model="Accretion Mod Studio")


def _load_session():
    try:
        data = json.loads(_session_path().read_text(encoding="utf-8"))
        return appstore._protect(base64.b64decode(data["session"]), unprotect=True).decode()
    except (OSError, ValueError, KeyError, appstore.AppStoreError):
        return ""


def _save_session(session, who):
    data = {"session": base64.b64encode(appstore._protect(session.encode())).decode(), "account": who}
    _session_path().write_text(json.dumps(data), encoding="utf-8")


def login():
    """Interactive, in the owner's console: the app keys, then Telegram's own sign-in."""
    from telethon.errors import SessionPasswordNeededError

    vault = _vault()
    if not vault.load():
        print("Telegram app keys from https://my.telegram.org > API development tools (kept in Credential Manager)")
        api_id = input("api_id: ").strip()
        api_hash = getpass.getpass("api_hash (hidden): ").strip()
        if not api_id.isdigit() or len(api_hash) < 16:
            sys.exit("that doesn't look like an api_id and api_hash")
        vault.save(api_id, api_hash)
    client = _client()
    client.connect()
    try:
        phone = input("phone number with country code (+1...): ").strip()
        client.send_code_request(phone)
        code = input("the code Telegram sent you: ").strip()
        try:
            client.sign_in(phone, code)
        except SessionPasswordNeededError:
            client.sign_in(password=getpass.getpass("two-step verification password (hidden): "))
        me = client.get_me()
        who = f"@{me.username}" if me.username else (me.first_name or "your account")
        _save_session(client.session.save(), who)
        print(f"signed in as {who}; fetch now asks the bot (chat {CHAT})")
    finally:
        client.disconnect()


def status():
    try:
        who = json.loads(_session_path().read_text(encoding="utf-8")).get("account", "")
    except (OSError, ValueError):
        who = ""
    return {"signed_in": bool(_load_session()), "account": who, "chat": CHAT}


def logout():
    _session_path().unlink(missing_ok=True)
    return {"signed_in": False}


def app_store_link(target, region):
    """The App Store page link the bot takes, from a bundle ID or a name (Apple's public lookup)."""
    key = "bundleId" if ipa_fetch.is_bundle_id(target) else "term"
    q = urllib.parse.urlencode({key: target, "country": region, "entity": "software", "limit": 1})
    url = f"https://itunes.apple.com/{'lookup' if key == 'bundleId' else 'search'}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": ipa_fetch.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        found = json.loads(r.read()).get("results", [])
    if not found:
        raise ipa_fetch.FetchError(f"{target} isn't on the {region.upper()} App Store")
    app = found[0]
    return app["trackViewUrl"].split("?")[0], app.get("bundleId"), app.get("trackName", target)


def links_in(message):
    """https links in a bot message: URL buttons first, then text links, then bare links in the text."""
    found = []
    for row in message.buttons or []:
        for b in row:
            if getattr(b, "url", None):
                found.append(b.url)
    for entity, text in message.get_entities_text() if message.entities else []:
        found.append(getattr(entity, "url", None) or text)
    found += re.findall(r"https?://[^\s)>\]]+", message.message or "")
    return [u for u in dict.fromkeys(found) if u and u.startswith("https://") and "apps.apple.com" not in u]


def _download_link(url, folder, say):
    """Streams a direct link into folder (https only, 8 GB at most); returns the file."""
    req = urllib.request.Request(url, headers={"User-Agent": ipa_fetch.USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        name = ipa_fetch.filename_from(r.headers.get("Content-Disposition"))
        name = ipa_fetch.safe_name(name or Path(urllib.parse.urlparse(r.geturl()).path).name or "app.ipa")
        total = int(r.headers.get("Content-Length") or 0)
        if total > 8 << 30:
            raise ipa_fetch.FetchError("the bot's link is larger than 8 GB: not an .ipa")
        out = Path(folder) / name
        part = out.with_name(out.name + ".part")
        done, shown = 0, 0
        with open(part, "wb") as f:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                f.write(chunk)
                done += len(chunk)
                if done > 8 << 30:
                    raise ipa_fetch.FetchError("the bot's link went past 8 GB: stopped")
                if done - shown >= 50 << 20:
                    shown = done
                    say(f"  {done >> 20} MB" + (f" of {total >> 20} MB" if total else ""))
    if total and done != total:
        part.unlink(missing_ok=True)
        raise ipa_fetch.FetchError(f"download cut short ({done} of {total} bytes)")
    part.replace(out)
    return out


def fetch(root, target, region="us", wait_limit=1800, say=print):
    """Asks the bot for target and brings its answer into intake/ (checked). Returns the .ipa path."""
    session = _load_session()
    if not session:
        raise ipa_fetch.FetchError("not signed in to Telegram: run `telegram_fetch.py login` once in a console")
    link, bundle, name = app_store_link(target, region)
    folder = Path(root) / "intake"
    folder.mkdir(parents=True, exist_ok=True)
    client = _client(session)
    client.connect()
    try:
        if not client.is_user_authorized():
            raise ipa_fetch.FetchError("the Telegram sign-in ended: run `telegram_fetch.py login` again")
        client.get_dialogs(limit=100)  # so the chat id resolves without a username
        chat = client.get_entity(CHAT)
        sent = client.send_message(chat, link)
        say(f"asked the bot for {name} ({bundle}): {link}")
        clicked, deadline, last = set(), time.time() + wait_limit, sent.id
        while time.time() < deadline:
            time.sleep(POLL)
            for m in reversed(client.get_messages(chat, min_id=last, limit=20)):
                last = max(last, m.id)
                if m.out:
                    continue
                if m.file and (m.file.name or "").lower().endswith(".ipa"):
                    out = folder / ipa_fetch.safe_name(m.file.name)
                    part = out.with_name(out.name + ".part")
                    say(f"the bot sent the file {m.file.name} ({(m.file.size or 0) >> 20} MB): downloading")
                    client.download_media(m, file=str(part))
                    part.replace(out)
                    return ipa_fetch.import_ipa(root, out, bundle, f"telegram:{CHAT}", region, say, via="telegram")
                for url in links_in(m):
                    say(f"the bot answered with a link: downloading from {urllib.parse.urlparse(url).hostname}")
                    try:
                        got = _download_link(url, folder, say)
                    except (OSError, ipa_fetch.FetchError) as e:
                        say(f"  that link didn't give a file ({e}); waiting for another answer")
                        continue
                    try:
                        return ipa_fetch.import_ipa(root, got, bundle, f"telegram:{CHAT}", region, say, via="telegram")
                    except ipa_fetch.FetchError as e:
                        got.unlink(missing_ok=True)
                        say(f"  not the decrypted .ipa ({e}); waiting for another answer")
                for i, row in enumerate(m.buttons or []):  # a button to press first ("Download", "Get IPA")
                    for j, b in enumerate(row):
                        key = (m.id, i, j)
                        if getattr(b, "data", None) and DOWNLOAD_WORDS.search(b.text or "") and key not in clicked:
                            clicked.add(key)
                            say(f"pressing the bot's \"{b.text}\" button")
                            m.click(i, j)
                text = (m.message or "").strip().splitlines()
                if text and not links_in(m):
                    say(f"bot: {text[0][:120]}")
        raise ipa_fetch.FetchError(f"the bot gave no .ipa within {wait_limit // 60} minutes")
    finally:
        client.disconnect()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("status")
    sub.add_parser("logout")
    f = sub.add_parser("fetch")
    f.add_argument("target")
    f.add_argument("--region", default="us")
    f.add_argument("--wait", type=int, default=1800)
    a = p.parse_args(argv)
    root = appstore.state_dir().parent
    try:
        if a.cmd == "login":
            login()
        elif a.cmd == "status":
            print(json.dumps(status()))
        elif a.cmd == "logout":
            print(json.dumps(logout()))
        else:
            path = fetch(root, a.target, a.region, a.wait, say=lambda s: print(s, flush=True))
            print(json.dumps({"path": str(path)}))
    except (ipa_fetch.FetchError, appstore.AppStoreError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
