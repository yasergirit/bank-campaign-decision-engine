#!/usr/bin/env python3
"""Fetch this project's frozen model release and verify bytes before saving them."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_URL = (
    "https://github.com/yasergirit/bank-campaign-decision-engine/"
    "releases/download/model-v1/selected_model.pkl"
)
MODEL_PATH = Path("artifacts/comparison-v1/selected_model.pkl")
MAX_MODEL_BYTES = 10 * 1024 * 1024


def download_bytes() -> bytes:
    """Download only the fixed project-owned release, without deserializing it."""
    try:
        with urllib.request.urlopen(RELEASE_URL, timeout=45) as response:
            data = response.read(MAX_MODEL_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(
            "Model download failed. Check network access and the model-v1 release."
        ) from exc
    if len(data) > MAX_MODEL_BYTES:
        raise RuntimeError("Model download exceeds the 10 MiB safety limit.")
    return data


def fetch_model(root: Path = ROOT) -> Path:
    freeze = json.loads((root / "reports/selection_freeze.json").read_text())
    if Path(freeze["selected_model_path"]) != MODEL_PATH:
        raise ValueError("The selection freeze points to an unsupported model path.")
    expected = freeze["selected_model_sha256"]
    target = root / MODEL_PATH
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(
                "Existing model differs from the frozen SHA; refusing to overwrite it."
            )
        return target
    data = download_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Downloaded model differs from the frozen SHA; nothing was installed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".model-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        # Linking is atomic and never overwrites another process's existing file.
        os.link(temporary, target)
    except FileExistsError as exc:
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(
                "A different model appeared during download; it was preserved."
            ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def main() -> None:
    try:
        target = fetch_model()
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"Model setup failed: {exc}") from exc
    print(f"Frozen model verified: {target}")
    print("No training, test scoring or deserialization was performed.")


if __name__ == "__main__":
    main()
