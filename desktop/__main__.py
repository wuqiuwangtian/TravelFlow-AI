"""Developer entry for the isolated D0 process-management proof."""

import argparse
import json
import sys
from pathlib import Path

from desktop.runtime import (
    AlreadyRunning,
    current,
    prepare,
    run_supervisor,
    selection,
    start,
    stop,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "start", "status", "stop", "_serve"])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--ports", type=int, nargs=2)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.action in ("start", "_serve") and not (args.probe and args.database and args.ports):
        parser.error(
            "Only --probe with explicit --database and --ports is enabled; "
            "no real application adapter"
        )
    try:
        folder = args.state_dir.resolve()
        if args.action in ("start", "_serve"):
            folder = prepare(folder)
        if args.action == "inspect":
            if not args.database:
                parser.error("inspect requires an explicit --database; .env is never loaded")
            result = selection(args.database)
        elif args.action == "status":
            result = current(folder) or {"ready": False}
        elif args.action == "stop":
            result = stop(folder)
        else:
            chosen = selection(args.database)
            if args.action == "_serve":
                run_supervisor(folder, chosen, args.ports)
                return 0
            result = start(folder, chosen, args.ports)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except AlreadyRunning:
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
