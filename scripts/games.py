"""App Store games for Mod Studio's Games tab and `dev.py games`: charts, new games, search, one game's details,
and games like the ones we mod.

Apple's public feeds only (no account, no key): the iTunes RSS charts (genre 6014 = Games), the iTunes Lookup
API for ratings, genres and icons, and the iTunes Search API for search and the "similar" list. Answers are
cached for half an hour per URL. Every row has the same fields (see row()).
"""

import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

GAMES = "6014"
CHARTS = {
    "top-free": "topfreeapplications",
    "top-paid": "toppaidapplications",
    "top-grossing": "topgrossingapplications",
    "new-free": "newfreeapplications",
    "new-paid": "newpaidapplications",
}
TTL = 1800
USER_AGENT = "AccretionModStudio/1"
ONLINE_NAME = re.compile(r"\b(mmo\w*|online|multiplayer|pvp|battle royale|io)\b", re.IGNORECASE)
ONLINE_TEXT = re.compile(r"\b(mmo\w*|multiplayer|pvp|battle royale|guild wars?|clan wars?)\b", re.IGNORECASE)
NOT_THEMES = {"games", "entertainment", "game", "the", "and", "of", "a", "an", "for", "with"}
# themes that make two single-player games alike; matched in names and descriptions
THEME_WORDS = (
    "post-apocalyptic", "post-nuclear", "nuclear", "wasteland", "survival", "vault", "shelter", "zombie",
    "roguelike", "roguelite", "dungeon", "turn-based", "tactics", "rpg", "crafting", "base building",
    "colony", "city builder", "management", "tycoon", "idle", "farming", "pixel", "retro", "open world",
    "sandbox", "fantasy", "sci-fi", "space", "pirate", "medieval", "horror", "mystery", "visual novel",
    "platformer", "metroidvania", "puzzle", "racing", "strategy", "card", "deckbuilding", "stealth",
)
THEME_HIT, GENRE_HIT = 3, 1  # a shared theme says more than a shared App Store genre
_cache = {}


class GamesError(Exception):
    pass


def get_json(url, opener=None):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
            data = json.loads(r.read())
    except (OSError, ValueError) as e:
        raise GamesError(f"the App Store feed did not answer ({e})") from e
    _cache[url] = (time.time(), data)
    return data


def row(r, like=None):
    """One game from an iTunes Lookup/Search result."""
    name = r.get("trackName", "")
    return {
        "bundle": r.get("bundleId"),
        "id": r.get("trackId"),
        "name": name,
        "seller": r.get("sellerName") or r.get("artistName", ""),
        "icon": r.get("artworkUrl512") or r.get("artworkUrl100", ""),
        "rating": round(r.get("averageUserRating") or 0, 1),
        "ratings": r.get("userRatingCount") or 0,
        "price": r.get("formattedPrice") or ("Free" if not r.get("price") else str(r.get("price"))),
        "genres": [g for g in r.get("genres", []) if g not in ("Games", "Entertainment")],
        "released": (r.get("releaseDate") or "")[:10],
        "updated": (r.get("currentVersionReleaseDate") or "")[:10],
        "size": int(r.get("fileSizeBytes") or 0),
        "url": r.get("trackViewUrl", ""),
        "online": bool(ONLINE_NAME.search(name) or ONLINE_TEXT.search(r.get("description") or "")),
        "like": like or [],
    }


def lookup(ids=None, bundles=None, region="us", opener=None):
    """iTunes Lookup for many apps in one call, in the order asked."""
    key, values = ("id", ids) if ids else ("bundleId", bundles)
    if not values:
        return []
    q = urllib.parse.urlencode({key: ",".join(str(v) for v in values), "country": region, "entity": "software"})
    found = get_json(f"https://itunes.apple.com/lookup?{q}", opener).get("results", [])
    field = "trackId" if ids else "bundleId"
    by = {str(r.get(field)): r for r in found if r.get("kind") == "software"}
    return [by[str(v)] for v in values if str(v) in by]


