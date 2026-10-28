"""keys: the secrets file the hooks source."""
import unittest

from baton import keys
from tests.helpers import POSIX, Home


class Secrets(Home):
    def test_key_with_shell_characters_round_trips(self):
        keys.write("XAI_API_KEY", "a b'c$d")
        keys.write("OTHER", "x")
        keys.write("XAI_API_KEY", "new$key")
        self.assertEqual(keys.read(), {"XAI_API_KEY": "new$key", "OTHER": "x"})
        self.assertEqual(keys.mode(), 0o600 if POSIX else None)

if __name__ == "__main__":
    unittest.main()
