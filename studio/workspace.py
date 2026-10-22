"""Local-only Studio state; never stored in the repository or published."""

import difflib
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path


def source_revision(root):
    root = Path(root)
    digest = hashlib.sha256()
    for folder, patterns in (
        ("studio", ("*.py", "*.html", "prompts.json")),
        ("scripts", ("*.py",)),
    ):
        paths = {p for pattern in patterns for p in (root / folder).glob(pattern)}
        for path in sorted(paths):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


SNAPSHOT_FILES = (
    "mod.json",
    "hooks.json",
    "tweaks.json",
    "Tweak.x",
    "AMTweaks.gen.h",
    "AMTweaks.gen.m",
    "Makefile",
    "control",
)
SNAPSHOT_FILE_LIMIT = 2 * 1024 * 1024
SNAPSHOT_TOTAL_LIMIT = 16 * 1024 * 1024
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)
}


def _snapshot_dir(state_root, mod_name):
    if (
        not isinstance(mod_name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", mod_name)
        or mod_name.lower() in _RESERVED
    ):
        raise ValueError("Invalid mod name")
    root = Path(state_root).resolve()
    folder = root / "snapshots" / mod_name
    if not folder.resolve().is_relative_to(root):
        raise ValueError("Snapshot folder escapes local state")
    return folder


def _snapshot_path(state_root, mod_name, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("Invalid snapshot id")
    path = _snapshot_dir(state_root, mod_name) / (identifier + ".json")
    if path.is_symlink():
        raise ValueError("Snapshot cannot be a symbolic link")
    return path


def _source_state(mod_folder):
    folder = Path(mod_folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Mod folder must be a directory")
    files = {}
    total = 0
    for name in SNAPSHOT_FILES:
        path = folder / name
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Source cannot be a link: {name}")
        if not path.exists():
            files[name] = {"present": False}
            continue
        if not path.is_file():
            raise ValueError(f"Source must be a regular file: {name}")
        if path.stat().st_size > SNAPSHOT_FILE_LIMIT:
            raise ValueError(f"Source file too large: {name}")
        with path.open("rb") as stream:
            raw = stream.read(SNAPSHOT_FILE_LIMIT + 1)
        total += len(raw)
        if len(raw) > SNAPSHOT_FILE_LIMIT or total > SNAPSHOT_TOTAL_LIMIT:
            raise ValueError("Snapshot source too large")
        try:
            content = raw.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"Source is not UTF8: {name}") from exc
        files[name] = {
            "present": True,
            "content": content,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return files


def _state_token(files):
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def _validate_snapshot(data, mod_name, identifier, validate_configs=True):
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("id") != identifier
        or data.get("mod") != mod_name
        or not isinstance(data.get("label"), str)
        or len(data["label"]) > 200
        or not isinstance(data.get("created_at"), str)
    ):
        raise ValueError("Corrupt snapshot metadata")
    try:
        datetime.fromisoformat(data["created_at"])
    except ValueError as exc:
        raise ValueError("Corrupt snapshot date") from exc
    files = data.get("files")
    if not isinstance(files, dict) or set(files) != set(SNAPSHOT_FILES):
        raise ValueError("Corrupt snapshot file list")
    total = 0
    for name, entry in files.items():
        if not isinstance(entry, dict) or type(entry.get("present")) is not bool:
            raise ValueError(f"Corrupt snapshot file: {name}")
        if not entry["present"]:
            if set(entry) != {"present"}:
                raise ValueError(f"Corrupt absent snapshot file: {name}")
            continue
        if set(entry) != {"present", "content", "sha256"} or not isinstance(
            entry["content"], str
        ):
            raise ValueError(f"Corrupt snapshot content: {name}")
        try:
            raw = entry["content"].encode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"Invalid UTF8 snapshot content: {name}") from exc
        total += len(raw)
        if len(raw) > SNAPSHOT_FILE_LIMIT or total > SNAPSHOT_TOTAL_LIMIT:
            raise ValueError("Snapshot content too large")
        if entry["sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"Corrupt snapshot hash: {name}")
        if validate_configs and name.endswith(".json"):
            try:
                config = json.loads(entry["content"])
            except ValueError as exc:
                raise ValueError(f"Invalid JSON source config: {name}") from exc
            if not isinstance(config, dict):
                raise ValueError(f"JSON source config must be an object: {name}")
    return data


def _read_snapshot(state_root, mod_name, identifier, validate_configs=True):
    path = _snapshot_path(state_root, mod_name, identifier)
    # JSON escaping can expand each source byte to at most six ASCII characters.
    limit = SNAPSHOT_TOTAL_LIMIT * 6 + 32768
    if path.stat().st_size > limit:
        raise ValueError("Snapshot manifest too large")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Snapshot manifest too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Corrupt snapshot manifest") from exc
    return _validate_snapshot(data, mod_name, identifier, validate_configs)


def _snapshot_summary(data):
    return {k: data[k] for k in ("id", "label", "created_at")} | {
        "files": [n for n, f in data["files"].items() if f["present"]]
    }


def snapshot_create(
    state_root: Path, mod_name: str, mod_folder: Path, label="manual"
) -> dict:
    identifier = uuid.uuid4().hex
    path = _snapshot_path(state_root, mod_name, identifier)
    if not isinstance(label, str) or len(label) > 200:
        raise ValueError("Snapshot label must be text of at most 200 characters")
    data = {
        "version": 1,
        "id": identifier,
        "mod": mod_name,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(),
        "files": _source_state(mod_folder),
    }
    # Recovery snapshots may contain temporarily invalid edited configs; restoration validates them.
    atomic_json(path, data)
    return _snapshot_summary(data)


def snapshot_list(state_root, mod_name) -> list:
    folder = _snapshot_dir(state_root, mod_name)
    if not folder.exists():
        return []
    result = []
    for path in folder.glob("*.json"):
        result.append(
            _snapshot_summary(
                _read_snapshot(state_root, mod_name, path.stem, validate_configs=False)
            )
        )
    return sorted(result, key=lambda s: (s["created_at"], s["id"]), reverse=True)


def snapshot_preview(state_root, mod_name, mod_folder, id) -> dict:
    target = _read_snapshot(state_root, mod_name, id)
    current = _source_state(mod_folder)
    files = []
    for name in SNAPSHOT_FILES:
        before, after = current[name], target["files"][name]
        diff = "".join(
            difflib.unified_diff(
                before.get("content", "").splitlines(keepends=True),
                after.get("content", "").splitlines(keepends=True),
                fromfile="current/" + name,
                tofile="snapshot/" + name,
            )
        )
        files.append(
            {
                "name": name,
                "before_present": before["present"],
                "after_present": after["present"],
                "changed": before != after,
                "diff": diff,
            }
        )
    return {
        "id": id,
        "label": target["label"],
        "expected": _state_token(current),
        "files": files,
    }


def snapshot_restore(state_root, mod_name, mod_folder, id, expected) -> dict:
    target = _read_snapshot(state_root, mod_name, id)
    current = _source_state(mod_folder)
    if not isinstance(expected, str) or _state_token(current) != expected:
        raise ValueError("Configuration changed since preview; preview again")
    recovery = snapshot_create(state_root, mod_name, mod_folder, "before restore " + id)
    folder = Path(mod_folder).resolve(strict=True)
    staged = []
    try:
        for name, entry in target["files"].items():
            if entry["present"]:
                temporary = folder / (".studio-restore-" + uuid.uuid4().hex + ".tmp")
                staged.append((temporary, folder / name))
                with temporary.open("xb") as stream:
                    stream.write(entry["content"].encode("utf-8"))
        if _state_token(_source_state(folder)) != expected:
            raise ValueError(
                "Configuration changed while staging restore; preview again"
            )
        for temporary, path in staged:
            os.replace(temporary, path)
        for name, entry in target["files"].items():
            if not entry["present"]:
                (folder / name).unlink(missing_ok=True)
    finally:
        for temporary, _path in staged:
            temporary.unlink(missing_ok=True)
    return {"id": id, "recovery_id": recovery["id"], "restored": list(SNAPSHOT_FILES)}
