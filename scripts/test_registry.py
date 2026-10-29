import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import registry
from cryptography.exceptions import InvalidSignature

SEED = bytes(range(32))
ENV = {
    "REGISTRY_RELEASE_KEY": base64.b64encode(SEED).decode(),
    "REGISTRY_URL": "https://registry.example",
    "REGISTRY_API_PWD": "test-password",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "verirelay/release",
}
VECTOR = Path(__file__).parent / "testdata/registry-vector.json"
TAG = "rel-2026.10.27-1"


def chain_with(release, measurements):
    return {
        "release": release,
        "launch_measurements": [{"measurement": m} for m in measurements],
    }


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.vector = json.loads(VECTOR.read_text())
        self.record = self.vector["record"]
        self.public = {"public_key": self.vector["public_key"]}

    def stored(self, statement, revoked=False):
        payload = registry.canonical(statement)
        return {
            "statement": statement,
            "proof": {
                "signed_payload": registry.b64url(payload),
                "signature": registry.b64url(registry.private_key().sign(payload)),
            },
            "revoked": revoked,
        }

    def test_golden_signature_and_canonical_bytes(self):
        key = registry.private_key()
        self.assertEqual(
            registry.b64url(key.public_key().public_bytes_raw()),
            self.vector["public_key"],
        )
        self.assertEqual(
            self.record["statement"]["version"], registry.BUILD_RECORD_VERSION
        )
        payload = registry.canonical(self.record["statement"])
        self.assertEqual(payload.decode(), self.vector["canonical"])
        self.assertEqual(registry.b64url(key.sign(payload)), self.record["signature"])

    def test_b64url_decode_is_canonical(self):
        self.assertEqual(registry.b64url_decode("YQ"), b"a")
        for value in ("YQ==", "Y+/=", "YR", "Y"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry.b64url_decode(value)

    def test_one_record_per_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            chain = Path(directory) / "chain.json"
            release = {"mode": "publish", "tag": TAG}
            chain.write_text(json.dumps(chain_with(release, ("a" * 96, "c" * 96))))
            with patch.object(registry, "git", side_effect=["0", "b" * 40]) as git:
                records = registry.create_records("checkout", chain)
            self.assertEqual(
                [record["statement"] for record in records],
                [
                    {
                        "version": "verirelay-build-record/1",
                        "repository": "https://github.com/verirelay/release",
                        "git_commit": "b" * 40,
                        "git_tag": TAG,
                        "measurement": measurement,
                        "committed_at": "1970-01-01T00:00:00Z",
                    }
                    for measurement in ("a" * 96, "c" * 96)
                ],
            )
            public_key = registry.private_key().public_key()
            for record in records:
                public_key.verify(
                    registry.b64url_decode(record["signature"]),
                    registry.canonical(record["statement"]),
                )
            self.assertEqual(
                git.call_args_list[-1].args, ("checkout", "rev-parse", "HEAD")
            )
            chain.write_text(json.dumps(chain_with(release, ("sha384:" + "a" * 96,))))
            with self.assertRaisesRegex(ValueError, "bare lowercase"):
                registry.create_records("checkout", chain)

    def test_record_or_missing_tag_cannot_create_build_record(self):
        with tempfile.TemporaryDirectory() as directory:
            chain = Path(directory) / "chain.json"
            for release in (
                {"mode": "record"},
                {"mode": "publish"},
                {"mode": "publish", "tag": "invalid"},
            ):
                with self.subTest(release=release):
                    chain.write_text(json.dumps({"release": release}))
                    with self.assertRaisesRegex(ValueError, "published chain"):
                        registry.create_records("checkout", chain)

    def test_new_records(self):
        second = {
            "statement": {**self.record["statement"], "measurement": "c" * 96},
        }
        second["signature"] = registry.b64url(
            registry.private_key().sign(registry.canonical(second["statement"]))
        )
        with patch.object(
            registry,
            "request",
            side_effect=[(200, self.public), (201, {}), (201, {})],
        ) as request:
            registry.register(
                [self.record, second], ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
            )
        posts = request.call_args_list[1:]
        self.assertEqual(
            [call.args[2] for call in posts], [ENV["REGISTRY_API_PWD"]] * 2
        )
        self.assertEqual(
            [json.loads(call.args[1]) for call in posts], [self.record, second]
        )

    def test_identical_conflict(self):
        item = {
            "statement": self.record["statement"],
            "proof": {
                "signed_payload": registry.b64url(self.vector["canonical"].encode()),
                "signature": self.record["signature"],
            },
        }
        with patch.object(
            registry,
            "request",
            side_effect=[(200, self.public), (409, None), (200, {"items": [item]})],
        ):
            registry.register(
                [self.record], ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
            )
        item["statement"] = {**item["statement"], "git_commit": "c" * 40}
        with (
            patch.object(
                registry,
                "request",
                side_effect=[(200, self.public), (409, None), (200, {"items": [item]})],
            ),
            self.assertRaisesRegex(ValueError, "409.*differs"),
        ):
            registry.register(
                [self.record], ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
            )

    def run_main(self, *argv):
        with (
            patch("sys.argv", ["registry.py", *argv]),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            try:
                registry.main()
            except SystemExit as exit:
                return exit.code, output.getvalue(), error.getvalue()
        return 0, output.getvalue(), error.getvalue()

    def test_unconfigured_publication_skips_without_reading_asset(self):
        with patch.dict(os.environ, dict.fromkeys(registry.SECRETS, "")):
            code, output, _ = self.run_main("register", "--records", "missing.json")
        self.assertEqual(code, 0)
        self.assertIn("::notice::", output)

    def test_partial_configuration_fails(self):
        for name in registry.SECRETS:
            with (
                self.subTest(name=name),
                patch.dict(os.environ, {name: ""}),
                patch.object(registry, "register") as register,
            ):
                code, _, error = self.run_main("register", "--records", "missing.json")
                self.assertEqual(code, 1)
                self.assertIn(f"incomplete: {name} unset", error)
                register.assert_not_called()

    def test_revoke_requires_configuration(self):
        with (
            patch.dict(os.environ, dict.fromkeys(registry.SECRETS, "")),
            patch.object(registry, "revoke") as revoke,
        ):
            code, _, error = self.run_main("revoke", "--tag", TAG, "--reason", "x")
        self.assertEqual(code, 1)
        self.assertIn("::error::Registry revoke failed", error)
        revoke.assert_not_called()

    def test_invalid_seed(self):
        with (
            patch.dict(os.environ, {"REGISTRY_RELEASE_KEY": "YQ=="}),
            self.assertRaisesRegex(ValueError, "32-byte"),
        ):
            registry.private_key()

    def test_wrong_registry_key(self):
        with (
            patch.object(
                registry,
                "request",
                return_value=(200, {"public_key": registry.b64url(bytes(32))}),
            ),
            self.assertRaisesRegex(ValueError, "does not match"),
        ):
            registry.register(
                [self.record], ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
            )

    def test_post_failure(self):
        with (
            patch.object(
                registry,
                "request",
                side_effect=[(200, self.public), ValueError("Registry HTTP 503")],
            ),
            self.assertRaisesRegex(ValueError, "503"),
        ):
            registry.register(
                [self.record], ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
            )

    def test_revoke_every_record_of_the_tag(self):
        records = [
            {**self.record["statement"], "git_tag": TAG, "measurement": m}
            for m in ("a" * 96, "c" * 96, "d" * 96)
        ]
        items = [
            self.stored(records[0]),
            self.stored(records[1], revoked=True),
            self.stored(records[2]),
        ]
        now = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)
        with (
            patch.object(
                registry,
                "request",
                side_effect=[
                    (200, self.public),
                    (200, {"items": items}),
                    (201, {}),
                    (409, None),
                ],
            ) as request,
            patch.object(registry, "datetime", wraps=datetime) as clock,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            clock.now.return_value = now
            registry.revoke(
                TAG, "  key compromise \n", ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
            )
        self.assertEqual(
            request.call_args_list[1].args[0],
            "https://registry.example/v1/build-records/by-tag/rel-2026.10.27-1",
        )
        posts = request.call_args_list[2:]
        self.assertEqual(
            [call.args[0] for call in posts],
            ["https://registry.example/v1/revocations"] * 2,
        )
        public_key = registry.private_key().public_key()
        for call, record in zip(posts, (records[0], records[2]), strict=True):
            self.assertEqual(call.args[2], ENV["REGISTRY_API_PWD"])
            body = json.loads(call.args[1])
            self.assertEqual(
                body["statement"],
                {
                    "version": "verirelay-revocation/1",
                    "repository": record["repository"],
                    "git_commit": record["git_commit"],
                    "git_tag": TAG,
                    "measurement": record["measurement"],
                    "reason": "key compromise",
                    "revoked_at": "2026-10-29T12:30:00Z",
                },
            )
            public_key.verify(
                registry.b64url_decode(body["signature"]),
                registry.canonical(body["statement"], registry.REVOCATION_FIELDS),
            )

    def test_revoke_rejects_unverifiable_or_missing_records(self):
        record = {**self.record["statement"], "git_tag": TAG}
        forged = self.stored(record)
        forged["proof"]["signature"] = registry.b64url(bytes(64))
        for items, error, message in (
            ([], ValueError, "no Build Records"),
            ([forged], InvalidSignature, ""),
            (
                [self.stored({**record, "git_tag": "rel-2026.10.27-2"})],
                ValueError,
                "another tag",
            ),
        ):
            with (
                self.subTest(error=error, message=message),
                patch.object(
                    registry,
                    "request",
                    side_effect=[(200, self.public), (200, {"items": items})],
                ) as request,
                self.assertRaisesRegex(error, message),
            ):
                registry.revoke(
                    TAG, "reason", ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
                )
            self.assertEqual(request.call_count, 2)

    def test_revoke_validates_input(self):
        for tag, reason, error in (
            ("release/x", "reason", "rel-YYYY"),
            (TAG, " \t", "1 to 1000"),
            (TAG, "x" * 1001, "1 to 1000"),
        ):
            with (
                self.subTest(tag=tag, reason=reason[:8]),
                patch.object(registry, "request") as request,
                self.assertRaisesRegex(ValueError, error),
            ):
                registry.revoke(
                    tag, reason, ENV["REGISTRY_URL"], ENV["REGISTRY_API_PWD"]
                )
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
