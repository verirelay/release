# Security policy

## Reporting a vulnerability

Vulnerabilities are reported privately through GitHub private vulnerability reporting: the repository's **Security** tab → **Report a vulnerability**.

## Scope

The release machinery in this repository:

- `versions.lock` validation
- the build scripts
- `chain.json` generation
- provenance signing
- Build Record signing and revocation
- the workflow definitions

In scope:

- Anything that lets a published artifact diverge from the recorded commits or the `[expected]` baseline unnoticed.
- Bypasses of the dirty-tree, Guest Manifest-vs-lock, digest, or pin-reachability assertions.
- Any path that lets a workflow outside `main` reach the `release` environment secrets or the release signing identity.
- Provenance or verification-command inaccuracies that let a forged artifact pass verification.

Out of scope: vulnerabilities in the components themselves (their own repositories) and in third-party actions consumed at pinned SHAs.
