"""Operator-only journal inspection/reconciliation; never replays a turn or sends a message."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("uncertain")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--action", required=True)
    reconcile.add_argument("--outcome", choices=["accepted", "not_performed"], required=True)
    reconcile.add_argument("--receipt", type=Path)
    reconcile.add_argument("--evidence", required=True, help="Operator's external delivery/CRM evidence; do not include credentials")
    args = parser.parse_args()
    if not (args.profile / "pipefacil-state" / "inbox.sqlite3").is_file():
        parser.exit(1, "This profile has no existing Pipefacil journal. Check --profile.\n")
    name = "_pipefacil_journal_cli"
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    from importlib import import_module
    module = import_module(name + ".state")
    state = module.State(args.profile)
    try:
        if args.command == "status":
            print(json.dumps(state.status()))
        elif args.command == "uncertain":
            with state.db() as db:
                rows = [dict(row) for row in db.execute("SELECT key,job,kind,state,updated FROM actions WHERE state IN ('pending','uncertain') ORDER BY updated LIMIT 200")]
            print(json.dumps(rows))
        else:
            state.acquire()
            receipt = json.loads(args.receipt.read_text()) if args.receipt else None
            state.reconcile(args.action, args.outcome, receipt, args.evidence)
            print(json.dumps({"reconciled": True, "replayed": False}))
    except (OSError, ValueError, module.StateError):
        parser.exit(1, "Reconciliation failed. Check arguments, evidence and whether this profile's gateway is stopped.\n")
    finally:
        state.close()


if __name__ == "__main__":
    main()
