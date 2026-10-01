import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.otter import artifact, provisioning
from model_specs import BM_TOKENIZER_MODEL, BM_TOKENIZER_REVISION


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    """Synthetic pinned Hub content, validated through real manifest/integrity code."""
    sources = {}
    configs = {}
    models = {}
    code = b"pinned code"
    code_hashes = {name: sha256(code).hexdigest() for name in artifact.PINNED_REMOTE_CODE_SHA256}
    for key, model in artifact.MODELS.items():
        source = tmp_path / "hub" / key
        source.mkdir(parents=True)
        payload = {
            "token_encoder_config": {"model_type": "example"},
            "type_encoder_config": {"model_type": "example"},
        }
        config = json.dumps(payload).encode()
        weights = f"pinned {key} weights".encode()
        for name in provisioning.required_checkpoint_files(key):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            content = code if name in code_hashes else b"{}"
            if name == "config.json":
                content = config
            elif name == "model.safetensors":
                content = weights
            path.write_bytes(content)
        (source / "README.md").write_text("not runtime content")
        (source / ".cache").mkdir()
        (source / ".cache" / "download-metadata").write_text("private cache")
        models[key] = replace(
            model, weight_size_bytes=len(weights), weight_sha256=sha256(weights).hexdigest()
        )
        configs[key] = sha256(config).hexdigest()
        sources[model.model_id] = source
    tokenizer = tmp_path / "hub" / "base-tokenizer"
    tokenizer.mkdir()
    tokenizer_pins = {}
    for name in artifact.BM_TOKENIZER_FILES:
        payload = b"{}"
        (tokenizer / name).write_bytes(payload)
        tokenizer_pins[name] = (len(payload), sha256(payload).hexdigest())
    sources[BM_TOKENIZER_MODEL] = tokenizer
    for module in (artifact, provisioning):
        monkeypatch.setattr(module, "MODELS", models)
        monkeypatch.setattr(module, "PINNED_REMOTE_CODE_SHA256", code_hashes)
        monkeypatch.setattr(module, "BM_TOKENIZER_FILES", tokenizer_pins)
    monkeypatch.setattr(artifact, "PINNED_CONFIG_SHA256", configs)
    calls = []

    def snapshot(repo, revision, files):
        calls.append((repo, revision, files))
        return sources[repo]

    monkeypatch.setattr(provisioning, "_snapshot", snapshot)
    return sources, calls, tmp_path / "artifacts"


def test_download_full_validation_and_exact_pins(snapshots):
    sources, calls, root = snapshots
    output = provisioning.download_artifacts(root)
    assert set(output) == {"CM", "BM"}
    for key, model in provisioning.MODELS.items():
        assert artifact.validate_artifact(root, key)[1] == output[key]
        target = artifact.artifact_path(root, key)
        assert not any(path.is_symlink() for path in target.rglob("*"))
        assert not (target / "README.md").exists()
        assert not (target / ".cache").exists()
        assert calls[0 if key == "CM" else 1] == (
            model.model_id,
            model.revision,
            provisioning.required_checkpoint_files(key),
        )
    assert calls[2] == (
        BM_TOKENIZER_MODEL,
        BM_TOKENIZER_REVISION,
        tuple(provisioning.BM_TOKENIZER_FILES),
    )
    assert not list(root.glob(".otter-download-*"))
    assert all(source.is_dir() for source in sources.values())


def test_repeat_reuses_healthy_targets_without_hub(snapshots, monkeypatch):
    _, _, root = snapshots
    first = provisioning.download_artifacts(root)
    weights = artifact.artifact_path(root, "CM") / "model.safetensors"
    original = weights.stat().st_mtime_ns
    monkeypatch.setattr(provisioning, "_snapshot", lambda *_: pytest.fail("redownload"))
    assert provisioning.download_artifacts(root) == first
    assert weights.stat().st_mtime_ns == original


