"""Source-mode desktop application and management commands."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from desktop.runtime import (
    ROOT,
    AlreadyRunning,
    current,
    mode,
    prepare,
    run_supervisor,
    selection,
    start,
    stop,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "console",
            "console-stop",
            "_console",
            "_connect",
            "prepare",
            "doctor",
            "inspect",
            "start",
            "status",
            "stop",
            "_serve",
        ],
    )
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--picker-python")
    parser.add_argument("--picker-distribution")
    parser.add_argument("--launch-ticket")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--ports", type=int, nargs=2)
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--probe", action="store_true")
    profile.add_argument("--qa", action="store_true")
    profile.add_argument("--app", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--allow-new-database", action="store_true")
    args = parser.parse_args()
    if args.action in ("start", "_serve") and not (
        (args.probe or args.qa or args.app) and args.ports and (args.database or not args.probe)
    ):
        parser.error("Select --probe, --qa or --app with ports; probe requires --database")
    if (args.probe or args.qa) and (args.env_file or args.allow_new_database):
        parser.error("Only --app accepts --env-file or --allow-new-database")
    try:
        folder = (
            args.state_dir
            or (
                ROOT / ".desktop-run" / ("console-" + mode())
                if args.action in ("console", "console-stop", "_connect")
                else Path(".desktop-run")
            )
        ).resolve()
        if args.action in ("start", "_serve", "console", "_console", "_connect"):
            folder = prepare(folder)
        if args.action == "_connect":
            from desktop.bridge import connect_payload

            try:
                result = connect_payload(
                    folder, args.nonce, args.picker_python, args.picker_distribution
                )
            except (OSError, ValueError, RuntimeError) as error:
                print(json.dumps({"bridge_version": 1, "nonce": args.nonce, "error": str(error)}))
                return 1
        elif args.action == "console-stop":
            from desktop.console import close_console, live_record

            was_running = bool(live_record(folder))
            if (folder / "console.json").exists() and not was_running:
                raise RuntimeError("管理器状态无法确认；已保留现场，不会按旧PID停止进程。")
            close_console(folder)
            result = {"stopped": True, "was_running": was_running}
        elif args.action in ("console", "_console"):
            from desktop.console import open_console, serve_console

            if args.action == "_console":
                serve_console(folder)
                return 0
            open_console(folder)
            result = {"opened": True}
        elif args.action in ("prepare", "doctor"):
            from desktop.application import doctor, prepare_application

            result = prepare_application() if args.action == "prepare" else doctor()
        elif args.action == "inspect":
            if args.app or args.qa:
                from desktop.application import application_selection, doctor

                chosen = application_selection(
                    folder,
                    "application" if args.app else "application-qa",
                    args.database,
                    args.env_file,
                )
                result = {
                    **chosen,
                    "database_exists": Path(chosen["database"]).is_file(),
                    "preparation": doctor(),
                }
            else:
                if not args.database:
                    parser.error("inspect requires an explicit database or --app/--qa")
                result = selection(args.database)
        elif args.action == "status":
            result = current(folder) or {"ready": False}
        elif args.action == "stop":
            result = stop(folder)
        else:
            if args.qa or args.app:
                from desktop.application import application_selection, doctor

                chosen = application_selection(
                    folder,
                    "application" if args.app else "application-qa",
                    args.database,
                    args.env_file,
                )
                chosen["frontend_build_id"] = doctor()["build_id"]
                if args.allow_new_database:
                    chosen["initialize_database"] = True
            else:
                chosen = selection(args.database)
            if args.action == "_serve":
                run_supervisor(folder, chosen, args.ports, launch_ticket=args.launch_ticket)
                return 0
            result = start(folder, chosen, args.ports)
        print(json.dumps(result, ensure_ascii=True))
        return 0 if args.action not in ("prepare", "doctor") or result["ready"] else 1
    except AlreadyRunning:
        if args.action not in ("_serve", "_console"):
            print(
                "Project is in use; stop the managed instance before preparation", file=sys.stderr
            )
        return 2
    except subprocess.CalledProcessError as error:
        print(
            f"Preparation command failed (exit {error.returncode}); inspect preceding output",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
