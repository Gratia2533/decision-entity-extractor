"""Local manifest, exact checkpoint identity and complete file integrity checks."""

from __future__ import annotations

import json
import os
import stat
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hashing import content_sha256
from model_specs import (
    BM_TOKENIZER_FILES,
    BM_TOKENIZER_MODEL,
    BM_TOKENIZER_REVISION,
    MODELS,
    PINNED_CONFIG_SHA256,
    PINNED_REMOTE_CODE_SHA256,
)


class ArtifactError(ValueError):
    pass


class ArtifactFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        path = PurePosixPath(value)
        if (
            not path.parts
            or path.is_absolute()
            or "\\" in value
            or ".." in path.parts
            or path.as_posix() != value
            or value == "manifest.json"
        ):
            raise ValueError("artifact path must be normalized and relative")
        return value


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["otter-cm-bm-artifact-v1"] = "otter-cm-bm-artifact-v1"
    model_key: Literal["CM", "BM"]
    model_id: str
    revision: str
    license: Literal["Apache-2.0"] = "Apache-2.0"
    runtime_tokenizer_model: str | None = None
    runtime_tokenizer_revision: str | None = None
    files: tuple[ArtifactFile, ...]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _safe(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError("artifact symlinks are forbidden")
    return path


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root: Path) -> tuple[ArtifactFile, ...]:
    records = []
    for path in sorted(root.rglob("*")):
        _safe(path)
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ArtifactError("artifact entries must be regular files")
        if path == root / "manifest.json":
            continue
        records.append(
            ArtifactFile(
                path=path.relative_to(root).as_posix(),
                size_bytes=path.stat().st_size,
                sha256=_digest(path),
            )
        )
    return tuple(records)


def artifact_path(root: Path, model_key: str) -> Path:
    identity = MODELS[model_key]
    return root / identity.model_id.replace("/", "--") / identity.revision


def _check_pins(root: Path, manifest: ArtifactManifest) -> None:
    identity = MODELS[manifest.model_key]
    if (manifest.model_id, manifest.revision, manifest.license) != (
        identity.model_id,
        identity.revision,
        identity.license,
    ):
        raise ArtifactError("artifact model identity mismatch")
    indexed = {item.path: item for item in manifest.files}
    if len(indexed) != len(manifest.files):
        raise ArtifactError("duplicate manifest paths")
    if tuple(indexed) != tuple(sorted(indexed)) or (
        content_sha256([item.model_dump() for item in manifest.files]) != manifest.artifact_sha256
    ):
        raise ArtifactError("artifact manifest identity differs from its ordered file inventory")
    pinned = {
        **PINNED_REMOTE_CODE_SHA256,
        "config.json": PINNED_CONFIG_SHA256[manifest.model_key],
        "model.safetensors": identity.weight_sha256,
    }
    if any(
        name not in indexed or indexed[name].sha256 != digest for name, digest in pinned.items()
    ):
        raise ArtifactError("artifact differs from frozen checkpoint/code checksums")
    if indexed["model.safetensors"].size_bytes != identity.weight_size_bytes:
        raise ArtifactError("artifact weight size mismatch")
    if manifest.model_key == "BM":
        if (manifest.runtime_tokenizer_model, manifest.runtime_tokenizer_revision) != (
            BM_TOKENIZER_MODEL,
            BM_TOKENIZER_REVISION,
        ):
            raise ArtifactError("BM runtime tokenizer identity mismatch")
        for name, (size, digest) in BM_TOKENIZER_FILES.items():
            item = indexed.get("runtime_tokenizer/" + name)
            if item is None or (item.size_bytes, item.sha256) != (size, digest):
                raise ArtifactError("BM runtime tokenizer differs from frozen base tokenizer")
    tokenizers = ("",) if manifest.model_key == "CM" else ("token_tokenizer/", "type_tokenizer/")
    required = {
        prefix + name
        for prefix in tokenizers
        for name in ("tokenizer.json", "tokenizer_config.json")
    }
    if not required <= indexed.keys():
        raise ArtifactError("local tokenizer files are required")
    config = json.loads((root / "config.json").read_bytes())
    if not config.get("token_encoder_config") or (
        manifest.model_key == "BM" and not config.get("type_encoder_config")
    ):
        raise ArtifactError("embedded encoder configurations are required for offline loading")