def chart(kind, region="us", limit=50, opener=None):
    if kind not in CHARTS:
        raise GamesError(f"no list {kind!r}: {', '.join(CHARTS)}")
    url = f"https://itunes.apple.com/{region}/rss/{CHARTS[kind]}/limit={limit}/genre={GAMES}/json"
    entries = get_json(url, opener).get("feed", {}).get("entry") or []
    entries = entries if isinstance(entries, list) else [entries]
    ids = []
    for e in entries:
        cat = e.get("category", {}).get("attributes", {}).get("im:id", "")
        if cat == GAMES or cat.startswith("70"):  # the new-app feeds mix in other kinds of apps
            ids.append(e["id"]["attributes"]["im:id"])
    return [row(r) for r in lookup(ids=ids[:limit], region=region, opener=opener)]


def search(term, region="us", limit=50, games_only=False, opener=None):
    """The App Store's own search (any app unless games_only), best match first; a bundle ID looks that app up."""
    term = (term or "").strip()
    if len(term) < 2:
        raise GamesError("search: two letters at least")
    if re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}", term):
        found = lookup(bundles=[term], region=region, opener=opener)
        if found:
            return [row(found[0])]
    params = {"term": term, "entity": "software", "country": region, "limit": min(int(limit), 200)}
    if games_only:
        params["genreId"] = GAMES
    q = urllib.parse.urlencode(params)
    results = get_json(f"https://itunes.apple.com/search?{q}", opener).get("results", [])
    return [row(r) for r in results if r.get("bundleId")]


def info(bundle, region="us", opener=None):
    """One app's details: the row plus its description, version, minimum iOS and age rating."""
    found = lookup(bundles=[bundle], region=region, opener=opener)
    if not found:
        raise GamesError(f"{bundle} is not in the {region.upper()} App Store")
    r = found[0]
    out = row(r)
    out.update(
        {
            "version": r.get("version", ""),
            "min_ios": r.get("minimumOsVersion", ""),
            "age": r.get("contentAdvisoryRating", ""),
            "description": r.get("description", ""),
            "notes": r.get("releaseNotes", ""),
            "languages": r.get("languageCodesISO2A", []),
            "themes": theme_words(r)[:8],
            # what the App Store page shows besides: its screenshots (iPhone first, iPad when it has none), the
            # content warnings, the developer's site and this version's ratings
            "screenshots": (r.get("screenshotUrls") or r.get("ipadScreenshotUrls") or [])[:10],
            "advisories": r.get("advisories", []),
            "seller_url": r.get("sellerUrl", ""),
            "all_genres": r.get("genres", []),
            "rating_now": round(r.get("averageUserRatingForCurrentVersion") or 0, 1),
            "ratings_now": r.get("userRatingCountForCurrentVersion") or 0,
        }
    )
    return out


def theme_words(game):
    """THEME_WORDS in a game's name and description, most mentioned first."""
    text = f"{game.get('trackName', '')} {game.get('trackName', '')} {game.get('description', '')}".lower()
    counts = {w: len(re.findall(r"\b" + re.escape(w) + r"\b", text)) for w in THEME_WORDS}
    return [w for w, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n]


def themes(game):
    """Search terms for games like this one, as (term, weight): the words after the colon in its name and its
    main themes (THEME_HIT), then its App Store sub-genres (GENRE_HIT)."""
    terms = []
    name = game.get("trackName", "")
    if ":" in name:
        tail = " ".join(w for w in re.findall(r"[A-Za-z-]+", name.split(":", 1)[1]) if w.lower() not in NOT_THEMES)
        if tail:
            terms.append((tail, THEME_HIT))
    words = theme_words(game)
    if len(words) >= 2:
        terms.append((f"{words[0]} {words[1]}", THEME_HIT))
    terms += [(w, THEME_HIT) for w in words[:3]]
    terms += [(g, GENRE_HIT) for g in game.get("genres", []) if g not in ("Games", "Entertainment")][:2]
    seen, out = set(), []
    for t, w in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append((t, w))
    return out[:6]


