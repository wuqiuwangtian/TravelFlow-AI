"""Opt-in real Python/Next acceptance; no personal configuration or providers."""

import json
import os
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from desktop.application import application_selection, doctor, prepare_application
from desktop.runtime import AlreadyRunning, current, prepare, request, start, stop


def free_ports():
    sockets = [socket.socket(), socket.socket()]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def api(port, path, data=None):
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=payload, headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as response:
        return json.loads(response.read())


@unittest.skipUnless(
    os.environ.get("TRAVELFLOW_RUN_APP_TESTS") == "1", "Requires explicit desktop preparation"
)
class RealApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="travelflow real QA 中文 ")
        self.folder = prepare(Path(self.temporary.name) / "state")
        self.ports = free_ports()
        self.assertTrue(doctor()["ready"], doctor()["errors"])

    def tearDown(self):
        try:
            stop(self.folder)
        finally:
            self.temporary.cleanup()

    def chosen(self):
        chosen = application_selection(self.folder, "application-qa")
        chosen["frontend_build_id"] = doctor()["build_id"]
        return chosen

    def launch(self, chosen):
        try:
            return start(self.folder, chosen, self.ports)
        except RuntimeError:
            for name in ("manager.log", "services.log"):
                path = self.folder / name
                if path.exists():
                    print(name, path.read_text(errors="replace")[-5000:])
            raise

    def test_real_proxy_sqlite_and_restart_persistence(self):
        chosen = self.chosen()
        result = self.launch(chosen)
        backend, frontend = result["services"]
        self.assertEqual(backend["database"], chosen["database"])
        self.assertTrue(backend["database_opened"])
        self.assertEqual(frontend["build_id"], chosen["frontend_build_id"])
        self.assertEqual(frontend["backend_url"], f"http://127.0.0.1:{self.ports[0]}")
        created = api(self.ports[1], "/api/backend/api/sessions", {"title": "D0虚构旅程"})
        items = api(self.ports[1], "/api/backend/api/sessions")["items"]
        self.assertTrue(any(item["id"] == created["session"]["id"] for item in items))
        stop(self.folder)
        self.assertTrue(Path(chosen["database"]).is_file())
        self.launch(chosen)
        again = api(self.ports[1], "/api/backend/api/sessions")["items"]
        self.assertTrue(any(item["id"] == created["session"]["id"] for item in again))

    def test_preparation_is_rejected_while_managed_build_is_running(self):
        result = self.launch(self.chosen())
        with self.assertRaises(AlreadyRunning):
            prepare_application()
        self.assertEqual(current(self.folder)["instance"], result["instance"])

    def test_actual_app_refuses_missing_db_then_initializes_only_when_allowed(self):
        config = self.folder / "isolated.env"
        config.write_text("LLM_API_KEY=\nSEARCH_API_KEY=\nAMAP_API_KEY=\n", encoding="utf-8")
        database = self.folder / "application.sqlite"
        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "",
                "SEARCH_API_KEY": "",
                "AMAP_API_KEY": "",
                "LLM_PROVIDER": "deepseek",
                "SEARCH_PROVIDER": "tavily",
            },
        ):
            chosen = application_selection(self.folder, "application", database, config)
            chosen["frontend_build_id"] = doctor()["build_id"]
            with self.assertRaises(ValueError):
                start(self.folder, {**chosen, "database": str(self.folder)}, self.ports)
            with self.assertRaises(RuntimeError):
                start(self.folder, chosen, self.ports)
            self.assertFalse(database.exists())
            chosen["initialize_database"] = True
            result = self.launch(chosen)
        self.assertEqual(result["services"][0]["database"], str(database))
        self.assertEqual(result["services"][0]["config_file"], str(config))
        self.assertTrue(result["services"][0]["database_opened"])
        health = request(self.ports[0], "/api/system/health")
        self.assertEqual(health["status"], "ok")
        stop(self.folder)
        self.assertTrue(database.is_file())


if __name__ == "__main__":
    unittest.main()
