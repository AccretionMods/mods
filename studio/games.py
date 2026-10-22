"""Games for Mod Studio's Games tab: App Store charts, new games, and games like the ones we mod.

Apple's public feeds only (no account, no key): the iTunes RSS charts (genre 6014 = Games), the iTunes Lookup
API for ratings, genres and icons, and the iTunes Search API for the "similar" list. Answers are cached for
half an hour per URL. Every row has the same fields (see row()).
"""

import json
import math
import re
import time
import urllib.parse
import urllib.request

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
