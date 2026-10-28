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

    def test_the_old_file_is_read_until_there_is_a_new_one(self):
        old = keys.old_secrets_path()
        old.parent.mkdir(parents=True)
        old.write_text("XAI_API_KEY=old\n")
        self.assertEqual(keys.read(), {"XAI_API_KEY": "old"})
        keys.write("XAI_API_KEY", "new")
        self.assertEqual(keys.read(), {"XAI_API_KEY": "new"})

if __name__ == "__main__":
    unittest.main()
