param(
    [string]$DistRoot = "",
    [string]$PythonBootstrap = "",
    [switch]$ReleaseMode
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $project ".venv"
if (-not $DistRoot) { $DistRoot = Join-Path $project "dist" }
$catalog = Get-Content -LiteralPath "$project\catalog.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$releaseVersion = [string]$catalog.version
if ($releaseVersion -notmatch '^\d+\.\d+(\.\d+)?$') {
    throw "Invalid catalog version: $releaseVersion"
}
$embeddedVersionPath = Join-Path $project "build_version.py"
$embeddedVersionSource = @(
    '"""Generated build identity embedded into the packaged executable."""'
    ''
    "VERSION = `"$releaseVersion`""
) -join "`r`n"
Set-Content -LiteralPath $embeddedVersionPath -Value $embeddedVersionSource -Encoding UTF8
$buildName = if ($ReleaseMode) { "UG MOD HUB $releaseVersion" } else { "UG MOD HUB DEV $releaseVersion" }
$buildConfig = Join-Path $project "build_config.json"
if ($ReleaseMode) {
    $generatedConfigDir = Join-Path $project ".build-tools\release"
    New-Item -ItemType Directory -Path $generatedConfigDir -Force | Out-Null
    $buildConfig = Join-Path $generatedConfigDir "build_config.json"
    Set-Content -LiteralPath $buildConfig -Value '{"dev_mode": false}' -Encoding ASCII
}

if (-not (Test-Path -LiteralPath "$venv\Scripts\python.exe")) {
    if ($PythonBootstrap) {
        if (-not (Test-Path -LiteralPath $PythonBootstrap)) { throw "Python bootstrap was not found: $PythonBootstrap" }
        & $PythonBootstrap -m venv $venv
    }
    else {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
        & $pyLauncher.Source -3.12 -m venv $venv
        }
        else {
            $bootstrap = Get-Command python -ErrorAction SilentlyContinue
            if (-not $bootstrap) {
                throw "Python 3.12 is required to build UG MOD HUB. Install it from python.org or use the GitHub Actions workflow."
            }
            & $bootstrap.Source -m venv $venv
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the Python virtual environment." }
}

$python = "$venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r "$project\requirements.txt" -r "$project\requirements-build.txt"
& $python -m PyInstaller --noconfirm --clean --windowed --uac-admin --name "$buildName" `
    --icon "$project\assets\ukraine_gta_app_icon.ico" `
    --distpath "$DistRoot" `
    --add-data "$project\catalog.json;." `
    --add-data "$project\skin_index.json;." `
    --add-data "$project\accessory_index.json;." `
    --add-data "$project\weapon_index.json;." `
    --add-data "$buildConfig;." `
    --add-data "$project\assets;assets" `
    --add-data "$project\tools;tools" `
    "$project\app.py"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$output = Join-Path $DistRoot $buildName
$versionedExe = Join-Path $output "$buildName.exe"
$finalExe = Join-Path $output "UG MOD HUB.exe"
if (Test-Path -LiteralPath $versionedExe) {
    Move-Item -LiteralPath $versionedExe -Destination $finalExe -Force
}
Copy-Item -LiteralPath "$project\README.md" -Destination "$output\README.md" -Force
if ($ReleaseMode) {
    Set-Content -LiteralPath (Join-Path $output "release.lock") -Value '{"locked": true}' -Encoding ASCII
}
New-Item -ItemType Directory -Path "$output\mods" -Force | Out-Null
$modsSource = Join-Path $project "mods"
if (-not $ReleaseMode) {
    Get-ChildItem -LiteralPath $modsSource -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($modsSource.Length).TrimStart('\')
        $destination = Join-Path "$output\mods" $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        try {
            New-Item -ItemType HardLink -Path $destination -Target $_.FullName -Force | Out-Null
        }
        catch {
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }
}
if ($ReleaseMode) {
    $catalogById = [ordered]@{}
    foreach ($item in $catalog.mods) {
        $catalogById[[string]$item.id] = $item
    }
    $frozenCatalog = [ordered]@{
        app_name = [string]$catalog.app_name
        version = $releaseVersion
        mods = @($catalogById.Values)
    }
    $frozenCatalog | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $output "release_catalog.json") -Encoding UTF8
}
Write-Host "Built: $finalExe"
