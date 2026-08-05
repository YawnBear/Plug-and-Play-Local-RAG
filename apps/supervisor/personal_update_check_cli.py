from __future__ import annotations

import argparse
import json
from pathlib import Path

from .personal_update_cli import _state
from .v8a_trust import verify_trust_metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trust-metadata", type=Path, required=True)
    parser.add_argument("--installed-state", type=Path, required=True)
    arguments = parser.parse_args()
    installed_sequence, installed_id = _state(arguments.installed_state)
    trust = verify_trust_metadata(
        arguments.trust_metadata.resolve(),
        authenticated_root_id="rag-root-v8",
        installed_release_sequence=installed_sequence,
        installed_release_id=installed_id,
    )
    print(
        json.dumps(
            {
                "result": (
                    "available"
                    if trust.release_sequence > installed_sequence
                    else "up_to_date"
                ),
                "release_id": trust.release_id,
                "release_sequence": trust.release_sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
