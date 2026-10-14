"""Report completed publication steps and retain failed staged uploads for retry."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def publication_plan(meta, requested):
    personal = bool(meta.get("personal"))
    return {"personal": personal, "catalogue": bool(requested) and not personal}


def summary(check, upload, source, requested, *, personal=False, personal_result=""):
    if personal:
        if check == "success" and personal_result == "success":
            return "Personal build stored privately; not added to the catalogue."
        return "Personal build copy not completed. Nothing was published; check the failed steps before retrying."
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
    parser.add_argument("action", choices=["summary", "cleanup", "plan"])
    parser.add_argument("--mod")
    args = parser.parse_args()
    upload, source = os.getenv("UPLOAD_RESULT", ""), os.getenv("SOURCE_RESULT", "")
    if args.action == "plan":
        if not args.mod or not re.fullmatch(r"[a-z0-9-]+", args.mod):
            parser.error("plan needs a valid --mod folder")
        with (Path("mods") / args.mod / "mod.json").open(encoding="utf-8") as file:
            plan = publication_plan(json.load(file), os.getenv("PUBLISH", "") == "true")
        for key, value in plan.items():
            print(f"{key}={str(value).lower()}")
    elif args.action == "cleanup":
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
                personal=os.getenv("PERSONAL", "") == "true",
                personal_result=os.getenv("PERSONAL_RESULT", ""),
            )
        )


if __name__ == "__main__":
    main()
