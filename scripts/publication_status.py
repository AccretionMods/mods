"""Report completed publication steps and retain failed staged uploads for retry."""

import argparse
import os
import re
import subprocess


def summary(check, upload, source, requested):
    if upload == "success" and source == "success":
        return "Published privately; it's in the store after its next build."
    if upload == "success":
        return "Private upload completed, but the source update failed or was cancelled. Retry publication."
    if check == "success" and not requested:
        return "Built and checked only (not published)."
    return "Build or publication not completed. Check the failed steps before retrying."


def cleanup_draft(repo, release, upload, source):
    if not release or upload != "success" or source != "success":
        return False
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or not release.isdecimal()
    ):
        raise ValueError("Invalid draft repository or release ID")
    subprocess.run(
        ["gh", "api", "-X", "DELETE", f"repos/{repo}/releases/{release}"], check=True
    )
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["summary", "cleanup"])
    args = parser.parse_args()
    upload, source = os.getenv("UPLOAD_RESULT", ""), os.getenv("SOURCE_RESULT", "")
    if args.action == "cleanup":
        release = os.getenv("DRAFT_RELEASE", "")
        if (
            not cleanup_draft(
                os.getenv("GITHUB_REPOSITORY", ""), release, upload, source
            )
            and release
        ):
            print(
                "Private staging draft retained. Retry this run after resolving the failed step."
            )
    else:
        print(
            summary(
                os.getenv("CHECK_RESULT", ""),
                upload,
                source,
                os.getenv("PUBLISH", "") == "true",
            )
        )


if __name__ == "__main__":
    main()
