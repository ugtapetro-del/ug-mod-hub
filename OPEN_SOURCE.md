# UG MOD HUB — public client source

This repository contains the Windows desktop client and its reproducible build
scripts. It intentionally does **not** contain the private website/backend,
database configuration, SMTP credentials, code-signing private keys, game IMG
keys, user data, game archives or mod payloads.

## Build from source

Requirements: Windows 10/11 and Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\build.ps1 -ReleaseMode -DistRoot .\dist
```

The GitHub Actions workflow performs the same test and build process on a clean
GitHub-hosted Windows runner. It produces a folder release, not a user-facing
ZIP package.

## Why administrator access is requested

UKRAINE GTA may be installed in a protected Windows directory. UG MOD HUB must
write selected mod files, create backups and restore original files. It does not
use administrator access to weaken Windows security settings or install a
background driver.

## Network behavior

At startup the client retrieves lightweight signed metadata. A mod payload is
downloaded only after the user chooses **Install**. Downloads and updates are
checked against signed metadata and SHA-256 hashes.

The production API and website remain independently operated services. Their
private source and credentials are not required to inspect or reproduce the
desktop executable.

## Game archives

Archive replacement code operates only on files selected by an authorized user
inside that user's configured game installation. No game archive, model,
texture or encryption key is distributed in this repository. Users remain
responsible for complying with the game license and applicable law.

## Code signing

The project is being prepared for free open-source code signing through
SignPath Foundation. A signature proves publisher identity and detects changes
made after signing; it does not replace antivirus scanning or security review.

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

