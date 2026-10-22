"""scripts/games.py: charts and "like ours" from canned App Store answers (no network)."""

import io
import json
import os
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import games


def app(bundle, track, name, genres, ratings=10, description=""):
    return {
        "kind": "software", "bundleId": bundle, "trackId": track, "trackName": name, "sellerName": "Maker",
        "genres": ["Games"] + genres, "userRatingCount": ratings, "averageUserRating": 4.44, "price": 0,
        "formattedPrice": "Free", "artworkUrl512": "https://example.test/i.png", "description": description,
        "trackViewUrl": f"https://apps.apple.com/app/id{track}",
    }


OURS = app("com.ours.wander", 1, "Wanderer: Post-Nuclear RPG", ["Adventure"],
           description="A wasteland RPG. Cross the wasteland and survive the wasteland.")
RADIO = dict(app("com.ours.radio", 2, "Radio", []), genres=["Music"])
APPS = {
    11: app("com.a.top", 11, "Top Game", ["Puzzle"]),
    12: app("com.a.second", 12, "Second Game", ["Puzzle"]),
}
SEARCH = {
    "post-nuclear rpg": [OURS, app("com.b.waste", 21, "Wasteland Tale", ["Roleplaying"], 50, "wasteland survival rpg")],
    "wasteland": [app("com.b.waste", 21, "Wasteland Tale", ["Roleplaying"], 50, "wasteland survival rpg"),
                  app("com.b.mmo", 22, "Ruins Online", ["Roleplaying"], 9000, "wasteland pvp")],
    "adventure": [app("com.b.big", 23, "Huge Adventure", ["Adventure"], 5_000_000)],
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Opener:
    def __init__(self):
        self.urls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        u, q = urlparse(url), {k: v[-1] for k, v in parse_qs(urlparse(url).query).items()}
        if "/rss/" in u.path:
            entry = lambda i, cat: {"id": {"attributes": {"im:id": str(i)}}, "category": {"attributes": {"im:id": cat}}}
            data = {"feed": {"entry": [entry(11, "6014"), entry(99, "6000"), entry(12, "7003")]}}
        elif u.path == "/lookup":
            if "id" in q:
                data = {"results": [APPS[int(i)] for i in q["id"].split(",") if int(i) in APPS]}
            else:
                data = {"results": [a for a in (OURS, RADIO) if a["bundleId"] in q["bundleId"].split(",")]}
        else:
            data = {"results": SEARCH.get(q["term"].lower(), [])}
        return FakeResponse(json.dumps(data).encode())


class Games(unittest.TestCase):
    def setUp(self):
        games._cache.clear()

    def test_chart_keeps_games_in_feed_order_and_caches(self):
        op = Opener()
        rows = games.chart("top-free", "us", opener=op)
        self.assertEqual([r["bundle"] for r in rows], ["com.a.top", "com.a.second"])  # 99 is not a game
        self.assertEqual(rows[0]["genres"], ["Puzzle"])
        self.assertEqual(rows[0]["rating"], 4.4)
        games.chart("top-free", "us", opener=op)
        self.assertEqual(len(op.urls), 2)  # feed + lookup, then both from the cache
        with self.assertRaises(games.GamesError):
            games.chart("top-everything", opener=op)

    def test_similar_ranks_shared_themes_and_flags_online(self):
        rows = games.similar({"com.ours.wander": "Wanderer+", "com.ours.radio": "Radio+"}, opener=Opener())
        names = [r["name"] for r in rows]
        self.assertNotIn("Wanderer: Post-Nuclear RPG", names)  # ours never suggested
        self.assertEqual(names[0], "Wasteland Tale")  # found by two themes beats a huge but plain hit
        self.assertEqual(rows[0]["like"], ["Wanderer+"])
        self.assertTrue(next(r for r in rows if r["name"] == "Ruins Online")["online"])
        self.assertFalse(rows[0]["online"])

    def test_search_by_words_or_bundle_id_and_info(self):
        op = Opener()
        self.assertEqual([r["name"] for r in games.search("wasteland", opener=op)], ["Wasteland Tale", "Ruins Online"])
        games.search("wasteland", games_only=True, opener=op)
        self.assertIn("genreId=6014", op.urls[-1])
        self.assertEqual(games.search("com.ours.wander", opener=op)[0]["name"], OURS["trackName"])
        with self.assertRaises(games.GamesError):
            games.search("x", opener=op)
        g = games.info("com.ours.wander", opener=op)
        self.assertIn("wasteland", g["themes"])
        self.assertTrue(g["description"].startswith("A wasteland"))
        with self.assertRaises(games.GamesError):
            games.info("com.nobody.here", opener=op)

    def test_dev_cli_games_commands(self):
        import contextlib
        import tempfile
        from pathlib import Path

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import dev

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "mods" / "wander-plus").mkdir(parents=True)
            (repo / "mods" / "wander-plus" / "mod.json").write_text(
                json.dumps({"name": "Wander+", "target_bundle_id": "com.ours.wander"})
            )
            old = (dev.ROOT, os.environ.get("AM_ROOT"), games.urllib.request.urlopen)
            dev.ROOT, os.environ["AM_ROOT"] = repo, str(Path(tmp) / "am")
            games.urllib.request.urlopen = Opener()
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(dev.main(["games", "search", "com.ours.wander"]), 0)
                    self.assertEqual(dev.main(["games", "info", "com.ours.wander", "--json"]), 0)
                    self.assertEqual(dev.main(["games", "like", "com.ours.wander", "--json"]), 0)
                    self.assertEqual(dev.main(["games", "--region", "u s"]), 1)
                text = out.getvalue()
                self.assertIn("our mod wander-plus", text)
                self.assertIn('"min_ios"', text)
                self.assertIn('"like": [', text)
            finally:
                dev.ROOT, env, games.urllib.request.urlopen = old
                if env is None:
                    os.environ.pop("AM_ROOT", None)
                else:
                    os.environ["AM_ROOT"] = env

    def test_themes_come_from_name_and_description(self):
        terms = [t for t, _ in games.themes(OURS)]
        self.assertEqual(terms[0], "Post-Nuclear RPG")
        self.assertIn("wasteland", terms)
        self.assertIn("Adventure", terms)


if __name__ == "__main__":
    unittest.main()
