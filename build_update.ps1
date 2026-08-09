param(
    [string]$DistRoot = "",
    [string]$PythonBootstrap = ""
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $project ".venv"
if (-not $DistRoot) { $DistRoot = Join-Path $project "release_audio" }
$catalog = Get-Content -LiteralPath "$project\catalog.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$catalog.version
$output = Join-Path $DistRoot "UG MOD HUB UPDATE $version"
if (Test-Path -LiteralPath $output) { throw "Update folder already exists: $output" }
New-Item -ItemType Directory -Path $output | Out-Null

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

$releaseConfigDir = Join-Path $project ".build-tools\update"
New-Item -ItemType Directory -Path $releaseConfigDir -Force | Out-Null
$releaseConfig = Join-Path $releaseConfigDir "build_config.json"
Set-Content -LiteralPath $releaseConfig -Value '{"dev_mode": false}' -Encoding ASCII

$python = "$venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r "$project\requirements.txt" -r "$project\requirements-build.txt"
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name "UG MOD HUB" `
    --icon "$project\assets\ukraine_gta_app_icon.ico" `
    --distpath $output `
    --workpath (Join-Path $project "build\UG MOD HUB UPDATE $version") `
    --specpath $project `
    --add-data "$project\catalog.json;." `
    --add-data "$project\skin_index.json;." `
    --add-data "$project\accessory_index.json;." `
    --add-data "$project\weapon_index.json;." `
    --add-data "$releaseConfig;." `
    --add-data "$project\assets;assets" `
    --add-data "$project\tools;tools" `
    "$project\app.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
Copy-Item -LiteralPath "$project\README.md" -Destination "$output\README.md" -Force
Write-Host "Built update: $output\UG MOD HUB.exe"
