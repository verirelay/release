# Contributing

## Checks

Run the checks of the CI `check` job locally:

```bash
uvx ruff@0.17.3 check scripts
uvx ruff@0.17.3 format --check scripts
shellcheck scripts/*.sh
actionlint
python3 scripts/release.py lock-env --lock versions.lock
python3 scripts/release.py check-pins --lock versions.lock  # needs an authenticated gh
python3 -m py_compile scripts/*.py
python3 scripts/launch-measurement.py --help
python3 scripts/pin_component.py --help
python3 scripts/release-notes.py --help
python3 scripts/release.py --help
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

## Tests

The Python tests use a public, deterministic test seed, not the Release Key:

```bash
uv run --with cryptography==46.0.5 python -m unittest discover -s scripts -p 'test_*.py' -v
```

`scripts/testdata/registry-vector.json` is a golden Build Record vector signed with the test seed.

Registry (`internal/buildrecord/testdata/`) and Verifier (`internal/testdata/`) keep verbatim copies that their own tests check. Update all three together.

## Repository rules

- `versions.lock` records choices and expectations only. Facts the component repositories already pin (kernel hashes, edk2 commit, apt closure) stay in those repositories.
- Automation is limited to lock changes and pull requests. Merging and publishing stay with humans.
- The following change together with the affected repositories (protocol, registry, verirelay, veriforge, verifier):
  - `chain.schema.json`
  - the `verirelay-release-chain/1` and `verirelay-launch-measurement/1` formats
  - the Build Record and revocation statements
  - the Guest Manifest contract
