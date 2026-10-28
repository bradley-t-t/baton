"""install.sh puts the bin folder on PATH in the login shell's startup files, and
`baton uninstall --all` takes that line out again."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton import package
from tests.helpers import posix_only

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"


@posix_only
class Harness(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.bin = str(self.home / ".local" / "bin")

    def check_path(self, shell, mac=False, orig_path="/usr/bin:/bin", **env):
        """Run install.sh's check_path for this login shell; returns its output."""
        script = ('. "%s"; %s BIN_DIR="%s"; ORIG_PATH="%s"; NEW_SHELL=""; check_path; '
                  'echo "NEW_SHELL=$NEW_SHELL"'
                  % (INSTALL_SH, "is_mac() { true; };" if mac else "is_mac() { false; };",
                     self.bin, orig_path))
        env.update({"HOME": str(self.home), "SHELL": shell, "PATH": "/usr/bin:/bin",
                    "BATON_INSTALL_FUNCTIONS": "1", "NO_COLOR": "1"})
        p = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def files(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")
                      if p.is_file())

    def path_in(self, shell_cmd, rc):
        """PATH after a shell reads this startup file."""
        p = subprocess.run(shell_cmd + ['. "%s"; printf %%s "$PATH"' % rc],
                           env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True)
        return p.stdout

class CheckPath(Harness):
    def test_zsh_gets_zshrc_not_profile(self):
        out = self.check_path("/bin/zsh")
        self.assertEqual(self.files(), [".zshrc"])
        self.assertIn("Added %s to PATH in %s/.zshrc" % (self.bin, self.home), out)
        self.assertIn("NEW_SHELL=1", out)
        rc = self.home / ".zshrc"
        self.assertTrue(rc.read_text().rstrip().endswith(package.PATH_MARK))
        self.assertTrue(self.path_in(["sh", "-c"], rc).startswith(self.bin + ":"))
        if shutil.which("zsh"):
            self.assertTrue(self.path_in(["zsh", "-f", "-c"], rc).startswith(self.bin + ":"))

    def test_zdotdir_is_respected(self):
        zdot = self.home / "zdot"
        out = self.check_path("/bin/zsh", ZDOTDIR=str(zdot))
        self.assertIn(str(zdot / ".zshrc"), out)
        self.assertEqual(self.files(), ["zdot/.zshrc"])

    def test_running_twice_adds_one_line(self):
        self.check_path("/bin/zsh")
        out = self.check_path("/bin/zsh")
        self.assertIn("already added", out)
        self.assertEqual((self.home / ".zshrc").read_text().count(package.PATH_MARK), 1)

    def test_nothing_when_already_on_path(self):
        out = self.check_path("/bin/zsh", orig_path="/usr/bin:%s:/bin" % self.bin)
        self.assertEqual(self.files(), [])
        self.assertIn("NEW_SHELL=\n", out)

    def test_bash_on_linux(self):
        self.check_path("/bin/bash")
        self.assertEqual(self.files(), [".bashrc"])
        (self.home / ".bashrc").unlink()
        (self.home / ".profile").write_text("# mine")
        self.check_path("/bin/bash")
        self.assertEqual(self.files(), [".bashrc", ".profile"])
        self.assertEqual((self.home / ".profile").read_text().splitlines()[0], "# mine")
        if shutil.which("bash"):
            self.assertTrue(self.path_in(["bash", "--norc", "-c"], self.home / ".bashrc")
                            .startswith(self.bin + ":"))

    def test_bash_on_mac_also_gets_a_login_file(self):
        self.check_path("/bin/bash", mac=True)
        self.assertEqual(self.files(), [".bash_profile", ".bashrc"])

    def test_fish_gets_its_own_file(self):
        self.check_path("/opt/homebrew/bin/fish")
        f = self.home / ".config" / "fish" / "conf.d" / "baton.fish"
        self.assertEqual(self.files(), [".config/fish/conf.d/baton.fish"])
        self.assertIn('set -gx PATH "%s" $PATH' % self.bin, f.read_text())

    def test_other_shells_get_profile(self):
        self.check_path("/bin/dash")
        self.assertEqual(self.files(), [".profile"])


class Uninstall(Harness):
    def test_removes_only_baton_lines(self):
        rc = self.home / ".zshrc"
        rc.write_text("export EDITOR=vim")  # no newline at the end
        self.check_path("/bin/zsh")
        self.check_path("/opt/homebrew/bin/fish")
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("ZDOTDIR", None)
            os.environ.pop("XDG_CONFIG_HOME", None)
            changed = package.remove_path_lines()
        self.assertEqual(sorted(changed), sorted([
            str(rc), str(self.home / ".config" / "fish" / "conf.d" / "baton.fish")]))
        self.assertEqual(rc.read_text(), "export EDITOR=vim\n")
        self.assertFalse((self.home / ".config" / "fish" / "conf.d" / "baton.fish").exists())


if __name__ == "__main__":
    unittest.main()