@pytest.mark.parametrize("fault", ["weights", "manifest", "extra", "empty", "symlink"])
def test_damaged_target_is_never_replaced(snapshots, monkeypatch, fault):
    _, _, root = snapshots
    provisioning.download_artifacts(root)
    target = artifact.artifact_path(root, "BM")
    if fault == "weights":
        (target / "model.safetensors").write_bytes(b"untrusted")
    elif fault == "manifest":
        (target / "manifest.json").write_text("invalid")
    elif fault == "extra":
        (target / "user-data.txt").write_text("preserve me")
    elif fault == "empty":
        target = artifact.artifact_path(root, "CM")
        for path in target.iterdir():
            path.unlink()
    else:
        file = target / "model.safetensors"
        file.unlink()
        file.symlink_to(target / "config.json")
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(provisioning, "_snapshot", lambda *_: pytest.fail("download"))
    with pytest.raises(artifact.ArtifactError, match="invalid|symlinks"):
        provisioning.download_artifacts(root)
    assert {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("fault", ["checksum", "missing", "interrupt", "network"])
def test_failed_staging_never_publishes_manifest(snapshots, monkeypatch, fault):
    sources, _, root = snapshots
    cm = provisioning.MODELS["CM"]
    checkpoint = sources[cm.model_id]
    if fault == "checksum":
        (checkpoint / "model.safetensors").write_bytes(b"changed weights")
    elif fault == "missing":
        (checkpoint / "tokenizer.json").unlink()
    else:

        def failed(*_):
            raise KeyboardInterrupt() if fault == "interrupt" else RuntimeError("secret")

        monkeypatch.setattr(provisioning, "_snapshot", failed)
    with pytest.raises((artifact.ArtifactError, OSError, KeyboardInterrupt, RuntimeError)):
        provisioning.download_artifacts(root)
    assert not artifact.artifact_path(root, "CM").exists()
    assert not list(root.glob(".otter-download-*"))
    assert checkpoint.exists()


def test_hub_symlinks_become_regular_copies(snapshots):
    sources, _, root = snapshots
    for source in sources.values():
        file = next(path for path in source.rglob("*") if path.is_file())
        payload = file.read_bytes()
        blob = source.parent / (source.name + "-blob")
        blob.write_bytes(payload)
        file.unlink()
        file.symlink_to(blob)
    provisioning.download_artifacts(root)
    assert not any(path.is_symlink() for path in root.rglob("*"))


def test_artifact_root_symlink_is_rejected(snapshots, tmp_path):
    _, calls, root = snapshots
    root.mkdir()
    link = tmp_path / "artifact-link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(artifact.ArtifactError, match="symlinks"):
        provisioning.download_artifacts(link)
    assert not calls


def test_snapshot_uses_library_cache_and_redacts_failure(monkeypatch):
    import sys

    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        return "/cached/snapshot"

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    assert provisioning._snapshot("model", "revision", ("config.json",)) == Path("/cached/snapshot")
    assert calls == [
        {"repo_id": "model", "revision": "revision", "allow_patterns": ["config.json"]}
    ]

    def failure(**_):
        raise RuntimeError("token=secret")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=failure))
    with pytest.raises(artifact.ArtifactError) as error:
        provisioning._snapshot("model", "revision", ())
    assert "secret" not in str(error.value)


def test_failed_full_validation_never_publishes(snapshots, monkeypatch):
    _, _, root = snapshots
    monkeypatch.setattr(
        provisioning,
        "validate_artifact",
        lambda *_: (_ for _ in ()).throw(artifact.ArtifactError("invalid bytes")),
    )
    with pytest.raises(artifact.ArtifactError, match="invalid bytes"):
        provisioning.download_artifacts(root)
    assert not artifact.artifact_path(root, "CM").exists()
    assert not list(root.glob(".otter-download-*"))


def test_target_appearing_before_publish_is_preserved(snapshots, monkeypatch):
    _, _, root = snapshots
    validate = provisioning.validate_artifact

    def competing_target(stage, key):
        result = validate(stage, key)
        target = artifact.artifact_path(root, key)
        target.mkdir(parents=True)
        (target / "user-file").write_text("preserve")
        return result

    monkeypatch.setattr(provisioning, "validate_artifact", competing_target)
    with pytest.raises(artifact.ArtifactError, match="appeared"):
        provisioning.download_artifacts(root)
    assert (artifact.artifact_path(root, "CM") / "user-file").read_text() == "preserve"
    assert not list(root.glob(".otter-download-*"))


def test_missing_bm_tokenizer_leaves_completed_cm_reusable(snapshots):
    sources, _, root = snapshots
    (sources[BM_TOKENIZER_MODEL] / next(iter(provisioning.BM_TOKENIZER_FILES))).unlink()
    with pytest.raises(OSError):
        provisioning.download_artifacts(root)
    artifact.validate_artifact(root, "CM")
    assert not artifact.artifact_path(root, "BM").exists()
    assert not list(root.glob(".otter-download-*"))


def test_nonregular_snapshot_entry_is_rejected(snapshots):
    sources, _, root = snapshots
    source = sources[provisioning.MODELS["CM"].model_id]
    (source / "tokenizer.json").unlink()
    (source / "tokenizer.json").mkdir()
    with pytest.raises(artifact.ArtifactError, match="regular files"):
        provisioning.download_artifacts(root)
    assert not artifact.artifact_path(root, "CM").exists()