def similar(targets, region="us", limit=40, opener=None):
    """Games like the ones we mod. targets: {bundle ID: our mod title}."""
    ours = [g for g in lookup(bundles=list(targets), region=region, opener=opener) if "Games" in g.get("genres", [])]
    scores, rows = {}, {}
    for game in ours:
        mine = targets[game["bundleId"]]
        my_genres = set(game.get("genres", [])) - {"Games", "Entertainment"}
        my_themes = set(theme_words(game)[:6])
        for term, weight in themes(game):
            q = urllib.parse.urlencode(
                {"term": term, "entity": "software", "country": region, "genreId": GAMES, "limit": 25}
            )
            for r in get_json(f"https://itunes.apple.com/search?{q}", opener).get("results", []):
                b = r.get("bundleId")
                if not b or b in targets or "Games" not in r.get("genres", []):
                    continue
                shared = len(my_genres & set(r.get("genres", []))) + 2 * len(my_themes & set(theme_words(r)))
                score = weight + shared + math.log10(1 + (r.get("userRatingCount") or 0)) / 4
                scores[b] = scores.get(b, 0) + score
                if b not in rows:
                    rows[b] = row(r)
                if mine not in rows[b]["like"]:
                    rows[b]["like"].append(mine)
    best = sorted(rows, key=lambda b: scores[b], reverse=True)[:limit]
    return [rows[b] for b in best]


ICON_DAYS = 30  # an app's icon is fetched again after this long (a new season's art)
MISSING_HOURS = 24  # an app the App Store doesn't have is asked about again after this long
ICON_SIZE = "128x128bb"  # the list shows 40 px icons: 128 px is sharp at 3x and ~10 KB instead of ~60 KB at 512
ICON_BUNDLE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)+$")
_icon_lock = threading.Lock()


def icon_file(root, bundle, others=(), region="us", opener=None):
    """The App Store icon of bundle, cached in <root>/cache/icons (index.json + one image per app), or None when
    the store has no such app. The first miss looks up every bundle in others too, in one iTunes Lookup call,
    and downloads their small icons, so a list of mods costs one lookup instead of one per mod."""
    if not ICON_BUNDLE.match(bundle or ""):
        return None
    folder = Path(root) / "cache" / "icons"
    with _icon_lock:
        try:
            index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {}
        now = time.time()

        def fresh(b):
            e = index.get(b)
            if not e:
                return False
            if e.get("file"):
                return (folder / e["file"]).is_file() and now - e["at"] < ICON_DAYS * 86400
            return now - e["at"] < MISSING_HOURS * 3600

        if not fresh(bundle):
            wanted = [b for b in dict.fromkeys([bundle, *others]) if ICON_BUNDLE.match(b or "") and not fresh(b)][:100]
            try:
                found = {r.get("bundleId"): r for r in lookup(bundles=wanted, region=region, opener=opener)}
            except GamesError:
                return None  # offline: no icon now, ask again next time
            folder.mkdir(parents=True, exist_ok=True)
            for b in wanted:
                url = (found.get(b) or {}).get("artworkUrl100") or (found.get(b) or {}).get("artworkUrl512")
                entry = {"file": None, "at": now}
                if url:
                    url = re.sub(r"\d+x\d+bb", ICON_SIZE, url)
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
                            data, ctype = r.read(), r.headers.get("Content-Type", "image/jpeg")
                        name = b + (".png" if "png" in ctype else ".jpg")
                        (folder / name).write_bytes(data)
                        entry["file"] = name
                    except OSError:
                        continue  # not recorded: tried again next time
                index[b] = entry
            (folder / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        e = index.get(bundle) or {}
        return folder / e["file"] if e.get("file") else None
