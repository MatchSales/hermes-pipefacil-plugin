"""Create private per-profile upload credentials; prints no secret values."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets


def write_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+")
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if len(set(args.profiles)) != len(args.profiles) or any(
        not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", name) for name in args.profiles
    ):
        parser.error("Profile namespaces must be unique lowercase names, up to 80 characters.")
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    credentials = {name: secrets.token_urlsafe(32) for name in args.profiles}
    namespaces = {hashlib.sha256(token.encode()).hexdigest(): name for name, token in credentials.items()}
    write_private(args.directory / "credentials.json", credentials)
    write_private(args.directory / "worker-secrets.json", {"UPLOAD_TOKENS_JSON": json.dumps(namespaces)})
    print(f"Created {len(credentials)} credentials in private directory {args.directory}; store them in your secret manager.")


if __name__ == "__main__":
    main()
