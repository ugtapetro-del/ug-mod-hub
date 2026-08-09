# Release and SignPath Foundation checklist

## One-time setup

- Publish this sanitized client source in a public GitHub repository.
- Enable multi-factor authentication for maintainers.
- Protect the default branch and require review for workflow/security changes.
- Add a public security policy, privacy policy and code-signing policy.
- Apply at https://signpath.org/apply.html.
- In the application, point to the public source, public build workflow and
  latest release. Explain why administrator access and game-file changes are
  necessary.
- After acceptance, install the SignPath GitHub App and configure the project,
  artifact configuration and signing policy supplied by SignPath.
- Store `SIGNPATH_API_TOKEN` as a GitHub Actions secret. Store organization,
  project and policy identifiers as GitHub repository variables.

## Every release

- Update and test the source in the public repository.
- Confirm the dependency and secret scans pass.
- Create a protected version tag.
- Let GitHub Actions build the unsigned artifact.
- Submit that workflow artifact to SignPath; never upload a locally built EXE
  for a public signed release.
- Verify the returned Authenticode signature and SHA-256.
- Publish the signed files without repacking or modifying them.
- Update the website release metadata and hash.

Note: code signing verifies publisher identity and file integrity. Microsoft
SmartScreen reputation may still take time to build for a new certificate or
new release.

## Automatic source publication

`build_update.ps1` publishes the sanitized source to the public GitHub
repository after a successful local update build. Publication runs the test
suite first, creates a versioned commit and pushes `main`. Local mod payloads,
IMG archives, secrets, build folders and EXE files are excluded.

For a diagnostic build that must stay local, pass `-SkipSourcePublish`.
