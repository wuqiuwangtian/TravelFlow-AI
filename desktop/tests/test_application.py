"""Application preflight tests that do not require installed app dependencies."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.application import (
    application_selection,
    dist_name,
    doctor,
    service_specs,
    source_digest,
)
from desktop.runtime import prepare


class ApplicationPreflightTests(unittest.TestCase):
    def test_qa_rejects_external_database_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory) / "state")
            outside = Path(directory) / "outside.sqlite"
            with self.assertRaises(ValueError):
                application_selection(folder, "application-qa", outside)
            self.assertFalse(outside.exists())

    def test_application_requires_explicit_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with self.assertRaises(ValueError):
                application_selection(folder, "application", env_file=folder / "missing.env")

    def test_source_change_invalidates_prepared_build(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("desktop.application.shutil.which", return_value=None),
        ):
            root = Path(directory)
            frontend = root / "frontend"
            frontend.mkdir()
            for name in (
                "package.json",
                "pnpm-lock.yaml",
                "next.config.ts",
                "tsconfig.json",
                "postcss.config.mjs",
            ):
                (frontend / name).write_text("fixture\n")
            (frontend / "app").mkdir()
            page = frontend / "app/page.tsx"
            page.write_text("original\n")
            build = frontend / dist_name()
            build.mkdir()
            (build / "BUILD_ID").write_text("build-one")
            with patch("desktop.application.ROOT", root):
                (build / "desktop-build.json").write_text(
                    json.dumps(
                        {
                            "mode": doctor()["mode"],
                            "build_id": "build-one",
                            "source_digest": source_digest(),
                        }
                    )
                )
                self.assertFalse(any("source changed" in e for e in doctor()["errors"]))
                page.write_text("new source\n")
                self.assertTrue(any("source changed" in e for e in doctor()["errors"]))

    def test_qa_clears_ambient_keys_and_uses_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory))
            chosen = application_selection(folder, "application-qa")
            prepared = {
                "ready": True,
                "backend_python": "python",
                "node": "node",
                "build_id": "fixture",
            }
            with (
                patch("desktop.application.doctor", return_value=prepared),
                patch.dict(
                    os.environ,
                    {
                        "LLM_API_KEY": "fixture-not-a-real-key",
                        "SEARCH_API_KEY": "fixture-not-a-real-key",
                        "AMAP_API_KEY": "fixture-not-a-real-key",
                    },
                ),
            ):
                specs = service_specs(folder, chosen, [18520, 18521], "instance", "proof-token")
            backend_env = specs[0][2]
            for name in ("LLM_API_KEY", "SEARCH_API_KEY", "AMAP_API_KEY"):
                self.assertEqual(backend_env[name], "")
                self.assertNotIn(name, specs[1][2])
            self.assertEqual(Path(backend_env["APP_ENV_FILE"]), folder / "qa.env")
            self.assertTrue((folder / "qa.env").exists())


if __name__ == "__main__":
    unittest.main()
