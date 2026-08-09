# Contributing

Use a separate branch and submit a pull request. Keep each change focused and
include tests for security-sensitive behavior. Never commit game archives,
commercial mod files, user data, tokens, passwords or private keys.

Before opening a pull request, run:

```powershell
py -3.12 -m unittest discover -s tests -v
```

Changes to `.github/workflows`, `.signpath`, dependency files, update logic,
signature verification, authentication, archive handling or privileged file
operations require maintainer review.

