#!/usr/bin/env python3
"""End-to-end checks for probe.py and serve.sh against tests/mock_api.py.

  python3 -m unittest discover -s tests -v
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "skills", "qc", "scripts", "probe.py")
SERVE = os.path.join(ROOT, "skills", "qc", "scripts", "serve.sh")
MOCK = os.path.join(ROOT, "tests", "mock_api.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(url, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"{url} never came up")


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen([sys.executable, MOCK, str(cls.port)])
        wait_up(cls.base + "/openapi.json")
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait()

    def probe(self, mode, *extra, env=None):
        out = os.path.join(self.tmp, f"{mode}.json")
        p = subprocess.run([sys.executable, PROBE, "--base", self.base, "--mode", mode,
                            "--out", out, *extra], capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        data = None
        if os.path.exists(out) and p.returncode != 2:
            with open(out) as f:
                data = json.load(f)
        return p, data

    def flagged(self, data, path, flag):
        return [r for r in data["results"] if r["path"] == path and flag in r["flags"]]

    def test_discovers_openapi3_with_server_prefix(self):
        p, data = self.probe("negative")
        self.assertEqual(p.returncode, 0, p.stderr)
        paths = {r["path"] for r in data["results"]}
        self.assertIn("/api/items", paths)

    def test_negative_flags_traceback_on_wrong_type(self):
        _, data = self.probe("negative")
        hits = self.flagged(data, "/api/items", "exception-leak")
        self.assertTrue(hits)
        self.assertTrue(self.flagged(data, "/api/items", "server-error"))

    def test_negative_wrong_type_path_id_and_replay_url(self):
        _, data = self.probe("negative")
        hits = [r for r in data["results"] if r["probe"] == "negative:wrong-type-path"]
        self.assertEqual(hits[0]["status"], 404)
        self.assertTrue(all(r["url"].startswith(self.base) for r in data["results"]))

    def test_edge_flags_overflow_path_id(self):
        _, data = self.probe("edge")
        hits = [r for r in self.flagged(data, "/api/items/{id}", "server-error")
                if r["probe"].startswith("edge:path-id")]
        self.assertTrue(hits)

    def test_fuzz_is_capped_and_seeded(self):
        _, a = self.probe("fuzz", "--max-requests", "20", "--seed", "7")
        self.assertEqual(a["meta"]["requests"], 20)
        _, b = self.probe("fuzz", "--max-requests", "20", "--seed", "7")
        self.assertEqual([r["status"] for r in a["results"]],
                         [r["status"] for r in b["results"]])

    def test_security_flags_reflection_and_missing_auth_only_where_due(self):
        _, data = self.probe("security")
        self.assertTrue(self.flagged(data, "/api/search", "reflected-marker"))
        self.assertTrue(self.flagged(data, "/api/search", "auth-not-enforced"))
        self.assertFalse(self.flagged(data, "/api/secure", "auth-not-enforced"))

    def test_token_from_env(self):
        _, data = self.probe("security", "--only", "/api/secure",
                             env={"QC_TOKEN": "good"})
        self.assertTrue(data["meta"]["authenticated"])
        baseline = [r for r in data["results"] if r["probe"] == "headers:baseline"]
        self.assertEqual(baseline[0]["status"], 200)

    def test_fail_on_flags_exit_code(self):
        p, _ = self.probe("negative", "--fail-on-flags")
        self.assertEqual(p.returncode, 1)

    def test_swagger2_file_with_base_path(self):
        spec = {"swagger": "2.0", "basePath": "/api", "paths": {
            "/items": {"get": {"parameters": [
                {"name": "page", "in": "query", "required": True, "type": "integer"}]}}}}
        f = os.path.join(self.tmp, "swagger2.json")
        with open(f, "w") as fh:
            json.dump(spec, fh)
        _, data = self.probe("negative", "--openapi", f)
        self.assertTrue(self.flagged(data, "/api/items", "exception-leak"))

    def test_refuses_remote_host(self):
        p = subprocess.run([sys.executable, PROBE, "--base", "http://example.com",
                            "--mode", "negative", "--out", os.path.join(self.tmp, "x.json")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("REFUSED", p.stderr)


class ServeTest(unittest.TestCase):
    def run_serve(self, *args):
        return subprocess.run(["bash", SERVE, *args], capture_output=True, text=True)

    def test_starts_then_stops_what_it_started(self):
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        name = f"qctest{port}"
        up = self.run_serve("up", "--name", name, "--url", url, "--health", "/openapi.json",
                            "--start", f"'{sys.executable}' '{MOCK}' {port}", "--timeout", "20")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertIn(f"BASE={url}", up.stdout)
        self.assertIn("STARTED=1", up.stdout)
        self.assertEqual(self.run_serve("down", "--name", name).returncode, 0)
        time.sleep(0.5)
        with self.assertRaises(Exception):
            urllib.request.urlopen(url + "/openapi.json", timeout=1)

    def test_refuses_a_different_program_on_the_port(self):
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        other = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                                  "--bind", "127.0.0.1"], cwd=tempfile.mkdtemp(),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_up(url + "/")
            up = self.run_serve("up", "--name", f"qcother{port}", "--url", url,
                                "--health", "/openapi.json",
                                "--start", f"'{sys.executable}' '{MOCK}' {port}")
            self.assertEqual(up.returncode, 1)
            self.assertNotIn("BASE=", up.stdout)
            self.assertIn("something else answers", up.stderr)
        finally:
            other.terminate()
            other.wait()

    def test_reuses_running_service_without_owning_it(self):
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        srv = subprocess.Popen([sys.executable, MOCK, str(port)])
        try:
            wait_up(url + "/openapi.json")
            up = self.run_serve("up", "--name", f"qcreuse{port}", "--url", url,
                                "--health", "/openapi.json")
            self.assertIn("STARTED=0", up.stdout)
            self.run_serve("down", "--name", f"qcreuse{port}")
            self.assertIsNone(srv.poll())  # still running
        finally:
            srv.terminate()
            srv.wait()


if __name__ == "__main__":
    unittest.main()
