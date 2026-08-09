# Security policy

## Supported version

Security fixes are provided for the latest published UG MOD HUB release only.

## Reporting a vulnerability

Do not publish credentials, tokens, private URLs, game encryption keys or a
working exploit in a public issue. Send a private report to the project owner
through the security contact listed on the official website. Include the
affected version, reproduction steps and the expected impact.

The maintainers will acknowledge a complete report as soon as practical and
will publish a fixed release after validation.

## Release integrity

- The catalog and update metadata are verified with a pinned public key.
- Downloaded mod files and updates are verified with SHA-256.
- Release executables must be built by the public GitHub Actions workflow.
- Once the SignPath Foundation project is approved, releases must also carry
  an Authenticode signature issued through the trusted build workflow.
- Private signing keys, IMG keys and server credentials are never stored in
  this repository or embedded as plaintext in the executable.

