#!/usr/bin/env python3
"""End-to-end checks for probe.py, summarize.py and serve.sh against
tests/mock_api.py.

  python3 -m unittest discover -s tests -v
"""
import importlib.util
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
SCRIPTS = os.path.join(ROOT, "skills", "qc", "scripts")
PROBE = os.path.join(SCRIPTS, "probe.py")
SUMMARIZE = os.path.join(SCRIPTS, "summarize.py")
SERVE = os.path.join(SCRIPTS, "serve.sh")
MOCK = os.path.join(ROOT, "tests", "mock_api.py")
CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("QC_TOKEN", "QC_TOKEN_B")}


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
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait()

    def probe(self, mode, *extra, env=None):
        ProbeTest.runs += 1
        out = os.path.join(self.tmp, f"{mode}-{self.runs}.json")
        p = subprocess.run([sys.executable, PROBE, "--base", self.base, "--mode", mode,
                            "--out", out, *extra], capture_output=True, text=True,
                           env={**CLEAN_ENV, **(env or {})})
        data = None
        if os.path.exists(out) and p.returncode != 2:
            with open(out) as f:
                data = json.load(f)
        return p, data, out

    def rows(self, data, path, probe=""):
        return [r for r in data["results"] if r["path"] == path and r["probe"].startswith(probe)]

    def flagged(self, data, path, flag, probe=""):
        return [r for r in self.rows(data, path, probe) if flag in r["flags"]]

    # discovery and spec use

    def test_resolves_refs_server_prefix_and_auth(self):
        _, data, _ = self.probe("security")
        items = self.rows(data, "/api/items", "authz:no-token")
        self.assertEqual(items[0]["auth"], "required")
        self.assertIn("page=1", items[0]["url"])  # required param sent, so auth decides
        self.assertEqual(self.rows(data, "/api/search")[0]["auth"], "public")

    def test_swagger2_file_with_base_path(self):
        spec = {"swagger": "2.0", "basePath": "/api", "paths": {
            "/items": {"get": {"parameters": [
                {"name": "page", "in": "query", "required": True, "type": "integer"}]}}}}
        f = os.path.join(self.tmp, "swagger2.json")
        with open(f, "w") as fh:
            json.dump(spec, fh)
        _, data, _ = self.probe("negative", "--openapi", f)
        self.assertTrue(self.flagged(data, "/api/items", "exception-leak"))

    # negative

    def test_negative_flags_traceback_on_wrong_type(self):
        _, data, _ = self.probe("negative")
        self.assertTrue(self.flagged(data, "/api/items", "server-error", "negative:wrong-type-query"))
        self.assertTrue(self.flagged(data, "/api/items", "exception-leak"))

    def test_negative_wrong_type_path_id_and_replay_url(self):
        _, data, _ = self.probe("negative")
        hits = self.rows(data, "/api/items/{id}", "negative:wrong-type-path")
        self.assertEqual(hits[0]["status"], 404)
        self.assertEqual(hits[0]["flags"], [])
        self.assertTrue(all(r["url"].startswith(self.base) for r in data["results"]))

    def test_negative_body_probes_need_writes_and_find_missing_field(self):
        _, ro, _ = self.probe("negative", env={"QC_TOKEN": "good"})
        self.assertFalse(self.rows(ro, "/api/orders", "negative:body"))
        _, data, _ = self.probe("negative", "--allow-writes", env={"QC_TOKEN": "good"})
        missing = self.flagged(data, "/api/orders", "server-error", "negative:body-missing:name")
        self.assertTrue(missing)
        self.assertIn('"qty"', missing[0]["request_body"])
        wrong = self.rows(data, "/api/orders", "negative:body-wrong-type:qty")
        self.assertEqual((wrong[0]["status"], wrong[0]["flags"]), (400, []))

    # edge

    def test_edge_uses_schema_limits(self):
        _, data, _ = self.probe("edge", "--only", "/api/items/")
        over = self.rows(data, "/api/items/{id}", "edge:path:id=max+1")
        self.assertIn("server-error", over[0]["flags"])
        last = self.rows(data, "/api/items/{id}", "edge:path:id=max=")
        self.assertEqual(last[0]["status"], 200)

    def test_edge_body_over_maximum_is_accepted_invalid(self):
        _, data, _ = self.probe("edge", "--allow-writes", "--only", "/api/orders",
                                env={"QC_TOKEN": "good"})
        self.assertTrue(self.flagged(data, "/api/orders", "accepted-invalid", "edge:body:qty=max+1"))
        self.assertFalse(self.flagged(data, "/api/orders", "accepted-invalid", "edge:body:name=maxLength+1"))

    def test_edge_query_enum_outside_is_rejected_cleanly(self):
        _, data, _ = self.probe("edge", "--only", "/api/lookup")
        r = self.rows(data, "/api/lookup", "edge:query:code=not-in-enum")[0]
        self.assertEqual((r["status"], r["flags"]), (400, []))

    # fuzz

    def test_fuzz_is_capped_and_seeded(self):
        _, a, _ = self.probe("fuzz", "--max-requests", "20", "--seed", "7")
        self.assertEqual(a["meta"]["requests"], 20)
        self.assertTrue(a["meta"]["cap_reached"])
        _, b, _ = self.probe("fuzz", "--max-requests", "20", "--seed", "7")
        self.assertEqual([r["url"] for r in a["results"]], [r["url"] for r in b["results"]])

    def test_fuzz_bodies_record_what_was_sent(self):
        _, data, _ = self.probe("fuzz", "--allow-writes", "--only", "/api/orders",
                                "--iterations", "5", env={"QC_TOKEN": "good"})
        rows = self.rows(data, "/api/orders", "fuzz:body")
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["request_body"] for r in rows))

    # security

    def test_security_auth_follows_spec(self):
        _, data, _ = self.probe("security")
        self.assertTrue(self.flagged(data, "/api/items", "auth-not-enforced"))
        self.assertFalse(self.flagged(data, "/api/search", "auth-not-enforced"))
        self.assertFalse(self.flagged(data, "/api/secure", "auth-not-enforced"))

    def test_security_reflection_html_flagged_json_noted(self):
        _, data, _ = self.probe("security")
        self.assertTrue(self.flagged(data, "/api/search", "reflected-marker"))
        lookup = self.rows(data, "/api/lookup", "security:injection-marker")
        self.assertFalse(any("reflected-marker" in r["flags"] for r in lookup))
        self.assertTrue(any(r.get("notes") for r in lookup))

    def test_token_from_env(self):
        _, data, _ = self.probe("security", "--only", "/api/secure", env={"QC_TOKEN": "good"})
        self.assertTrue(data["meta"]["authenticated"])
        self.assertEqual(self.rows(data, "/api/secure", "headers:baseline")[0]["status"], 200)

    # scope

    def test_scope_needs_two_tokens(self):
        p, _, _ = self.probe("scope", env={"QC_TOKEN": "good"})
        self.assertEqual(p.returncode, 2)

    def test_scope_finds_cross_user_access_and_unscoped_list(self):
        _, data, _ = self.probe("scope", "--value", "id=7",
                                env={"QC_TOKEN": "good", "QC_TOKEN_B": "other"})
        self.assertTrue(self.flagged(data, "/api/orders/{id}", "cross-user-access"))
        self.assertTrue(self.flagged(data, "/api/me/orders", "same-data-both-users"))
        self.assertFalse(self.rows(data, "/api/secure")[0]["flags"])  # {"ok":true} is too small to judge
        needs = [s for s in data["meta"]["skipped"] if s["path"] == "/api/items"]
        self.assertEqual(needs[0]["needs"], ["page"])

    # flags: hangs, slowness, gates, accept rules, delay

    def test_timeout_and_slow_flags(self):
        _, data, _ = self.probe("edge", "--only", "/api/slow", "--timeout", "1",
                                "--max-requests", "3")
        self.assertTrue(self.flagged(data, "/api/slow", "timeout"))
        _, data, _ = self.probe("edge", "--only", "/api/slow", "--slow-ms", "1000",
                                "--max-requests", "3")
        self.assertTrue(self.flagged(data, "/api/slow", "slow"))

    def test_fail_on_flags_exit_code(self):
        p, _, _ = self.probe("negative", "--fail-on-flags")
        self.assertEqual(p.returncode, 1)

    def test_accept_rules_silence_flags(self):
        rules = os.path.join(self.tmp, "accept.json")
        with open(rules, "w") as f:
            json.dump([{"flag": "auth-not-enforced", "path": "/api/items", "reason": "x"}], f)
        _, data, _ = self.probe("security", "--only", "/api/items", "--accept", rules)
        rows = self.rows(data, "/api/items", "authz:")
        self.assertTrue(rows and all("auth-not-enforced" in r.get("accepted_flags", []) for r in rows))
        self.assertFalse(self.flagged(data, "/api/items", "auth-not-enforced"))

    def test_delay_spaces_requests(self):
        t0 = time.time()
        _, data, _ = self.probe("security", "--only", "/api/secure", "--max-requests", "4",
                                "--delay-ms", "150")
        self.assertEqual(data["meta"]["requests"], 4)
        self.assertGreaterEqual(time.time() - t0, 0.3)

    def test_refuses_remote_host(self):
        p = subprocess.run([sys.executable, PROBE, "--base", "http://example.com",
                            "--mode", "negative", "--out", os.path.join(self.tmp, "x.json")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("REFUSED", p.stderr)

    # summarize

    def summarize(self, *args):
        return subprocess.run([sys.executable, SUMMARIZE, *args], capture_output=True, text=True)

    def test_summary_groups_findings_and_headers(self):
        _, _, out = self.probe("security")
        s = self.summarize(out)
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("`auth-not-enforced` — `GET /api/items` — authz:no-token", s.stdout)
        self.assertIn("## Missing security headers", s.stdout)
        self.assertIn("replay: `GET http://127.0.0.1", s.stdout)

    def test_summary_baseline_diff_and_gate(self):
        _, data, before = self.probe("negative")
        # The baseline pretends /api/items' leak didn't exist yet.
        data["results"] = [r for r in data["results"] if r["path"] != "/api/items"]
        with open(before, "w") as f:
            json.dump(data, f)
        _, _, after = self.probe("negative")
        s = self.summarize(after, "--baseline", before, "--fail-on-new")
        self.assertEqual(s.returncode, 1)
        new = s.stdout.split("**New**")[1].split("**Fixed**")[0]
        self.assertIn("/api/items", new)
        self.assertEqual(self.summarize(after, "--baseline", after, "--fail-on-new").returncode, 0)

    def test_summary_accept_missing_header(self):
        _, _, out = self.probe("security", "--only", "/api/secure")
        rules = os.path.join(self.tmp, "hdr.json")
        with open(rules, "w") as f:
            json.dump([{"flag": "missing-header:" + h} for h in (
                "Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options",
                "Content-Security-Policy", "Cache-Control")], f)
        self.assertNotIn("Missing security headers", self.summarize(out, "--accept", rules).stdout)


class LeakPatternTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("probe", PROBE)
        cls.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.probe)

    def leaks(self, body):
        flags, _ = self.probe.flag({"status": 400, "body": body, "ms": 1}, "x", {})
        return "exception-leak" in flags

    def test_real_traces_are_caught(self):
        self.assertTrue(self.leaks("   at Mosa.Api.Controllers.Foo.Get() in /src/Foo.cs:line 3"))
        self.assertTrue(self.leaks("\tat com.acme.OrderService.find(OrderService.java:42)"))
        self.assertTrue(self.leaks("    at Object.<anonymous> (/app/index.js:3:9)"))
        self.assertTrue(self.leaks("goroutine 7 [running]:\nmain.main()"))
        self.assertTrue(self.leaks("ORA-00942: table or view does not exist"))

    def test_ordinary_text_is_not(self):
        self.assertFalse(self.leaks("Look at company plans at org level"))
        self.assertFalse(self.leaks("Please try again on line 2 of the form"))
        self.assertFalse(self.leaks('{"error":"Internal server error: please retry"}'))


class ServeTest(unittest.TestCase):
    def run_serve(self, *args):
        return subprocess.run(["bash", SERVE, *args], capture_output=True, text=True)

    def test_starts_then_stops_what_it_started(self):
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        name = f"qctest{port}"
        up = self.run_serve("up", "--name", name, "--url", url, "--health", "/openapi.json",
                            "--start", f"'{sys.executable}' '{MOCK}' {{port}}", "--timeout", "20")
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

            free = self.run_serve("up", "--name", f"qcfree{port}", "--url", url,
                                  "--health", "/openapi.json", "--free-port", "--timeout", "20",
                                  "--start", f"'{sys.executable}' '{MOCK}' {{port}}")
            self.assertEqual(free.returncode, 0, free.stderr)
            self.assertIn("STARTED=1", free.stdout)
            self.assertNotIn(f"BASE={url}\n", free.stdout)
            self.run_serve("down", "--name", f"qcfree{port}")
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
