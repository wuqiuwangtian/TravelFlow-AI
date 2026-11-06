"""Backend-venv entry; inspect emits paths only, serve loads the chosen profile."""

import argparse
import hmac
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def inspect_settings(args):
    from app.core.config import Settings

    values = {"app_env_file": args.env_file}
    if args.database:
        values["session_db_path"] = args.database
    try:
        settings = Settings(_env_file=args.env_file, **values)
        print(json.dumps({"database": str(settings.session_db_path.resolve())}))
        return 0
    except Exception:
        print("Configuration could not be validated", file=sys.stderr)
        return 1


def serve(args):
    import uvicorn

    if os.environ["TRAVELFLOW_RUNTIME_PROFILE"] == "application-qa":
        sys.path.insert(0, str(ROOT / "backend/tests"))
        from browser_server import app

        app.state.acceptance_database = Path(os.environ["SESSION_DB_PATH"])
    else:
        from app.main import app

    server = None

    async def wrapped(scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in (
            "/_travelflow_runtime",
            "/_travelflow_shutdown",
        ):
            return await app(scope, receive, send)
        supplied = dict(scope["headers"]).get(b"x-travelflow-control", b"")
        expected = os.environ["TRAVELFLOW_SERVICE_TOKEN"].encode()
        if not hmac.compare_digest(supplied, expected):
            await send({"type": "http.response.start", "status": 403, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        if scope["path"] == "/_travelflow_shutdown":
            status = 200 if scope["method"] == "POST" else 405
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})
            if status == 200:
                server.should_exit = True
            return
        connection = app.state.db
        async with connection.execute("PRAGMA database_list") as cursor:
            rows = await cursor.fetchall()
        database = next(str(Path(row[2]).resolve()) for row in rows if row[1] == "main")
        from app.core.config import get_settings

        config_file = str(get_settings().env_file_path.resolve())
        body = json.dumps(
            {
                "instance": os.environ["TRAVELFLOW_PROBE_INSTANCE"],
                "pid": os.getpid(),
                "role": "backend",
                "profile": os.environ["TRAVELFLOW_RUNTIME_PROFILE"],
                "database": database,
                "database_opened": True,
                "config_file": config_file,
                "config_source": "fixed-qa"
                if os.environ["TRAVELFLOW_RUNTIME_PROFILE"] == "application-qa"
                else "env-file",
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    server = uvicorn.Server(
        uvicorn.Config(
            wrapped, host="127.0.0.1", port=args.port, access_log=False, timeout_graceful_shutdown=8
        )
    )
    server.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "serve"])
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.action == "inspect":
        return inspect_settings(args)
    serve(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
