"""Create, register, and revoke Registry Signed Build Records."""

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BUILD_RECORD_VERSION = "verirelay-build-record/1"
REVOCATION_VERSION = "verirelay-revocation/1"
BUILD_RECORD_FIELDS = (
    "version",
    "repository",
    "git_commit",
    "git_tag",
    "measurement",
    "committed_at",
)
REVOCATION_FIELDS = (
    "version",
    "repository",
    "git_commit",
    "git_tag",
    "measurement",
    "reason",
    "revoked_at",
)
SECRETS = ("REGISTRY_URL", "REGISTRY_API_PWD", "REGISTRY_RELEASE_KEY")
TAG_RE = re.compile(r"rel-\d{4}\.\d{2}\.\d{2}-[1-9]\d*")
B64URL_RE = re.compile(r"[A-Za-z0-9_-]*")


def canonical(statement, fields=BUILD_RECORD_FIELDS):
    # Go encoding/json escapes HTML and these two Unicode line separators.
    value = json.dumps(
        {key: statement[key] for key in fields},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    for char in "<>&\u2028\u2029":
        value = value.replace(char, f"\\u{ord(char):04x}")
    return value.encode()


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(value):
    if not B64URL_RE.fullmatch(value) or len(value) % 4 == 1:
        raise ValueError("value is not unpadded base64url")
    data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if b64url(data) != value:
        raise ValueError("value is not canonical base64url")
    return data


def utc_timestamp(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def private_key():
    seed = base64.b64decode(os.environ["REGISTRY_RELEASE_KEY"], validate=True)
    if len(seed) != 32:
        raise ValueError(
            "REGISTRY_RELEASE_KEY must be base64 of a 32-byte Ed25519 seed"
        )
    return Ed25519PrivateKey.from_private_bytes(seed)


def git(checkout, *args):
    return subprocess.check_output(["git", "-C", checkout, *args], text=True).strip()


def create_records(checkout, chain):
    """Sign one Build Record per launch measurement of a published chain.json."""
    evidence = json.loads(Path(chain).read_text())
    tag = evidence["release"].get("tag", "")
    if evidence["release"]["mode"] != "publish" or not TAG_RE.fullmatch(tag):
        raise ValueError("Build Records require a published chain with a release tag")
    measurements = [entry["measurement"] for entry in evidence["launch_measurements"]]
    if not measurements:
        raise ValueError("chain has no launch measurements")
    for measurement in measurements:
        if not re.fullmatch(r"[0-9a-f]{96}", measurement):
            raise ValueError("chain launch measurement must be bare lowercase 96-hex")
    committed_at = utc_timestamp(
        datetime.fromtimestamp(
            int(git(checkout, "show", "-s", "--format=%ct", "HEAD")), UTC
        )
    )
    commit = git(checkout, "rev-parse", "HEAD")
    repository = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
    key = private_key()
    records = []
    for measurement in measurements:
        statement = dict(
            zip(
                BUILD_RECORD_FIELDS,
                (
                    BUILD_RECORD_VERSION,
                    repository,
                    commit,
                    tag,
                    measurement,
                    committed_at,
                ),
                strict=True,
            )
        )
        records.append(
            {
                "statement": statement,
                "signature": b64url(key.sign(canonical(statement))),
            }
        )
    return records


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Registry redirects are not allowed")


def request(url, body=None, password=None):
    headers = {"Accept": "application/json", "Cache-Control": "no-cache"}
    if password is not None:
        headers["X-API-Key"] = password
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 409:
            return 409, None
        raise ValueError(f"Registry HTTP {error.code}") from None


def registry_base(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "REGISTRY_URL must be an HTTPS base URL without credentials, query or fragment"
        )
    return url.rstrip("/")


def check_registry_key(base, public_key):
    _, info = request(base + "/v1/public-key")
    if b64url_decode(info["public_key"]) != public_key.public_bytes_raw():
        raise ValueError("Release Key does not match Registry public key")


def by_tag(base, tag):
    _, found = request(
        base + "/v1/build-records/by-tag/" + urllib.parse.quote(tag, safe="")
    )
    return found["items"]


def register(records, url, password):
    base = registry_base(url)
    public_key = private_key().public_key()
    signed = []
    for record in records:
        payload = canonical(record["statement"])
        signature = b64url_decode(record["signature"])
        public_key.verify(signature, payload)
        signed.append((record, payload, signature))
    check_registry_key(base, public_key)
    for record, payload, signature in signed:
        statement = record["statement"]
        status, _ = request(
            base + "/v1/build-records", json.dumps(record).encode(), password
        )
        if status == 201:
            print(f"Registry build record registered: {statement['measurement']}")
            continue
        if status != 409:
            raise ValueError(f"unexpected Registry POST status: {status}")
        if not any(
            item["statement"] == statement
            and b64url_decode(item["proof"]["signed_payload"]) == payload
            and b64url_decode(item["proof"]["signature"]) == signature
            for item in by_tag(base, statement["git_tag"])
        ):
            raise ValueError(
                "Registry 409: existing record differs from the signed release asset"
            )
        print(
            f"Registry already contains the identical build record: {statement['measurement']}"
        )


def revoke(tag, reason, url, password):
    """Sign and post a revocation for every Build Record of a release tag."""
    if not TAG_RE.fullmatch(tag):
        raise ValueError("release tag must be rel-YYYY.MM.DD-N")
    reason = reason.strip()
    if not reason or len(reason.encode()) > 1000:
        raise ValueError("reason must contain 1 to 1000 bytes")
    base = registry_base(url)
    key = private_key()
    check_registry_key(base, key.public_key())
    items = by_tag(base, tag)
    if not items:
        raise ValueError(f"Registry has no Build Records for {tag}")
    revoked_at = utc_timestamp(datetime.now(UTC))
    for item in items:
        # Revoke the identity the Release Key signed, not the unsigned copy.
        payload = b64url_decode(item["proof"]["signed_payload"])
        key.public_key().verify(b64url_decode(item["proof"]["signature"]), payload)
        record = json.loads(payload)
        if record["version"] != BUILD_RECORD_VERSION or record["git_tag"] != tag:
            raise ValueError("Registry returned a Build Record for another tag")
        if item["revoked"]:
            print(f"Build Record already revoked: {record['measurement']}")
            continue
        statement = {
            "version": REVOCATION_VERSION,
            "repository": record["repository"],
            "git_commit": record["git_commit"],
            "git_tag": record["git_tag"],
            "measurement": record["measurement"],
            "reason": reason,
            "revoked_at": revoked_at,
        }
        body = {
            "statement": statement,
            "signature": b64url(key.sign(canonical(statement, REVOCATION_FIELDS))),
        }
        status, _ = request(
            base + "/v1/revocations", json.dumps(body).encode(), password
        )
        if status == 409:
            print(f"Build Record already revoked: {record['measurement']}")
        elif status == 201:
            print(f"Build Record revoked: {record['measurement']}")
        else:
            raise ValueError(f"unexpected Registry POST status: {status}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--checkout", required=True)
    create.add_argument("--chain", required=True)
    create.add_argument("--out", required=True)
    post = sub.add_parser("register")
    post.add_argument("--records", required=True)
    withdraw = sub.add_parser("revoke")
    withdraw.add_argument("--tag", required=True)
    withdraw.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, KeyError, OSError, binascii.Error, InvalidSignature) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        hint = (
            "; dispatch registry-replay after fixing it"
            if args.command == "register"
            else ""
        )
        print(
            f"::error::Registry {args.command} failed: {detail}{hint}", file=sys.stderr
        )
        sys.exit(1)


def run(args):
    unset = [name for name in SECRETS if not os.environ.get(name)]
    if len(unset) == len(SECRETS) and args.command != "revoke":
        print(
            "::notice::Registry publication skipped: REGISTRY_URL, REGISTRY_API_PWD and REGISTRY_RELEASE_KEY are unset"
        )
        return
    if unset:
        raise ValueError(
            f"Registry configuration is incomplete: {', '.join(unset)} unset"
        )
    if args.command == "create":
        records = create_records(args.checkout, args.chain)
        Path(args.out).write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n"
        )
    elif args.command == "register":
        register(
            json.loads(Path(args.records).read_text()),
            os.environ["REGISTRY_URL"],
            os.environ["REGISTRY_API_PWD"],
        )
    else:
        revoke(
            args.tag,
            args.reason,
            os.environ["REGISTRY_URL"],
            os.environ["REGISTRY_API_PWD"],
        )


if __name__ == "__main__":
    main()
