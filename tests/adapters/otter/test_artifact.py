from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256

import pytest
from pydantic import ValidationError

from adapters.otter import artifact
from adapters.otter.artifact import ArtifactError, validate_artifact


@pytest.fixture
def provisioned_artifact(tmp_path, monkeypatch):
    """Tiny local checkpoint with complete pins; no production validation is bypassed."""
    root = artifact.artifact_path(tmp_path, "CM")
    root.mkdir(parents=True)
    config = json.dumps({"token_encoder_config": {"model_type": "example"}}).encode()
    files = {
        "config.json": config,
        "model.safetensors": b"test weights",
        "tokenizer.json": b"{}",
        "tokenizer_config.json": b"{}",
    }
    files.update({name: b"test code" for name in artifact.PINNED_REMOTE_CODE_SHA256})
    for name, data in files.items():
        (root / name).write_bytes(data)
    monkeypatch.setattr(
        artifact,
        "PINNED_REMOTE_CODE_SHA256",
        {name: sha256(files[name]).hexdigest() for name in artifact.PINNED_REMOTE_CODE_SHA256},
    )
    monkeypatch.setattr(artifact, "PINNED_CONFIG_SHA256", {"CM": sha256(config).hexdigest()})
    monkeypatch.setattr(
        artifact,
        "MODELS",
        {
            **artifact.MODELS,
            "CM": replace(
                artifact.MODELS["CM"],
                weight_size_bytes=len(files["model.safetensors"]),
                weight_sha256=sha256(files["model.safetensors"]).hexdigest(),
            ),
        },
    )
    manifest = artifact.build_manifest(root, "CM")
    (root / "manifest.json").write_text(manifest.model_dump_json())
    return tmp_path, root, manifest


def test_artifact_validation_fails_closed_for_missing_model(tmp_path) -> None:
    with pytest.raises(ArtifactError):
        validate_artifact(tmp_path, "CM")


def test_artifact_validation_rejects_unknown_model(tmp_path) -> None:
    with pytest.raises((ArtifactError, ValueError, KeyError)):
        validate_artifact(tmp_path, "UNKNOWN")


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "a/../b", "a\\b", "./a", "manifest.json"]
)
def test_manifest_rejects_unsafe_paths(path):
    with pytest.raises(ValidationError):
        artifact.ArtifactFile(path=path, size_bytes=0, sha256="a" * 64)


def test_trusted_startup_and_explicit_full_verification(provisioned_artifact, monkeypatch):
    base, root, manifest = provisioned_artifact
    assert validate_artifact(base, "CM")[1]["content_verification"] == "FULL_SHA256"
    weights = root / "model.safetensors"
    weights.write_bytes(b"changed data")  # Same size; trusted startup does not verify weight bytes.
    with monkeypatch.context() as scoped:
        scoped.setattr(
            artifact, "_digest", lambda path: pytest.fail("startup must not hash content")
        )
        identity = artifact.inspect_trusted_artifact(base, "CM")[1]
    assert identity["content_verification"] == "TRUSTED_SOURCE_NOT_HASHED"
    assert identity["artifact_sha256"] == manifest.artifact_sha256
    with pytest.raises(ArtifactError, match="checksum"):
        validate_artifact(base, "CM")


@pytest.mark.parametrize(
    "fault", ["file_set", "size", "identity", "digest", "duplicate", "symlink"]
)
def test_artifact_inventory_and_identity_fail_closed(provisioned_artifact, fault):
    base, root, manifest = provisioned_artifact
    payload = manifest.model_dump(mode="json")
    if fault == "file_set":
        (root / "unexpected").write_bytes(b"new")
    elif fault == "size":
        (root / "model.safetensors").write_bytes(b"short")
    elif fault == "identity":
        payload["revision"] = "untrusted"
    elif fault == "digest":
        payload["artifact_sha256"] = "0" * 64
    elif fault == "duplicate":
        payload["files"].append(payload["files"][0])
    elif fault == "symlink":
        target = root / "tokenizer.json"
        target.rename(root / "other.json")
        target.symlink_to(root / "other.json")
    (root / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ArtifactError):
        artifact.inspect_trusted_artifact(base, "CM")
