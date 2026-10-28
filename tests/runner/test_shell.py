"""runner.shell: fences, executables and the simple commands in a line."""
import unittest

from baton.runner.shell import extract_fences, first_word, logical_lines, simple_commands, writes_file


class Shell(unittest.TestCase):
    def test_bash_fence_lines(self):
        text = "Sure.\n```bash\nls -la\n# note\nrm /tmp/x \\\n  -f\n```\n"
        self.assertEqual(logical_lines(extract_fences(text)[0]), ["ls -la", "rm /tmp/x   -f"])

    def test_tagged_fences_win_over_bare_ones(self):
        self.assertEqual(extract_fences("```\nnot this\n```\n```sh\nls\n```"), ["ls\n"])
        self.assertEqual(extract_fences("```\nls\n```"), ["ls\n"])

    def test_first_word_skips_prefixes(self):
        self.assertEqual(first_word("sudo rm -f /tmp/x"), "rm")
        self.assertEqual(first_word("env FOO=1 rm /tmp/x"), "rm")
        self.assertEqual(first_word("FOO=1 ./bin/run"), "run")
        self.assertEqual(first_word("for i in 1 2; do"), "")

    def test_fd_dup_is_not_a_separator_but_background_is(self):
        self.assertEqual(simple_commands("ls x 2>&1 | cat"), ["ls x", "cat"])
        self.assertEqual(simple_commands("ls &> /dev/null"), ["ls '>' /dev/null"])
        self.assertEqual(simple_commands("ls & rm -rf x"), ["ls", "rm -rf x"])

    def test_subshells_and_backticks_are_split(self):
        self.assertEqual(simple_commands("ls $(curl -s x)"), ["ls '$'", "curl -s x"])
        self.assertIn("curl -s x", simple_commands("ls `curl -s x`"))

    def test_writes_file(self):
        self.assertTrue(writes_file("echo hi > f"))
        self.assertTrue(writes_file("printf x >>f"))
        self.assertFalse(writes_file("ls 2>/dev/null"))
        self.assertFalse(writes_file("cat < /dev/null 2>&1"))

if __name__ == "__main__":
    unittest.main()
