"""How Baton was installed, and the commands that reinstall or remove it."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton import package


def prefix_with(marker: str) -> str:
    prefix = Path(tempfile.mkdtemp()) / "claude-baton"
    prefix.mkdir()
    (prefix / marker).write_text("")
    return str(prefix)


class Installer(unittest.TestCase):
    def test_pipx_and_uv_are_recognised(self):
        for marker, want in (("pipx_metadata.json", "pipx"), ("uv-receipt.toml", "uv")):
            with mock.patch("sys.prefix", prefix_with(marker)):
                self.assertEqual(package.installer(), want)
        with mock.patch("sys.prefix", tempfile.mkdtemp()):
            self.assertEqual(package.installer(), "")

    def test_tool_is_found_outside_path(self):
        home = Path(tempfile.mkdtemp())
        (home / ".local" / "bin").mkdir(parents=True)
        uv = home / ".local" / "bin" / ("uv" if os.name != "nt" else "uv.exe")
        uv.write_text("")
        uv.chmod(0o755)
        with mock.patch.dict("os.environ", {"HOME": str(home), "USERPROFILE": str(home),
                                            "PATH": "/nowhere"}), \
                mock.patch("baton.package.TOOL_DIRS", ("~/.local/bin",)):
            self.assertEqual(package.tool("uv"), str(uv))
            self.assertEqual(package.tool("pipx"), "")
            self.assertEqual(package.search_path(), "/nowhere" + os.pathsep + str(uv.parent))


class Upgrade(unittest.TestCase):
    def test_reinstalls_from_main_with_the_same_tool(self):
        with mock.patch("baton.package.tool", side_effect=lambda n: "/bin/" + n), \
                mock.patch("shutil.which", return_value="/usr/bin/git"):
            with mock.patch("sys.prefix", prefix_with("pipx_metadata.json")):
                self.assertEqual(package.upgrade_command(), [
                    "/bin/pipx", "install", "--force",
                    "git+https://github.com/bradley-t-t/baton.git@main"])
            with mock.patch("sys.prefix", prefix_with("uv-receipt.toml")):
                self.assertEqual(package.upgrade_command()[:5], [
                    "/bin/uv", "tool", "install", "--force", "--reinstall"])

    def test_tarball_without_git(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(package.source(),
                             "https://github.com/bradley-t-t/baton/archive/main.tar.gz")

    def test_nothing_without_pipx_or_uv(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()):
            self.assertEqual(package.upgrade_command(), [])
        with mock.patch("sys.prefix", prefix_with("uv-receipt.toml")), \
                mock.patch("baton.package.tool", return_value=""):
            self.assertEqual(package.upgrade_command(), [])


class Removal(unittest.TestCase):
    def test_pipx_and_uv(self):
        for marker, want in (("pipx_metadata.json", ["pipx", "uninstall", "claude-baton"]),
                             ("uv-receipt.toml", ["uv", "tool", "uninstall", "claude-baton"])):
            with mock.patch("sys.prefix", prefix_with(marker)):
                self.assertEqual(package.removal_command(), want)

    def test_pip_install_uses_pip(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()), \
                mock.patch("baton.package._installed_name", return_value="claude-baton"):
            cmd = package.removal_command()
        self.assertEqual(cmd[1:], ["-m", "pip", "uninstall", "-y", "claude-baton"])

    def test_not_installed_gives_nothing(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()), \
                mock.patch("baton.package._installed_name", return_value=""):
            self.assertEqual(package.removal_command(), [])


if __name__ == "__main__":
    unittest.main()
