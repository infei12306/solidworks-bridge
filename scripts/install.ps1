# install.ps1 - deploy the SOLIDWORKS bridge for DeepSeek Harness
#
# What it does:
#   1. finds a 64-bit Python that has pywin32
#   2. copies the driver into %LOCALAPPDATA%\swbridge  (override with SWBRIDGE_HOME)
#   3. generates the COM stubs from the SOLIDWORKS .tlb files
#   4. links this checkout into the DSH skills directory
#
# It never touches the SOLIDWORKS registry: this install registers its type
# libraries under the wrong version, which is exactly why the stubs are generated
# from the .tlb FILES instead of from the registry.
#
# This file is deliberately ASCII-only: Windows PowerShell 5.1 reads a BOM-less
# .ps1 as ANSI, and non-ASCII characters break the parser (a lesson already paid
# for on this machine).

[CmdletBinding()]
param(
    [string]$Python,
    [string]$SkillsDir = (Join-Path $env:USERPROFILE '.dsh\skills'),
    [switch]$NoStubs,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Repo 'sw\swbridge.py'))) {
    throw "cannot find sw\swbridge.py next to $PSScriptRoot - run this from the checkout"
}
$BridgeHome = if ($env:SWBRIDGE_HOME) { $env:SWBRIDGE_HOME } else { Join-Path $env:LOCALAPPDATA 'swbridge' }
$LinkName = 'solidworks-bridge'
$Link = Join-Path $SkillsDir $LinkName

if ($Uninstall) {
    if (Test-Path $Link) {
        # only remove the link itself; never the checkout it points at
        (Get-Item $Link -Force).Delete()
        Write-Output "removed link : $Link"
    } else {
        Write-Output "no link at   : $Link"
    }
    Write-Output "left in place: $BridgeHome (logs, shots, generated stubs)"
    Write-Output "               delete that folder by hand if you want a clean slate"
    return
}

function Resolve-Python {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "no such python: $Explicit" }
        return $Explicit
    }
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $found = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
            if ($found) { $candidates += $found.Trim() }
        } catch { }
    }
    foreach ($version in '311', '312', '313') {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe")
    }
    $candidates += (Get-Command python -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty Source)
    foreach ($candidate in $candidates) {
        if (-not $candidate -or -not (Test-Path $candidate)) { continue }
        $bits = & $candidate -c "import struct; print(struct.calcsize('P')*8)" 2>$null
        if ("$bits".Trim() -ne '64') { continue }
        & $candidate -c "import pythoncom, win32com.client" 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    throw ("no 64-bit Python with pywin32 found. Install it, then re-run:" + [Environment]::NewLine +
           "  & '<python.exe>' -m pip install pywin32")
}

$PythonExe = Resolve-Python -Explicit $Python
Write-Output "python       : $PythonExe"

# --- 2. deploy the driver ------------------------------------------------------
foreach ($sub in @('sw', 'scripts', 'stubs', 'tasks', 'logs', 'status', 'shots', 'out')) {
    $path = Join-Path $BridgeHome $sub
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}
Copy-Item (Join-Path $Repo 'sw\*.py') (Join-Path $BridgeHome 'sw') -Force
Copy-Item (Join-Path $Repo 'scripts\genstubs.py') (Join-Path $BridgeHome 'scripts') -Force
Write-Output "driver       : $(Join-Path $BridgeHome 'sw\swbridge.py')"

# --- 3. generate the stubs -----------------------------------------------------
if (-not $NoStubs) {
    & $PythonExe (Join-Path $BridgeHome 'scripts\genstubs.py')
    if ($LASTEXITCODE -ne 0) { throw "stub generation failed" }
} else {
    Write-Output "stubs        : skipped (-NoStubs)"
}

# --- 4. link it into the skills directory --------------------------------------
if (-not (Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }
if (Test-Path $Link) {
    $existing = Get-Item $Link -Force
    if ($existing.LinkType) {
        $existing.Delete()          # replace our own link, whatever it pointed at
    } else {
        throw "$Link exists and is a real directory - move it aside first"
    }
}
New-Item -ItemType Junction -Path $Link -Target $Repo | Out-Null
Write-Output "skill link   : $Link -> $Repo"

Write-Output ""
Write-Output "DRIVER : $PythonExe $(Join-Path $BridgeHome 'sw\swbridge.py')"
Write-Output "SKILL  : $Link\SKILL.md"
Write-Output "CHECK  : & '$PythonExe' '$(Join-Path $BridgeHome 'sw\swbridge.py')' doctor"
