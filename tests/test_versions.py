import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from update_source import app_version_of, store_version, version_key


class Versions(unittest.TestCase):
    def test_store_version(self):
        self.assertEqual(store_version("4.0.0", "1.1.0"), "4.0.0-mod1.1.0")
        self.assertEqual(app_version_of("4.0.0-mod1.1.0"), "4.0.0")
        # builds published before mod versions
        self.assertEqual(app_version_of("4.0.0"), "4.0.0")

    def test_order(self):
        # every mod update is newer, and a new app version beats any mod version of the old one
        order = ["4.0.0", "4.0.0-mod1.0.0", "4.0.0-mod1.1.0", "4.0.0-mod1.10.0", "4.0.0-mod2.0.0", "4.1.0-mod1.0.0"]
        self.assertEqual(sorted(order, key=version_key), order)


if __name__ == "__main__":
    unittest.main()
