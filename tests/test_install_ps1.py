"""install.ps1's own logic, run with PowerShell (pwsh) when it's installed. The script
runs a test file with its functions (BATON_INSTALL_TEST) instead of installing; the
registry is stood in for by plain variables."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "install.ps1"
PWSH = shutil.which("pwsh")

# Replaces the registry functions and prints what a test asks for as JSON.
FAKES = r'''
$script:UserPath = $env:FAKE_USER_PATH
$script:Marked = ''
function Get-UserPath { $script:UserPath }
function Set-UserPath([string]$Value) { $script:UserPath = $Value }
function Get-MachinePath { $env:FAKE_MACHINE_PATH }
function Save-PathMark([string]$Dir) { $script:Marked = $Dir }
'''


@unittest.skipUnless(PWSH, "needs pwsh")
class InstallPs1(unittest.TestCase):
    def run_ps(self, body: str, **env) -> dict:
        d = Path(tempfile.mkdtemp())
        test = d / "t.ps1"
        test.write_text(FAKES + body)
        full = dict(os.environ, BATON_INSTALL_TEST=str(test), FAKE_USER_PATH="",
                    FAKE_MACHINE_PATH="")
        full.update(env)
        p = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)],
                           capture_output=True, text=True, env=full, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def add(self, user="", machine="", session="/usr/bin"):
        return self.run_ps(r'''
$env:Path = $env:FAKE_SESSION
$a = Add-BinToPath 'C:\Users\t\.local\bin' 6>$null
$b = Add-BinToPath 'C:\Users\t\.local\bin' 6>$null
@{first=$a; second=$b; user=$script:UserPath; marked=$script:Marked; session=$env:Path} | ConvertTo-Json -Compress
''', FAKE_USER_PATH=user, FAKE_MACHINE_PATH=machine, FAKE_SESSION=session)

    def test_adds_the_folder_once_and_marks_it(self):
        r = self.add(user="%USERPROFILE%\\x;C:\\Python")
        self.assertEqual((r["first"], r["second"]), (True, False))
        self.assertEqual(r["user"], "C:\\Users\\t\\.local\\bin;%USERPROFILE%\\x;C:\\Python")
        self.assertEqual(r["marked"], "C:\\Users\\t\\.local\\bin")
        self.assertTrue(r["session"].startswith("C:\\Users\\t\\.local\\bin;"))
        self.assertEqual(r["session"].count(".local"), 1)

    def test_already_there_in_any_case_or_with_a_backslash(self):
        for user, machine in (("c:\\users\\t\\.local\\bin\\", ""), ("", "C:\\Users\\t\\.local\\bin")):
            r = self.add(user=user, machine=machine)
            self.assertFalse(r["first"])
            self.assertEqual(r["marked"], "")
            self.assertEqual(r["user"], user)

    def test_empty_user_path(self):
        self.assertEqual(self.add()["user"], "C:\\Users\\t\\.local\\bin")

    def test_source_url_follows_baton_ref(self):
        r = self.run_ps("@{u=(Get-SourceUrl)} | ConvertTo-Json -Compress", BATON_REF="dev")
        self.assertTrue(r["u"].endswith("baton.git@dev") or r["u"].endswith("/archive/dev.tar.gz"))

    def test_remove_old_follows_the_config(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "config.json"
        out = {}
        for value in ({"install": {"remove_old": False}}, {"install": {}}, None):
            if value is None:
                cfg.write_text("not json")
            else:
                cfg.write_text(json.dumps(value))
            out[str(value)] = self.run_ps("@{w=(Test-RemoveOldWanted)} | ConvertTo-Json -Compress",
                                          BATON_CONFIG=str(cfg))["w"]
        self.assertEqual(list(out.values()), [False, True, True])

    def test_program_arguments_keep_their_spaces(self):
        r = self.run_ps(r'''
$code = Invoke-Interactive 'python3' @('-c', 'import sys; sys.exit(0 if sys.argv[1:] == ["a b", "", "c\"d"] else 3)', 'a b', '', 'c"d')
@{code=$code} | ConvertTo-Json -Compress
''')
        self.assertEqual(r["code"], 0)


if __name__ == "__main__":
    unittest.main()
