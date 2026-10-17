"""Release retrieval never installs unverified bytes or overwrites local models."""

import importlib.util
import json
from hashlib import sha256
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fetch_model.py"
spec = importlib.util.spec_from_file_location("model_download", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def bundle(root: Path, content: bytes) -> Path:
    reports = root / "reports"
    reports.mkdir()
    (reports / "selection_freeze.json").write_text(
        json.dumps(
            {
                "selected_model_path": str(module.MODEL_PATH),
                "selected_model_sha256": sha256(content).hexdigest(),
            }
        )
    )
    return root / module.MODEL_PATH


def test_verified_download_is_installed_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"trusted model bytes, never unpickled by installer"
    target = bundle(tmp_path, content)
    monkeypatch.setattr(module, "download_bytes", lambda: content)
    assert module.fetch_model(tmp_path) == target
    assert target.read_bytes() == content
    assert list(target.parent.glob(".model-*")) == []


def test_hash_mismatch_installs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = bundle(tmp_path, b"trusted")
    monkeypatch.setattr(module, "download_bytes", lambda: b"untrusted")
    with pytest.raises(RuntimeError, match="nothing was installed"):
        module.fetch_model(tmp_path)
    assert not target.exists()


def test_existing_matching_model_needs_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = bundle(tmp_path, b"trusted")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"trusted")
    monkeypatch.setattr(module, "download_bytes", lambda: pytest.fail("Network must not be used"))
    assert module.fetch_model(tmp_path) == target


def test_existing_different_model_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = bundle(tmp_path, b"trusted")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"local different model")
    monkeypatch.setattr(module, "download_bytes", lambda: pytest.fail("Network must not be used"))
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        module.fetch_model(tmp_path)
    assert target.read_bytes() == b"local different model"


def test_network_failure_does_not_create_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = bundle(tmp_path, b"trusted")

    def unavailable():
        raise RuntimeError("download failed")

    monkeypatch.setattr(module, "download_bytes", unavailable)
    with pytest.raises(RuntimeError, match="download failed"):
        module.fetch_model(tmp_path)
    assert not target.exists()


def test_unsupported_frozen_path_is_rejected(tmp_path: Path) -> None:
    bundle(tmp_path, b"trusted")
    metadata = tmp_path / "reports/selection_freeze.json"
    value = json.loads(metadata.read_text())
    value["selected_model_path"] = "../untrusted.pkl"
    metadata.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unsupported"):
        module.fetch_model(tmp_path)
