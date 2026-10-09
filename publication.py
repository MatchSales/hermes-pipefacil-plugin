"""Private publication marker; session routing changes without deleting conversations."""

import json
import re
from pathlib import Path


def read_publication(home):
    path = Path(home) / ".pipefacil-publication.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("Invalid publication marker")
    value = json.loads(path.read_text())
    if (type(value.get("revision")) is not int or value["revision"] < 1
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("contentHash", "")))):
        raise ValueError("Invalid publication marker")
    return value
