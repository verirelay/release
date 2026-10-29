"""Choose a publish tag from the UTC date and existing GitHub releases and tags."""

import argparse
import json
import subprocess
from datetime import UTC, datetime


def choose_tag(releases, tags, now):
    prefix = now.astimezone(UTC).strftime("rel-%Y.%m.%d-")
    existing = {item["tag_name"] for item in releases} | {item["name"] for item in tags}
    count = sum(tag.startswith(prefix) for tag in existing)
    tag = f"{prefix}{count + 1}"
    if tag in existing:
        raise ValueError(f"candidate tag {tag} already exists; review same-day tags")
    return tag


def list_items(repository, resource):
    pages = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/{resource}?per_page=100",
            ],
            text=True,
        )
    )
    return [item for page in pages for item in page]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub owner/repository")
    args = parser.parse_args()
    releases = list_items(args.repo, "releases")
    tags = list_items(args.repo, "tags")
    try:
        tag = choose_tag(releases, tags, datetime.now(UTC))
    except ValueError as exc:
        parser.error(str(exc))
    print(tag)


if __name__ == "__main__":
    main()
