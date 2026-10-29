English | [简体中文](provenance.zh-CN.md)

# Provenance and verification

Publish mode signs these files with cosign keyless signing:

- the four artifacts
- `build-report.json`
- `artifacts.lock`
- `chain.json`
- `SHA256SUMS`
- `launch-measurement.json`

`build-records.json` is signed by the Release Key instead. See [registry.md](registry.md).

## Cosign keyless

Each signed file ships with a `<file>.cosign.bundle` release asset.

```bash
cosign verify-blob \
  --bundle initrd.img.cosign.bundle \
  --certificate-identity-regexp '^https://github\.com/verirelay/release/\.github/workflows/release\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  initrd.img
```

Signatures are public in the **Rekor transparency log** (digest and signer identity only). Archived bundles and the certificate chain support fully offline verification.

## GitHub artifact attestation

Publish mode also creates native GitHub artifact attestations for the release assets. Cosign bundles are the primary mechanism.

## What the evidence proves

- The artifact bytes come from `release.yml` on `main` of this repository, the identity in the certificate.
- `chain.json` binds the four artifact digests and the Launch Measurements to the component commits and `versions.lock` (`lock_sha256`), so the Release Evidence Chain replays offline.
