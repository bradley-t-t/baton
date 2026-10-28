# Install Baton on Windows: irm https://taylorurl.com/baton.ps1 | iex
#
# Installs the baton command with pipx or uv (installing uv into your home folder if
# neither is there), removes an older pip install of Baton, adds the bin folder
# to your user PATH, then runs `baton setup` and `baton doctor`. Nothing is installed
# system-wide and administrator rights are never needed. Set
#
# Arguments for `baton setup` need the script as a script block, for example:
#   & ([scriptblock]::Create((irm https://taylorurl.com/baton.ps1))) --provider local --yes
# Environment: BATON_REF (branch, tag or commit; default main),
#              BATON_INSTALLER (pipx or uv, to choose one).
#
# Works in Windows PowerShell 5.1 and PowerShell 7. It runs inside your own session
# (that's what iex does), so everything is inside one script block that leaves no
# variables or functions behind, it never calls exit, and baton works in this window as
# soon as it finishes.

& {
$ErrorActionPreference = 'Stop'

$Repo = 'https://github.com/bradley-t-t/baton'
$Pkg = 'claude-baton'
# install.ps1 records the folder it adds to the user PATH here, so `baton uninstall
# --all` removes that entry and never one you or another tool added.
$MarkKey = 'HKCU:\Software\Baton'
$SetupArgs = @($args)

function Say([string]$Text) { Write-Host $Text }
function Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host 'ok  ' -ForegroundColor Green -NoNewline; Write-Host $Text }
function Note([string]$Text) { Write-Host 'note  ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Have([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# A terminal to ask questions in. irm | iex keeps the console as input, unlike curl | sh.
$Tty = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected

# Ask 'Question' $true -> $true for yes. Without a terminal the default is used.
function Ask([string]$Question, [bool]$Default) {
    if (-not $Tty) { return $Default }
    $hint = if ($Default) { '[Y/n]' } else { '[y/N]' }
    $reply = Read-Host "$Question $hint"
    if ($reply -match '^[Yy]') { return $true }
    if ($reply -match '^[Nn]') { return $false }
    return $Default
}

# Run a program in this console, so it can ask questions and show colors. Returns its
# exit code. (A program PowerShell runs with & inside a function may have its output
# captured, and then it can't tell it's talking to a terminal.)
function Invoke-Interactive([string]$Exe, [string[]]$Arguments) {
    # Windows' quoting rules: backslashes double before a quote and at the end.
    $quoted = foreach ($a in $Arguments) {
        if ($a -notmatch '[\s"]' -and $a -ne '') { $a; continue }
        '"' + (($a -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
    }
    $info = New-Object Diagnostics.ProcessStartInfo $Exe
    $info.UseShellExecute = $false
    $info.Arguments = $quoted -join ' '
    $process = [Diagnostics.Process]::Start($info)
    $process.WaitForExit()
    return $process.ExitCode
}

# Run a program and collect its output and exit code. Windows PowerShell 5.1 turns a
# program's stderr into errors, which 'Stop' would make fatal, so they're collected too.
function Invoke-Program([string]$Exe, [string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
    [pscustomobject]@{ Code = $code; Output = @($out) }
}

# Pythons that might hold an old pip install of Baton. The python.exe
# under WindowsApps can be the Store's placeholder, which opens the Store, so it's skipped.
function Get-Pythons {
    $candidates = @()
    if (Have 'py') {
        foreach ($line in (Invoke-Program 'py' @('-0p')).Output) {
            if ($line -match '([A-Za-z]:\\.*?pythonw?\.exe)\s*$') { $candidates += $Matches[1] }
        }
    }
    foreach ($name in 'python', 'python3') {
        foreach ($c in @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue)) {
            if ($c.Source -notmatch '\\WindowsApps\\') { $candidates += $c.Source }
        }
    }
    $seen = @{}
    foreach ($py in $candidates) {
        $r = Invoke-Program $py @('-c', 'import os,sys; print(os.path.realpath(sys.executable))')
        if ($r.Code -ne 0 -or -not $r.Output) { continue }
        $real = $r.Output[-1].Trim().ToLowerInvariant()
        if ($seen.ContainsKey($real)) { continue }
        $seen[$real] = $true
        $py
    }
}

# Names of this project's packages installed with pip in that Python. PyPI has an
# unrelated package called baton, so a name only counts when it was installed from a
# folder or git (direct_url.json) and its command runs baton.cli.
$OldFinder = @'
import sys
try:
    from importlib import metadata
except ImportError:
    sys.exit(0)
for name in ("baton", "claude-baton"):
    try:
        d = metadata.distribution(name)
    except Exception:
        continue
    eps = [e.value for e in d.entry_points]
    if any(v.startswith("baton.cli") for v in eps) and d.read_text("direct_url.json"):
        print(name)
'@

function Remove-OldInstall {
    Step 'Checking for an older install'
    $finder = Join-Path ([IO.Path]::GetTempPath()) ('baton-finder-{0}.py' -f [guid]::NewGuid())
    Set-Content -LiteralPath $finder -Value $OldFinder -Encoding ASCII
    $found = $false
    try {
        foreach ($py in @(Get-Pythons)) {
            $names = @((Invoke-Program $py @($finder)).Output | Where-Object { $_ -match '^[\w-]+$' })
            if (-not $names) { continue }
            $found = $true
            Say "Found $($names -join ' ') installed with pip in $py"
            $r = Invoke-Program $py (@('-m', 'pip', 'uninstall', '-y') + $names)
            if ($r.Code -eq 0) { Ok "Uninstalled $($names -join ' ')" }
            else { Note "Couldn't uninstall $($names -join ' '). Run: $py -m pip uninstall $($names -join ' ')" }
        }
    } finally {
        Remove-Item -LiteralPath $finder -ErrorAction SilentlyContinue
    }
    if (-not $found) { Ok 'None found' }
}

function Test-PipxHas { (Have 'pipx') -and ((Invoke-Program 'pipx' @('list', '--short')).Output -match "^$Pkg ") }
function Test-UvHas { (Have 'uv') -and ((Invoke-Program 'uv' @('tool', 'list', '--color', 'never')).Output -match "^$Pkg ") }

function Select-Installer {
    Step 'Choosing an installer'
    switch ($env:BATON_INSTALLER) {
        'pipx' { if (-not (Have 'pipx')) { throw "BATON_INSTALLER=pipx but pipx isn't installed" }; return 'pipx' }
        'uv' { if (-not (Have 'uv')) { Install-Uv }; return 'uv' }
        { $_ } { throw 'BATON_INSTALLER must be pipx or uv' }
    }
    if (Test-PipxHas) { $how = 'pipx' }
    elseif (Test-UvHas) { $how = 'uv' }
    elseif (Have 'pipx') { $how = 'pipx' }
    elseif (Have 'uv') { $how = 'uv' }
    else {
        Say 'Baton installs with pipx or uv, and neither is here.'
        Say 'uv is a single program from Astral. Its installer puts it in ~\.local\bin.'
        if (-not (Ask 'Install uv now?' $true)) { throw 'Install pipx or uv, then run this again.' }
        Install-Uv
        $how = 'uv'
    }
    Ok "Using $how"
    return $how
}

function Install-Uv {
    Say 'Installing uv from https://astral.sh/uv'
    # Astral's installer stops unless scripts may run, and exits when it fails, so it gets
    # its own PowerShell with the policy relaxed for that process only, as Astral suggests.
    $saved = $env:UV_NO_MODIFY_PATH
    $env:UV_NO_MODIFY_PATH = '1'  # Baton adds its own bin folder, the same one, below
    try {
        $shell = (Get-Process -Id $PID).Path
        $r = Invoke-Program $shell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "irm 'https://astral.sh/uv/install.ps1' | iex")
    } finally {
        $env:UV_NO_MODIFY_PATH = $saved
    }
    if ($r.Code -ne 0) { $r.Output | Write-Host; throw "uv's installer failed." }
    foreach ($d in @($env:XDG_BIN_HOME, (Join-Path $HOME '.local\bin'), (Join-Path $HOME '.cargo\bin'))) {
        if ($d -and (Test-Path -LiteralPath (Join-Path $d 'uv.exe'))) { $env:Path = "$d;$env:Path"; break }
    }
    if (-not (Have 'uv')) { throw "uv was installed but can't be found. Open a new terminal and run this again." }
    Ok "Installed $((Invoke-Program 'uv' @('--version')).Output)"
}

function Get-SourceUrl {
    $ref = if ($env:BATON_REF) { $env:BATON_REF } else { 'main' }
    if (Have 'git') { return "git+$Repo.git@$ref" }
    # No git: GitHub's tarball of the same ref works without it.
    return "$Repo/archive/$ref.tar.gz"
}

# The baton program in a bin folder: baton.exe on Windows (baton elsewhere, for tests).
function Find-Baton([string]$Dir) {
    foreach ($name in 'baton.exe', 'baton') {
        $p = Join-Path $Dir $name
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    }
    return $null
}

# baton\package.py runs these same install commands when Baton updates itself.
function Install-BatonPackage([string]$How) {
    $url = Get-SourceUrl
    Step 'Installing Baton'
    Say "from $url"
    if ($How -eq 'pipx') {
        if (Test-PipxHas) { Say "Baton is already installed with pipx, so it's reinstalled." }
        $r = Invoke-Program 'pipx' @('install', '--force', $url)
        if ($r.Code -ne 0) { $r.Output | Write-Host; throw 'pipx install failed.' }
        $bin = ((Invoke-Program 'pipx' @('environment', '--value', 'PIPX_BIN_DIR')).Output | Select-Object -Last 1)
        if (-not $bin) { $bin = Join-Path $HOME '.local\bin' }
    } else {
        if (Test-UvHas) { Say "Baton is already installed with uv, so it's reinstalled." }
        $r = Invoke-Program 'uv' @('tool', 'install', '--force', '--reinstall', $url)
        if ($r.Code -ne 0) { $r.Output | Write-Host; throw 'uv tool install failed.' }
        $bin = (Invoke-Program 'uv' @('tool', 'dir', '--bin', '--color', 'never')).Output | Select-Object -Last 1
    }
    $bin = "$bin".Trim()
    $baton = Find-Baton $bin
    if (-not $baton) { throw "Installed, but there's no baton program in $bin." }
    Ok "$((Invoke-Program $baton @('--version')).Output) in $bin"
    return $bin
}

# True when the ;-separated list holds the folder. Entries may use %VARIABLES% and a
# trailing backslash; case doesn't matter.
function Test-PathList([string]$List, [string]$Dir) {
    $want = [Environment]::ExpandEnvironmentVariables($Dir).TrimEnd('\', '/')
    foreach ($entry in ($List -split ';')) {
        $e = [Environment]::ExpandEnvironmentVariables($entry.Trim()).TrimEnd('\', '/')
        if ($e -and $e -ieq $want) { return $true }
    }
    return $false
}

# The user PATH as stored, with %VARIABLES% left unexpanded so writing it back keeps them.
function Get-UserPath {
    $key = Get-Item -LiteralPath 'HKCU:\Environment'
    return [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
}

function Set-UserPath([string]$Value) {
    Set-ItemProperty -LiteralPath 'HKCU:\Environment' -Name 'Path' -Value $Value -Type ExpandString
    # Setting a variable through .NET announces the change, so terminals opened from now
    # on see the new PATH. This one is set and removed again just for that.
    [Environment]::SetEnvironmentVariable('BATON_PATH_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('BATON_PATH_REFRESH', $null, 'User')
}

function Save-PathMark([string]$Dir) {
    New-Item -Path $MarkKey -Force | Out-Null
    Set-ItemProperty -LiteralPath $MarkKey -Name 'PathAdded' -Value $Dir
}

function Get-MachinePath { [string][Environment]::GetEnvironmentVariable('Path', 'Machine') }

# Put the bin folder on the user PATH, for new terminals, and on this session's PATH.
# Returns $true when the user PATH was changed.
function Add-BinToPath([string]$Bin) {
    if (-not (Test-PathList $env:Path $Bin)) { $env:Path = "$Bin;$env:Path" }
    $user = Get-UserPath
    if ((Test-PathList $user $Bin) -or (Test-PathList (Get-MachinePath) $Bin)) { return $false }
    Step 'PATH'
    $new = if ($user.Trim(';')) { "$Bin;$($user.TrimStart(';'))" } else { $Bin }
    Set-UserPath $new
    Save-PathMark $Bin
    Ok "Added $Bin to your user PATH"
    return $true
}

function Invoke-Setup([string]$Baton, [object[]]$Arguments) {
    if ($Tty -or $Arguments.Count -gt 0) {
        Step 'Setting up'
        if ((Invoke-Interactive $Baton (@('setup') + $Arguments)) -ne 0) { throw "baton setup didn't finish. Run ``baton setup`` to try again." }
        return $true
    }
    Note 'No terminal to ask questions in, so setup was skipped.'
    Say 'Finish with: baton setup'
    Say 'Or without questions, for example: baton setup --provider local --yes'
    return $false
}

function Install-Baton([object[]]$Arguments) {
    Write-Host 'Baton installer' -ForegroundColor White
    try {
        if (-not $Tty) { Note 'No terminal found, so nothing will be asked.' }
        Remove-OldInstall
        $how = Select-Installer
        $bin = Install-BatonPackage $how
        $baton = Find-Baton $bin
        $added = Add-BinToPath $bin
        if (Invoke-Setup $baton $Arguments) {
            Step 'Checking'
            Invoke-Interactive $baton @('doctor') | Out-Null
            Write-Host ''
            Write-Host 'Done. Start a new Claude Code session to use Baton.' -ForegroundColor White
            Say 'To remove it later: baton uninstall --all'
        }
        if ($added) {
            Write-Host ''
            Write-Host 'baton works in this window now. Terminals that were already open need to be reopened to find it.' -ForegroundColor White
        }
        $global:LASTEXITCODE = 0
    } catch {
        Write-Host 'error  ' -ForegroundColor Red -NoNewline
        Write-Host $_.Exception.Message
        $global:LASTEXITCODE = 1
    }
}

# BATON_INSTALL_TEST=<file> runs that file with these functions instead of installing,
# for the tests.
if ($env:BATON_INSTALL_TEST) { . $env:BATON_INSTALL_TEST } else { Install-Baton $SetupArgs }
} @args
