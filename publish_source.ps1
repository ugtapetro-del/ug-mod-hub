param(
    [string]$PublicRepository = "",
    [string]$CommitMessage = ""
)

$ErrorActionPreference = "Stop"
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspace = Split-Path -Parent $source
if (-not $PublicRepository) {
    $PublicRepository = Join-Path $workspace "UG MOD HUB OPEN SOURCE 0.1.7"
}

$source = [System.IO.Path]::GetFullPath($source)
$PublicRepository = [System.IO.Path]::GetFullPath($PublicRepository)
if (-not (Test-Path -LiteralPath (Join-Path $PublicRepository ".git"))) {
    throw "Public Git repository was not found: $PublicRepository"
}

$expectedRemote = "https://github.com/ugtapetro-del/ug-mod-hub.git"
$actualRemote = (& git -C $PublicRepository remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRemote -ne $expectedRemote) {
    throw "Unexpected Git remote: $actualRemote"
}

$initialChanges = @(& git -C $PublicRepository status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "Could not read Git status." }
if ($initialChanges.Count -gt 0) {
    throw "The public repository contains uncommitted changes. Commit or discard them before automatic publication."
}

$publicFiles = @(
    ".gitignore",
    "accessory_index.json",
    "app.py",
    "build.ps1",
    "build_config.json",
    "build_update.ps1",
    "build_version.py",
    "catalog.json",
    "CODE_SIGNING_POLICY.md",
    "CONTRIBUTING.md",
    "core.py",
    "fastman_img.py",
    "LICENSE",
    "OPEN_SOURCE.md",
    "PRIVACY.md",
    "publish_source.ps1",
    "README.md",
    "RELEASE_CHECKLIST.md",
    "requirements-build.txt",
    "requirements.txt",
    "run.ps1",
    "SECURITY.md",
    "skin_index.json",
    "THIRD_PARTY_NOTICES.md",
    "weapon_index.json"
)
$publicDirectories = @(".github", "assets", "tests", "tools")

foreach ($relative in $publicFiles) {
    $from = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $from -PathType Leaf)) {
        throw "Required public source file is missing: $relative"
    }
    Copy-Item -LiteralPath $from -Destination (Join-Path $PublicRepository $relative) -Force
}
foreach ($relative in $publicDirectories) {
    $from = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $from -PathType Container)) {
        throw "Required public source directory is missing: $relative"
    }
    Copy-Item -LiteralPath $from -Destination $PublicRepository -Recurse -Force
}

& git -C $PublicRepository add -- $publicFiles $publicDirectories
if ($LASTEXITCODE -ne 0) { throw "Git could not stage the public source files." }

$changes = @(& git -C $PublicRepository status --porcelain)
if ($changes.Count -eq 0) {
    Write-Host "Public repository is already up to date."
    exit 0
}

$venvPython = Join-Path $source ".venv\Scripts\python.exe"
$pythonCommand = if (Test-Path -LiteralPath $venvPython) { $null } else { Get-Command py -ErrorAction SilentlyContinue }
if (-not $pythonCommand -and -not (Test-Path -LiteralPath $venvPython)) { $pythonCommand = Get-Command python -ErrorAction SilentlyContinue }
if (-not $pythonCommand -and -not (Test-Path -LiteralPath $venvPython)) { throw "Python was not found; tests cannot be run before publication." }
$pythonPath = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { $pythonCommand.Source }
$pythonIsLauncher = -not (Test-Path -LiteralPath $venvPython) -and $pythonCommand.Name -eq "py.exe"
$testExitCode = 1
Push-Location $PublicRepository
try {
    if ($pythonIsLauncher) {
        & $pythonPath -3.12 -m unittest discover -s tests -v
    }
    else {
        & $pythonPath -m unittest discover -s tests -v
    }
    $testExitCode = $LASTEXITCODE
}
catch {
    $testExitCode = 1
    Write-Error $_
}
finally {
    Pop-Location
}
if ($testExitCode -ne 0) {
    & git -C $PublicRepository restore --staged --worktree -- $publicFiles $publicDirectories
    & git -C $PublicRepository clean -fd -- $publicFiles $publicDirectories
    throw "Tests failed. The update was not published."
}

$version = [string]((Get-Content -LiteralPath (Join-Path $PublicRepository "catalog.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version)
if (-not $CommitMessage) { $CommitMessage = "Publish UG MOD HUB $version update" }
& git -C $PublicRepository commit -m $CommitMessage
if ($LASTEXITCODE -ne 0) { throw "Git commit failed." }
& git -C $PublicRepository push origin main
if ($LASTEXITCODE -ne 0) { throw "GitHub publication failed. The local commit was preserved and can be pushed later." }

Write-Host "Published source to $expectedRemote"