def build_manifest(root: Path, model_key: str) -> ArtifactManifest:
    """Host provisioning helper: hash existing files; never download or modify model files."""
    root = _safe(root)
    if not root.is_dir():
        raise ArtifactError("artifact directory unavailable")
    identity = MODELS[model_key]
    records = _inventory(root)
    manifest = ArtifactManifest(
        model_key=model_key,
        model_id=identity.model_id,
        revision=identity.revision,
        runtime_tokenizer_model=BM_TOKENIZER_MODEL if model_key == "BM" else None,
        runtime_tokenizer_revision=BM_TOKENIZER_REVISION if model_key == "BM" else None,
        files=records,
        artifact_sha256=content_sha256([r.model_dump() for r in records]),
    )
    _check_pins(root, manifest)
    return manifest


def validate_artifact(artifact_root: Path, model_key: str) -> tuple[Path, dict]:
    root = _safe(artifact_path(artifact_root, model_key))
    path = _safe(root / "manifest.json")
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ArtifactError("local artifact manifest is required")
    manifest = ArtifactManifest.model_validate_json(path.read_bytes())
    if manifest.model_key != model_key:
        raise ArtifactError("artifact model key mismatch")
    actual = _inventory(root)
    if actual != manifest.files:
        raise ArtifactError("artifact file set/size/checksum mismatch")
    if content_sha256([r.model_dump() for r in actual]) != manifest.artifact_sha256:
        raise ArtifactError("artifact manifest checksum mismatch")
    _check_pins(root, manifest)
    return root, {
        **_identity(manifest),
        "content_verification": "FULL_SHA256",
        "files_verified": len(actual),
    }


def inspect_trusted_artifact(artifact_root: Path, model_key: str) -> tuple[Path, dict]:
    """Inspect the user-trusted local source without reading weight bytes for hashing.

    Manifest digests are declared identity only. The explicit CLI verifier remains
    available when a consumer needs cryptographic verification of current file bytes.
    """
    root = _safe(artifact_path(artifact_root, model_key))
    path = _safe(root / "manifest.json")
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ArtifactError("local artifact manifest is required")
    manifest = ArtifactManifest.model_validate_json(path.read_bytes())
    if manifest.model_key != model_key:
        raise ArtifactError("artifact model key mismatch")
    actual = {}
    for item in root.rglob("*"):
        _safe(item)
        info = item.stat()
        if stat.S_ISDIR(info.st_mode) or item == path:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError("artifact entries must be regular files")
        actual[item.relative_to(root).as_posix()] = info.st_size
    declared = {item.path: item.size_bytes for item in manifest.files}
    if actual != declared:
        raise ArtifactError("artifact file set/size mismatch")
    _check_pins(root, manifest)
    return root, {
        **_identity(manifest),
        "content_verification": "TRUSTED_SOURCE_NOT_HASHED",
        "files_checked": len(actual),
    }


def _identity(manifest: ArtifactManifest) -> dict:
    return {
        "model_id": manifest.model_id,
        "revision": manifest.revision,
        "license": manifest.license,
        "artifact_sha256": manifest.artifact_sha256,
        "runtime_tokenizer": (
            {
                "model_id": BM_TOKENIZER_MODEL,
                "revision": BM_TOKENIZER_REVISION,
                "files": {
                    name: {"size_bytes": size, "sha256": digest}
                    for name, (size, digest) in BM_TOKENIZER_FILES.items()
                },
            }
            if manifest.model_key == "BM"
            else {"source": "checkpoint"}
        ),
    }
