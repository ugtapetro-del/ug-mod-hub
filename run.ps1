$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $project ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython "$project\app.py"
    exit $LASTEXITCODE
}

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3.12 "$project\app.py"
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python 3.12 is not installed." }
& $python.Source "$project\app.py"
