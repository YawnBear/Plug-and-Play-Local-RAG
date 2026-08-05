"""Download the development reranker into an explicit offline model directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError


def model_is_complete(destination: Path) -> bool:
    has_weights = any(destination.glob("*.safetensors"))
    return (
        (destination / "config.json").is_file()
        and (destination / "tokenizer_config.json").is_file()
        and has_weights
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--preferred-path", required=True, type=Path)
    parser.add_argument("--result-file", required=True, type=Path)
    arguments = parser.parse_args()

    preferred_path = arguments.preferred_path.expanduser().resolve()
    if model_is_complete(preferred_path):
        resolved_path = preferred_path
    else:
        download_options = {
            "repo_id": arguments.model,
            "allow_patterns": [
                "*.model",
                "*.safetensors",
                "*.txt",
                "added_tokens.json",
                "config.json",
                "model.safetensors.index.json",
                "sentencepiece.bpe.model",
                "special_tokens_map.json",
                "tokenizer*",
            ],
            "ignore_patterns": [
            "*.onnx",
            "*.onnx_data",
            "flax_model.msgpack",
            "openvino/**",
            "pytorch_model.bin",
            "rust_model.ot",
            "tf_model.h5",
            ],
        }
        try:
            cached = snapshot_download(**download_options, local_files_only=True)
            print(f"reusing cached reranker at {cached}")
        except LocalEntryNotFoundError:
            print(f"downloading missing reranker {arguments.model}")
            cached = snapshot_download(**download_options)
        resolved_path = Path(cached).resolve()

    if not model_is_complete(resolved_path):
        raise RuntimeError(
            f"resolved reranker is incomplete at {resolved_path}"
        )
    result_file = arguments.result_file.expanduser().resolve()
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(f"{resolved_path}\n", encoding="utf-8")
    print(f"reranker prepared at {resolved_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
