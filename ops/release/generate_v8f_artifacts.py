from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _component(kind: str, name: str, version: str, purl: str) -> dict[str, object]:
    return {
        "type": kind,
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
    }


def python_components() -> list[dict[str, object]]:
    lock = tomllib.loads((ROOT / "apps" / "api" / "uv.lock").read_text("utf-8"))
    return [
        _component(
            "library",
            package["name"],
            package["version"],
            f"pkg:pypi/{package['name']}@{package['version']}",
        )
        for package in lock["package"]
        if isinstance(package.get("name"), str)
        and isinstance(package.get("version"), str)
    ]


def npm_components() -> list[dict[str, object]]:
    text = (ROOT / "pnpm-lock.yaml").read_text("utf-8")
    try:
        package_section = text.split("\npackages:\n", 1)[1].split("\nsnapshots:\n", 1)[
            0
        ]
    except IndexError as exc:
        raise ValueError("pnpm lock package section is unavailable") from exc
    components: list[dict[str, object]] = []
    package_keys = re.findall(
        r'''^  (?:'([^']+)'|"([^"]+)"|([^'"\s][^:]*)):\s*$''',
        package_section,
        re.MULTILINE,
    )
    for single_quoted, double_quoted, unquoted in package_keys:
        raw = single_quoted or double_quoted or unquoted
        if "@" not in raw:
            continue
        name, version = raw.rsplit("@", 1)
        if not name or not version or "(" in version:
            continue
        escaped = name.replace("@", "%40").replace("/", "%2F")
        components.append(
            _component("library", name, version, f"pkg:npm/{escaped}@{version}")
        )
    return components


def platform_components() -> list[dict[str, object]]:
    release = json.loads(
        (ROOT / "ops" / "windows" / "v8a" / "personal-release.json").read_text("utf-8")
    )
    components = [
        _component(
            "container",
            "pgvector-postgresql",
            release["stores"]["postgres_image"].split("@", 1)[0].rsplit(":", 1)[-1],
            "pkg:oci/pgvector-postgresql@"
            + release["stores"]["postgres_image"].split("@sha256:", 1)[1],
        ),
        _component(
            "container",
            "rustfs",
            release["stores"]["rustfs_image"].split("@", 1)[0].rsplit(":", 1)[-1],
            "pkg:oci/rustfs@"
            + release["stores"]["rustfs_image"].split("@sha256:", 1)[1],
        ),
    ]
    for model in release["ollama_models"]:
        identity = model["identity"]
        digest = model["expected_digest"]
        components.append(
            _component(
                "machine-learning-model",
                identity.split(":", 1)[0],
                digest,
                "pkg:generic/ollama/" + identity.split(":", 1)[0] + "@" + digest,
            )
        )
    components.extend(
        [
            _component(
                "machine-learning-model",
                "bge-reranker-v2-m3",
                "release-pinned",
                "pkg:huggingface/BAAI/bge-reranker-v2-m3@release-pinned",
            ),
            _component(
                "machine-learning-model",
                "PaddleOCR-VL-1.6",
                "1.6",
                "pkg:huggingface/PaddlePaddle/PaddleOCR-VL-1.6@1.6",
            ),
        ]
    )
    return components


def build_sbom() -> dict[str, object]:
    components = python_components() + npm_components() + platform_components()
    unique = {item["bom-ref"]: item for item in components}
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000008",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "pkg:github/YawnBear/Plug-and-Play-Local-RAG@v8f",
                "name": "Local RAG Personal",
                "version": "v8f",
            },
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "Local RAG deterministic SBOM generator",
                        "version": "1",
                    }
                ]
            },
        },
        "components": [unique[key] for key in sorted(unique)],
        "compositions": [
            {
                "aggregate": "incomplete",
                "assemblies": [
                    "pkg:github/YawnBear/Plug-and-Play-Local-RAG@v8f"
                ],
            }
        ],
    }


def write_sbom(path: Path, *, check: bool) -> None:
    value = json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n"
    if check:
        if not path.is_file() or path.read_text("utf-8") != value:
            raise SystemExit("SBOM.cdx.json is stale; regenerate it")
        return
    path.write_text(value, encoding="utf-8", newline="\n")


def write_checksums(root: Path, *, check: bool) -> None:
    names = [
        "Local-RAG-Personal.zip",
        "SBOM.cdx.json",
        "release-trust-metadata.json",
        "Verify-and-Install-Local-RAG.ps1",
        "Install-Local-RAG.cmd",
    ]
    lines = []
    for name in names:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"release checksum input is missing: {name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    value = "".join(lines)
    output = root / "SHA256SUMS"
    if check:
        if not output.is_file() or output.read_text("ascii") != value:
            raise SystemExit("SHA256SUMS is stale; regenerate it")
        return
    output.write_text(value, encoding="ascii", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--release-root", type=Path)
    arguments = parser.parse_args()
    write_sbom(ROOT / "SBOM.cdx.json", check=arguments.check)
    if arguments.release_root:
        write_checksums(arguments.release_root.resolve(), check=arguments.check)


if __name__ == "__main__":
    main()
