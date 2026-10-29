English | [简体中文](registry.zh-CN.md)

# Registry publication

Publish mode signs one Build Record per Launch Measurement, uploads them as the `build-records.json` release asset, and registers them with Registry.

## Configuration

The secrets belong to the GitHub environment `release`, which must be restricted to the `main` branch. The publish, replay, and revoke jobs run in that environment.

| Secret | Value |
| --- | --- |
| `REGISTRY_URL` | HTTPS base URL of Registry |
| `REGISTRY_API_PWD` | plaintext password sent in `X-API-Key` |
| `REGISTRY_RELEASE_KEY` | standard padded base64 of the raw 32-byte Ed25519 seed of the Release Key |

When all three secrets are unset, publish and replay skip Registry with a notice, and no `build-records.json` is produced. A partial configuration or an invalid key fails the job.

The seed's public key must match Registry's `RELEASE_PUBLIC_KEY`. Registration and revocation check `/v1/public-key` before they send the password.

Registration runs **after** the GitHub release is created. A Registry failure fails the job but leaves the release published.

## Release Key

Generate a new pair locally. Store the seed output as the secret, never in the repository or CI logs.

```bash
openssl genpkey -algorithm ed25519 -out release-key.pem
echo "REGISTRY_RELEASE_KEY=$(openssl pkey -in release-key.pem -outform DER | tail -c 32 | openssl base64 -A)"
echo "RELEASE_PUBLIC_KEY=$(openssl pkey -in release-key.pem -pubout -outform DER | tail -c 32 | openssl base64 -A | tr '+/' '-_' | tr -d '=')"
```

A new pair does not match the key Registry already runs with. Keep the existing seed, or change Registry's `RELEASE_PUBLIC_KEY` together with this repository's `REGISTRY_RELEASE_KEY` secret.

## Build Record fields

Release signs a `verirelay-build-record/1` statement for each entry of `launch_measurements` in `chain.json`. Its canonical encoding and the bytes the signature covers are defined in Registry's [api.md](https://github.com/verirelay/registry/blob/main/docs/api.md), section "Signed writes".

| Field | Value |
| --- | --- |
| `repository` | `${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}` |
| `git_commit` | the release checkout HEAD |
| `git_tag` | the generated `release.tag` of `chain.json`, also the tag of the GitHub release |
| `measurement` | the bare lowercase SHA-384 Launch Measurement of one `launch_measurements` entry |
| `committed_at` | HEAD's committer timestamp in UTC, rendered as RFC3339 `YYYY-MM-DDTHH:MM:SSZ` |

`build-records.json` is a JSON array of Signed Build Records. Each record is `{"statement": {...}, "signature": "..."}`, with the Ed25519 signature as unpadded base64url.

If Registry already holds a Build Record with the same identity (HTTP 409), registration succeeds only when the stored record is identical. A different record fails.

## Replaying a registration

After fixing a registration failure, dispatch **registry-replay** with `release_tag`. It registers every record in the published release's `build-records.json` with its original signature.

Replay needs the `build-records.json` asset. If signing or the upload failed, create and upload it first. A release published without the Registry configuration has no asset to replay.

## Revoking a release

Dispatch **registry-revoke** with `release_tag` and `reason`. For each Build Record of that tag in Registry that is not revoked yet, it signs and posts a `verirelay-revocation/1` statement:

| Field | Value |
| --- | --- |
| `repository`, `git_commit`, `git_tag`, `measurement` | from the signed Build Record |
| `reason` | the `reason` input without surrounding whitespace, 1 to 1000 bytes |
| `revoked_at` | the time of the run in UTC, rendered as RFC3339 `YYYY-MM-DDTHH:MM:SSZ` |

Revocation requires all three secrets and fails when Registry has no Build Record for the tag.
