"""Explicit provisioning from the Hub cache into fully verified local artifacts."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.otter.artifact import (
    ArtifactError,
    artifact_path,
    build_manifest,
    safe_artifact_path,
    validate_artifact,
)
from model_specs import (
    BM_TOKENIZER_FILES,
    BM_TOKENIZER_MODEL,
    BM_TOKENIZER_REVISION,
    MODELS,
    PINNED_REMOTE_CODE_SHA256,
)


def required_checkpoint_files(model_key: str) -> tuple[str, ...]:
    common = (
        *PINNED_REMOTE_CODE_SHA256,
        "config.json",
        "model.safetensors",
        "token_encoder_config.json",
    )
    if model_key == "CM":
        tokenizer_files = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")
    elif model_key == "BM":
        tokenizer_files = (
            "type_encoder_config.json",
            "token_tokenizer/tokenizer.json",
            "token_tokenizer/tokenizer_config.json",
            "type_tokenizer/tokenizer.json",
            "type_tokenizer/tokenizer_config.json",
        )
    else:
        raise ArtifactError("unknown pinned model key")
    return tuple(sorted((*common, *tokenizer_files)))


def _snapshot(model_id: str, revision: str, files: tuple[str, ...]) -> Path:
    # Keep Hub caching, resumability and network handling in the existing library.
    from huggingface_hub import snapshot_download

    try:
        return Path(
            snapshot_download(repo_id=model_id, revision=revision, allow_patterns=list(files))
        )
    except Exception:
        raise ArtifactError(
            "Pinned model download failed; check network access and retry."
        ) from None


def _copy_files(snapshot: Path, destination: Path, files: tuple[str, ...]) -> None:
    for name in files:
        # Hub cache symlinks are expected; only their regular-file contents are copied.
        source = (snapshot / name).resolve(strict=True)
        if not stat.S_ISREG(source.stat().st_mode):
            raise ArtifactError("downloaded artifact entries must be regular files")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def download_artifacts(artifact_root: str | Path) -> dict[str, dict]:
    """Reuse healthy targets or publish new pinned artifacts; never replace existing files."""
    root = safe_artifact_path(Path(artifact_root))
    root.mkdir(parents=True, exist_ok=True)
    output = {}
    # Preflight every existing target before downloading or publishing any new model.
    for key in MODELS:
        target = safe_artifact_path(artifact_path(root, key))
        if target.exists():
            try:
                output[key] = validate_artifact(root, key)[1]
            except (ValueError, OSError):
                raise ArtifactError(
                    f"Existing {key} artifact is invalid; move it aside before retrying."
                ) from None
    for key, identity in MODELS.items():
        if key in output:
            continue
        target = safe_artifact_path(artifact_path(root, key))
        # Staging shares a filesystem with the destination, allowing atomic directory rename.
        with TemporaryDirectory(prefix=".otter-download-", dir=root) as temporary:
            stage = Path(temporary)
            checkpoint = artifact_path(stage, key)
            checkpoint.mkdir(parents=True)
            files = required_checkpoint_files(key)
            _copy_files(_snapshot(identity.model_id, identity.revision, files), checkpoint, files)
            if key == "BM":
                tokenizer_files = tuple(BM_TOKENIZER_FILES)
                _copy_files(
                    _snapshot(BM_TOKENIZER_MODEL, BM_TOKENIZER_REVISION, tokenizer_files),
                    checkpoint / "runtime_tokenizer",
                    tokenizer_files,
                )
            manifest = build_manifest(checkpoint, key)
            (checkpoint / "manifest.json").write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            output[key] = validate_artifact(stage, key)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            safe_artifact_path(target)
            if target.exists():
                raise ArtifactError(f"{key} artifact appeared during download; retry to verify it.")
            checkpoint.rename(target)
    return output
