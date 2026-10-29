import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import release
import release_tag

spec = importlib.util.spec_from_file_location(
    "release_notes", Path(__file__).with_name("release-notes.py")
)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


class ReleaseTagTests(unittest.TestCase):
    def test_empty_repository_starts_at_one(self):
        self.assertEqual(
            release_tag.choose_tag([], [], datetime(2026, 10, 27, tzinfo=UTC)),
            "rel-2026.10.27-1",
        )

    def test_counts_distinct_releases_and_tags_for_date(self):
        releases = [{"tag_name": "rel-2026.10.27-1"}, {"tag_name": "other"}]
        tags = [
            {"name": value}
            for value in ("rel-2026.10.27-1", "rel-2026.10.27-2", "rel-2026.10.26-3")
        ]
        self.assertEqual(
            release_tag.choose_tag(releases, tags, datetime(2026, 10, 27, tzinfo=UTC)),
            "rel-2026.10.27-3",
        )

    def test_date_uses_utc_at_day_boundary(self):
        now = datetime(2026, 10, 28, 1, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(release_tag.choose_tag([], [], now), "rel-2026.10.27-1")

    def test_sparse_suffix_collision_fails_instead_of_reusing_tag(self):
        tags = [{"name": f"rel-2026.10.27-{n}"} for n in (1, 3)]
        with self.assertRaisesRegex(ValueError, "already exists"):
            release_tag.choose_tag([], tags, datetime(2026, 10, 27, tzinfo=UTC))

    def test_reads_every_api_page(self):
        pages = [[{"name": str(n)} for n in range(100)], [{"name": "last"}]]
        with patch.object(
            release_tag.subprocess, "check_output", return_value=json.dumps(pages)
        ) as get:
            items = release_tag.list_items("owner/repo", "tags")
        self.assertEqual(len(items), 101)
        self.assertEqual(items[-1], {"name": "last"})
        self.assertEqual(
            get.call_args.args[0],
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                "repos/owner/repo/tags?per_page=100",
            ],
        )

    def test_api_failure_does_not_generate_a_tag(self):
        with (
            patch("sys.argv", ["release_tag.py", "--repo", "owner/repo"]),
            patch.object(
                release_tag.subprocess,
                "check_output",
                side_effect=subprocess.CalledProcessError(1, "gh"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            release_tag.main()
        self.assertEqual(output.getvalue(), "")

    def test_main_uses_both_resources(self):
        with (
            patch("sys.argv", ["release_tag.py", "--repo", "owner/repo"]),
            patch.object(release_tag, "list_items", return_value=[]) as get,
            patch.object(release_tag, "datetime") as clock,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            clock.now.return_value = datetime(2026, 10, 27, tzinfo=UTC)
            release_tag.main()
        self.assertEqual(
            [call.args for call in get.call_args_list],
            [("owner/repo", "releases"), ("owner/repo", "tags")],
        )
        self.assertEqual(output.getvalue(), "rel-2026.10.27-1\n")

    def test_main_reports_collision_without_output(self):
        with (
            patch("sys.argv", ["release_tag.py", "--repo", "owner/repo"]),
            patch.object(release_tag, "list_items", return_value=[]),
            patch.object(
                release_tag, "choose_tag", side_effect=ValueError("already exists")
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as error,
            self.assertRaises(SystemExit),
        ):
            release_tag.main()
        self.assertEqual(output.getvalue(), "")
        self.assertIn("already exists", error.getvalue())


class ReleaseMetadataTests(unittest.TestCase):
    def args(self, mode, tag=""):
        return argparse.Namespace(
            mode=mode,
            tag=tag,
            lock=str(Path(__file__).parents[1] / "versions.lock"),
            run_url="https://example.com/run",
        )

    def test_record_chain_has_no_tag(self):
        metadata = release._chain_release(self.args("record"))
        self.assertEqual(set(metadata), {"mode", "lock_sha256", "run_url"})

    def test_publish_chain_uses_generated_tag(self):
        metadata = release._chain_release(self.args("publish", "rel-2026.10.27-2"))
        self.assertEqual(metadata["tag"], "rel-2026.10.27-2")

    def test_publish_chain_rejects_empty_or_invalid_tag(self):
        for tag in ("", "old-name", "rel-2026.10.27-0"):
            with (
                self.subTest(tag=tag),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                release._chain_release(self.args("publish", tag))

    def test_chain_validation_requires_publish_tag(self):
        chain = {
            "version": release.CHAIN_VERSION,
            "release": {"mode": "publish"},
            "components": {},
            "launch": {},
            "artifacts": {},
            "provenance": {},
        }
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            release._validate_chain_required_fields(chain)
        chain["release"]["tag"] = "rel-2026.10.27-1"
        release._validate_chain_required_fields(chain)
        chain["release"] = {"mode": "record"}
        release._validate_chain_required_fields(chain)

    def test_changes_label_previous_release(self):
        for metadata, label in (
            ({"tag": "rel-2026.10.27-1"}, "rel-2026.10.27-1"),
            ({}, "previous release"),
            ({"tag": ""}, "previous release"),
        ):
            with self.subTest(metadata=metadata):
                previous = {
                    "release": {"mode": "publish", **metadata},
                    "components": {},
                    "artifacts": {},
                }
                self.assertIn(
                    f"Compared with `{label}`:",
                    notes.section_changes(previous, previous),
                )

    def test_notes_render_record_and_publish(self):
        for metadata, title in (
            ({"mode": "record"}, "Record build"),
            ({"mode": "publish", "tag": "rel-2026.10.27-1"}, "rel-2026.10.27-1"),
        ):
            with (
                self.subTest(mode=metadata["mode"]),
                patch.object(notes, "section_identity", return_value="") as identity,
                patch.object(notes, "section_measurement", return_value=""),
                patch.object(notes, "section_artifacts", return_value=""),
                patch.object(notes, "section_verify", return_value=""),
            ):
                text = notes._render_release_notes(
                    {"release": metadata},
                    argparse.Namespace(repo="owner/repo"),
                    None,
                )
                self.assertTrue(text.startswith(f"# {title}\n"))
                identity.assert_called_once()
            chain = {
                "release": {**metadata, "lock_sha256": "test"},
                "components": {
                    key: {"commit": "test"}
                    for key in ("verirelay", "veriforge", "ovmf")
                },
            }
            self.assertIn(f"`{title}`", notes.section_identity(chain))


class PublishWorkflowTests(unittest.TestCase):
    def run_publish(self, tag, collision=False):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/release.yml").read_text()
        step = workflow.split("      - name: publish release\n", 1)[1].split(
            "      - name:", 1
        )[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "release").mkdir()
            for command, source in {
                "date": "#!/bin/sh\necho 2026.10.27\n",
                "git": "#!/bin/sh\necho checkout-commit\n",
                "gh": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nif [ "$1" = api ] && [ "$COLLISION" = 1 ]; then exit 1; fi\n',
            }.items():
                path = base / command
                path.write_text(source)
                path.chmod(0o755)
            log = base / "calls"
            result = subprocess.run(
                ["bash", "-c", script],
                check=False,
                cwd=base,
                env={
                    **os.environ,
                    "PATH": f"{base}:{os.environ['PATH']}",
                    "RELEASE_TAG": tag,
                    "GITHUB_REPOSITORY": "owner/repo",
                    "OUT": str(base),
                    "CALLS": str(log),
                    "COLLISION": "1" if collision else "0",
                },
                capture_output=True,
                text=True,
            )
            calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    def test_publish_creates_tag_at_checkout_before_release(self):
        result, calls = self.run_publish("rel-2026.10.27-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "git/refs -f ref=refs/tags/rel-2026.10.27-1 -f sha=checkout-commit",
            calls[0],
        )
        self.assertIn(
            "release create rel-2026.10.27-1 --repo owner/repo --verify-tag", calls[1]
        )

    def test_tag_collision_stops_before_release_creation(self):
        result, calls = self.run_publish("rel-2026.10.27-1", collision=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_utc_rollover_stops_before_tag_creation(self):
        result, calls = self.run_publish("rel-2026.10.26-1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UTC publish date changed", result.stderr)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
