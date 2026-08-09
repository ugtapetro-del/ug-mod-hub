# Code-signing policy

UG MOD HUB releases are built from the public source repository by a
GitHub-hosted Windows runner. Direct local signing is not permitted for public
releases.

After acceptance into SignPath Foundation:

1. A protected release tag starts the public GitHub Actions workflow.
2. Tests and the release build run on GitHub-hosted infrastructure.
3. The unsigned workflow artifact is submitted through the official SignPath
   GitHub integration with origin verification.
4. A designated approver reviews the request when required by the signing
   policy.
5. Only the returned signed artifact is published on the official website.

Repository maintainers must use multi-factor authentication. Changes to build
workflows, dependency declarations, signing configuration and security-critical
code require review before merge. The SignPath API token is stored only as a
GitHub Actions secret and cannot be printed to logs.

The signed artifact must not contain proprietary mod payloads, database or SMTP
credentials, signing keys, private IMG keys, or private server source code.

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

