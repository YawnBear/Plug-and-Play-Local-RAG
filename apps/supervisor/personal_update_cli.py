from __future__ import annotations

import argparse
import json
from pathlib import Path

from .personal_update import verify_personal_update


def _state(path: Path) -> tuple[int, str | None]:
    if not path.exists():
        return 0, None
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "release_id",
            "release_sequence",
            "trust_metadata_sha256",
        }
        or value["schema_version"] != 1
        or not isinstance(value["release_sequence"], int)
        or value["release_sequence"] < 1
        or not isinstance(value["release_id"], str)
    ):
        raise ValueError("installed Personal release state is invalid")
    return value["release_sequence"], value["release_id"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--allowed-signers", type=Path, required=True)
    parser.add_argument("--allowed-signers-sha256", required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--installed-state", type=Path, required=True)
    arguments = parser.parse_args()
    sequence, release_id = _state(arguments.installed_state)
    result = verify_personal_update(
        manifest_path=arguments.manifest.resolve(),
        signature_path=arguments.signature.resolve(),
        artifact_root=arguments.artifact_root.resolve(),
        allowed_signers_path=arguments.allowed_signers.resolve(),
        pinned_public_key_sha256=arguments.allowed_signers_sha256,
        stage_root=arguments.stage_root.resolve(),
        candidate_root=arguments.candidate_root.resolve(),
        installed_release_sequence=sequence,
        installed_release_id=release_id,
    )
    print(
        json.dumps(
            {
                "result": "verified",
                "version": result.version,
                "release_id": result.release_id,
                "release_sequence": result.release_sequence,
                "expected_alembic_revision": result.expected_alembic_revision,
                "candidate_root": str(result.candidate_root),
                "manifest_sha256": result.manifest_sha256,
                "trust_metadata_sha256": result.trust_metadata_sha256,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
