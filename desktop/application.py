"""Source-mode application preparation and safe metadata inspection."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from desktop.runtime import ROOT, instance_lock, mode, prepare, selection, write_json


def python_path():
    base = ROOT / "backend" / (".venv-desktop-" + mode())
    return base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def dist_name():
    return ".next-desktop-" + mode()


def source_digest():
    root = ROOT / "frontend"
    files = [
        root / n
        for n in (
            "package.json",
            "pnpm-lock.yaml",
            "next.config.ts",
            "tsconfig.json",
            "postcss.config.mjs",
        )
    ]
    for name in ("app", "components", "lib", "styles", "public"):
        files.extend(p for p in (root / name).rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(file.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
    return digest.hexdigest()


def build_lock():
    return instance_lock(prepare(ROOT / ".desktop-run" / ("build-" + mode())))


def node_metadata(node):
    result = subprocess.run(
        [
            node,
            "-p",
            "JSON.stringify({platform:process.platform,arch:process.arch,version:process.version})",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Node.js metadata could not be read")
    return json.loads(result.stdout)


def claim_source_platform():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is missing")
    metadata = node_metadata(node)
    expected = "win32" if os.name == "nt" else "linux"
    if metadata["platform"] != expected:
        raise RuntimeError("Node.js platform does not match this runtime mode")
    owner = {key: metadata[key] for key in ("platform", "arch")}
    path = prepare(ROOT / ".desktop-run") / "source-platform.json"
    try:
        with open(path, "x", encoding="utf-8") as stream:
            json.dump(owner, stream)
    except FileExistsError:
        if json.loads(path.read_text()) != owner:
            raise RuntimeError(
                "Use a separate checkout for another OS/architecture; "
                "shared node_modules cannot be replaced safely"
            ) from None
    return metadata


def doctor():
    errors = []
    interpreter = python_path()
    if not interpreter.is_file():
        errors.append("Backend environment is missing; run python -m desktop prepare")
    node = shutil.which("node")
    if not node:
        errors.append("Node.js is missing")
    else:
        metadata = node_metadata(node)
        if metadata["platform"] != ("win32" if os.name == "nt" else "linux"):
            errors.append("Node.js platform does not match this runtime mode")
        try:
            owner = json.loads((ROOT / ".desktop-run/source-platform.json").read_text())
            if owner != {key: metadata[key] for key in ("platform", "arch")}:
                errors.append(
                    "This checkout belongs to another OS/architecture; use a separate checkout"
                )
        except (OSError, ValueError):
            errors.append("Source platform is unregistered; run python -m desktop prepare")
    if not (ROOT / "frontend/node_modules/next/dist/bin/next").is_file():
        errors.append("Frontend dependencies are missing; run python -m desktop prepare")
    folder = ROOT / "frontend" / dist_name()
    manifest = None
    try:
        manifest = json.loads((folder / "desktop-build.json").read_text(encoding="utf-8"))
        if node and manifest.get("node_version") != metadata["version"]:
            errors.append("Node.js runtime changed; run python -m desktop prepare again")
        if manifest["source_digest"] != source_digest() or manifest["mode"] != mode():
            errors.append("Frontend source changed; prepare a new build after stopping the app")
        if manifest["build_id"] != (folder / "BUILD_ID").read_text().strip():
            errors.append("Frontend build identity differs")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        manifest = None
        errors.append("Desktop frontend build is missing; run python -m desktop prepare")
    return {
        "ready": not errors,
        "errors": errors,
        "mode": mode(),
        "backend_python": str(interpreter),
        "node": node,
        "dist": dist_name(),
        "build_id": manifest.get("build_id") if manifest else None,
    }


def prepare_application():
    uv = shutil.which("uv")
    if not uv or not shutil.which("pnpm"):
        raise RuntimeError("Preparation requires uv, Node.js and pnpm")
    with build_lock():
        node_info = claim_source_platform()
        env = {
            **os.environ,
            "UV_PROJECT_ENVIRONMENT": str(python_path().parent.parent),
            "UV_PYTHON": sys.executable,
            "TRAVELFLOW_NEXT_DIST_DIR": dist_name(),
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        subprocess.run([uv, "sync", "--frozen"], cwd=ROOT / "backend", env=env, check=True)

        def frontend(args):
            if os.name == "nt":
                # Fixed commands only; paths travel through cwd, never shell interpolation.
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", " ".join(args)]
            else:
                command = args
            subprocess.run(command, cwd=ROOT / "frontend", env=env, check=True)

        frontend(["pnpm", "install", "--frozen-lockfile"])
        manifest = ROOT / "frontend" / dist_name() / "desktop-build.json"
        manifest.unlink(missing_ok=True)
        before = source_digest()
        config = json.loads((ROOT / "frontend/tsconfig.json").read_text())
        config["include"] = [s for s in config["include"] if not s.startswith(".next/")]
        config["include"] += [dist_name() + "/types/**/*.ts", dist_name() + "/dev/types/**/*.ts"]
        config["exclude"] += [".next"] + [
            ".next-desktop-" + m for m in ("linux", "wsl", "windows-native") if m != mode()
        ]
        write_json(ROOT / "frontend" / (dist_name() + "-tsconfig.json"), config)
        frontend(["pnpm", "exec", "next", "build", "--webpack"])
        if source_digest() != before:
            raise RuntimeError(
                "Frontend source changed during preparation; rebuild a stable version"
            )
        build_id = (manifest.parent / "BUILD_ID").read_text().strip()
        write_json(
            manifest,
            {
                "mode": mode(),
                "source_digest": before,
                "build_id": build_id,
                "node_version": node_info["version"],
            },
        )
    return doctor()


def application_selection(folder, profile, database=None, env_file=None):
    if profile == "application-qa":
        database = (database or folder / "qa.sqlite").resolve()
        if not database.is_relative_to(folder):
            raise ValueError("QA database must stay inside its isolated state directory")
        chosen = selection(database)
        chosen.update(profile=profile, env_file=str(folder / "qa.env"))
        return chosen
    if not env_file or not env_file.resolve().is_file():
        raise ValueError("Application mode requires an explicit existing --env-file")
    interpreter = python_path()
    if not interpreter.is_file():
        raise RuntimeError("Prepare the backend environment before inspecting application settings")
    command = [
        str(interpreter),
        str(ROOT / "desktop/backend.py"),
        "inspect",
        "--env-file",
        str(env_file.resolve()),
    ]
    if database:
        command += ["--database", str(database.resolve())]
    result = subprocess.run(command, cwd=ROOT / "backend", capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Configuration could not be validated; no setting values were printed")
    chosen = selection(json.loads(result.stdout)["database"])
    chosen.update(
        profile="application", env_file=str(env_file.resolve()), database_config_loaded=True
    )
    return chosen


def service_specs(folder, chosen, ports, instance, proof_token):
    info = doctor()
    if not info["ready"]:
        raise RuntimeError("; ".join(info["errors"]))
    env_file = Path(chosen["env_file"])
    bypass = ",".join(
        filter(
            None,
            [os.environ.get("NO_PROXY", os.environ.get("no_proxy", "")), "127.0.0.1,localhost,::1"],
        )
    )
    env = {
        **os.environ,
        "APP_ENV_FILE": str(env_file),
        "APP_HOST": "127.0.0.1",
        "APP_PORT": str(ports[0]),
        "SESSION_DB_PATH": chosen["database"],
        "TRAVELFLOW_PROBE_INSTANCE": instance,
        "TRAVELFLOW_SERVICE_TOKEN": proof_token,
        "TRAVELFLOW_RUNTIME_PROFILE": chosen["profile"],
        "TRAVELFLOW_NEXT_DIST_DIR": dist_name(),
        "BACKEND_URL": f"http://127.0.0.1:{ports[0]}",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NO_PROXY": bypass,
        "no_proxy": bypass,
    }
    if chosen["profile"] == "application-qa":
        if env_file.resolve() != folder / "qa.env":
            raise ValueError("QA configuration must stay in its state directory")
        if not env_file.exists():
            with open(env_file, "x", encoding="utf-8") as stream:
                stream.write("# Isolated desktop QA; do not enter real provider keys\n")
        env.update(LLM_API_KEY="", SEARCH_API_KEY="", AMAP_API_KEY="")
    backend = [
        info["backend_python"],
        str(ROOT / "desktop/backend.py"),
        "serve",
        "--port",
        str(ports[0]),
    ]
    frontend = [
        info["node"],
        str(ROOT / "frontend/node_modules/next/dist/bin/next"),
        "start",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(ports[1]),
    ]
    frontend_env = {
        key: value
        for key, value in env.items()
        if key
        not in {
            "APP_ENV_FILE",
            "SESSION_DB_PATH",
            "LLM_API_KEY",
            "SEARCH_API_KEY",
            "AMAP_API_KEY",
        }
    }
    frontend_env["NODE_ENV"] = "production"
    return [
        (backend, ROOT / "backend", env, "/_travelflow_runtime", "backend"),
        (frontend, ROOT / "frontend", frontend_env, "/api/desktop/health", "frontend"),
    ]
