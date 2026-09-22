import ast
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import genos_panel as panel


ORIGIN = "https://genos.example"
SAMPLE = {
    "servers": [
        {
            "id": "server-1",
            "name": "Alpha <b> & Bravo",
            "game": {"name": "Factorio"},
            "status": "Running",
            "notableUpdates": ["build"],
            "metrics": {"playerCount": 4},
        }
    ]
}


class MenuTests(unittest.TestCase):
    def test_menu_fields_and_exact_actions(self):
        rows = panel.parse_menu(SAMPLE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(tuple(row.keys()), panel.MENU_FIELDS)
        self.assertEqual(row["id"], "server-1")
        self.assertEqual(row["name"], "Alpha <b> & Bravo")
        self.assertEqual(row["gameName"], "Factorio")
        self.assertEqual(row["status"], "Running")
        self.assertEqual(row["playerCount"], 4)
        self.assertEqual(row["notableUpdates"], ["build"])
        self.assertEqual(row["actions"], ["start", "stop", "restart"])
        self.assertEqual(panel.actions_for_server({"forceStopAvailable": True}), ["start", "stop", "restart"])
        self.assertEqual(list(panel.SERVER_ACTIONS), ["start", "stop", "restart"])
        for word in ("force-stop", "configure", "mods", "files", "saves", "console"):
            self.assertNotIn(word, panel.SERVER_ACTIONS)
            self.assertNotIn(word, row["actions"])
        self.assertNotIn("<", row["tooltip"])
        self.assertNotIn(">", row["tooltip"])
        self.assertNotIn("&", row["tooltip"])
        self.assertIn("Alpha", row["tooltip"])
        self.assertIn("Bravo", row["tooltip"])
        self.assertTrue(row["restartNeedsConfirm"])
        self.assertIn("Alpha", row["confirmStop"])
        self.assertNotIn("<", row["confirmStop"])

    def test_rejects_too_many_servers(self):
        payload = {
            "servers": [
                {"id": f"s{i}", "name": "N", "game": {"name": "G"}, "status": "Stopped"}
                for i in range(panel.SERVER_MAX + 1)
            ]
        }
        with self.assertRaises(panel.ParseError):
            panel.parse_menu(payload)

    def test_missing_metrics_are_null_and_updates_default_empty(self):
        rows = panel.parse_menu({"servers": [{"id": "server-1", "name": "Alpha", "status": "Stopped"}]})
        self.assertIsNone(rows[0]["playerCount"])
        self.assertEqual(rows[0]["notableUpdates"], [])
        self.assertFalse(rows[0]["restartNeedsConfirm"])


class ConfirmTests(unittest.TestCase):
    def row(self, **changes):
        base = {"name": "Alpha", "playerCount": 0, "notableUpdates": []}
        base.update(changes)
        return base

    def test_confirm_rules(self):
        quiet = self.row()
        self.assertFalse(panel.needs_confirmation("start", self.row(playerCount=5, notableUpdates=["x"])))
        self.assertTrue(panel.needs_confirmation("stop", quiet))
        self.assertIn("Alpha", panel.confirm_message("stop", quiet))
        self.assertFalse(panel.needs_confirmation("restart", quiet))
        self.assertFalse(panel.needs_confirmation("restart", self.row(playerCount=None)))
        self.assertTrue(panel.needs_confirmation("restart", self.row(playerCount=1)))
        self.assertTrue(panel.needs_confirmation("restart", self.row(notableUpdates=["map"])))
        marked = panel.confirm_message("stop", {"name": "A <b> & B"})
        self.assertNotIn("<", marked)
        self.assertNotIn(">", marked)
        self.assertNotIn("&", marked)
        self.assertIn("A", marked)
        self.assertIn("B", marked)

    def test_action_gate_does_not_post_until_confirmed(self):
        calls = []

        class Transport:
            def request(self, origin, method, path, headers, body=None):
                calls.append(method)
                if method == "GET":
                    payload = {
                        "servers": [
                            {
                                "id": "server-1",
                                "name": "Alpha",
                                "game": {"name": "Factorio"},
                                "status": "Running",
                                "notableUpdates": [],
                                "metrics": {"playerCount": 2},
                            }
                        ]
                    }
                    return 200, json.dumps(payload).encode()
                raise AssertionError("action was sent")

        credential = panel.Resolved("sekret-value", "env")
        with self.assertRaises(panel.ConfirmRequired) as caught:
            panel.do_action("server-1", "stop", False, origin=ORIGIN, credential=credential, transport=Transport())
        self.assertIn("Alpha", caught.exception.message)
        self.assertEqual(calls, ["GET"])
        calls.clear()
        with self.assertRaises(panel.ConfirmRequired):
            panel.do_action("server-1", "restart", False, origin=ORIGIN, credential=credential, transport=Transport())
        self.assertEqual(calls, ["GET"])

        class Posting(Transport):
            def request(self, origin, method, path, headers, body=None):
                calls.append((method, path, headers, body))
                if method == "GET":
                    return Transport.request(self, origin, method, path, headers, body)
                return 202, b'{"disposition":"accepted"}'

        calls.clear()
        started = panel.do_action("server-1", "start", False, origin=ORIGIN, credential=credential, transport=Posting())
        self.assertEqual(started["action"], "start")
        post = calls[-1]
        self.assertEqual(post[0], "POST")
        self.assertEqual(post[1], "/api/v1/servers/server-1/actions")
        self.assertEqual(json.loads(post[3]), {"type": "start", "confirmUnsavedProgressLoss": False})
        self.assertIn("Idempotency-Key", post[2])
        self.assertEqual(post[2]["Authorization"], "Bearer sekret-value")
        self.assertNotIn("sekret-value", post[1])
        self.assertNotIn(b"sekret-value", post[3])
        self.assertNotIn("sekret-value", json.dumps(started))

        calls.clear()
        stopped = panel.do_action("server-1", "stop", True, origin=ORIGIN, credential=credential, transport=Posting())
        self.assertEqual(json.loads(calls[-1][3])["type"], "stop")
        self.assertEqual(stopped["status"], 202)
        calls.clear()
        panel.do_action("server-1", "restart", True, origin=ORIGIN, credential=credential, transport=Posting())
        self.assertEqual(
            json.loads(calls[-1][3]),
            {"type": "restart", "confirmUnsavedProgressLoss": False},
        )


class CredentialTests(unittest.TestCase):
    def test_resolver_call_order_with_fakes(self):
        calls = []

        def keyring(origin):
            calls.append(("keyring", origin))
            return None

        def read_file(origin):
            calls.append(("file", origin))
            return "file-token"

        resolved = panel.resolve_credential(
            ORIGIN,
            environ={},
            keyring_lookup=keyring,
            read_file=read_file,
        )
        self.assertEqual(calls, [("keyring", ORIGIN), ("file", ORIGIN)])
        self.assertEqual(resolved.source, "file")
        self.assertEqual(resolved.token, "file-token")

        calls.clear()
        resolved = panel.resolve_credential(
            ORIGIN,
            environ={"GENOS_TOKEN": "env-token"},
            keyring_lookup=keyring,
            read_file=read_file,
        )
        self.assertEqual(calls, [])
        self.assertEqual((resolved.source, resolved.token), ("env", "env-token"))

        def keyring_hit(origin):
            calls.append(("keyring", origin))
            return "key-token"

        calls.clear()
        resolved = panel.resolve_credential(
            ORIGIN,
            environ={},
            keyring_lookup=keyring_hit,
            read_file=read_file,
        )
        self.assertEqual(calls, [("keyring", ORIGIN)])
        self.assertEqual(resolved.source, "keyring")

    def test_keyring_lookup_never_receives_genos_command(self):
        seen = []

        def run(argv, input_bytes=None, timeout=10):
            seen.append(list(argv))
            self.assertNotEqual(os.path.basename(argv[0]), "genos")
            self.assertEqual(input_bytes, None)
            return panel.CommandResult(1, b"", b"")

        self.assertIsNone(panel.keyring_lookup(ORIGIN, run=run))
        self.assertEqual(
            seen,
            [
                ["/usr/bin/secret-tool", "lookup", "service", "genos", "host", ORIGIN],
                ["/usr/bin/secret-tool", "lookup", "service", "genos", "username", ORIGIN],
            ],
        )

    def test_store_puts_token_on_stdin(self):
        seen = []

        def run(argv, input_bytes=None, timeout=10):
            seen.append((list(argv), input_bytes))
            self.assertNotEqual(os.path.basename(argv[0]), "genos")
            self.assertNotIn(b"pasted-token", " ".join(argv).encode())
            return panel.CommandResult(0, b"", b"")

        where = panel.store_token(ORIGIN, "pasted-token", run=run, write_file=lambda *_args: self.fail("file"))
        self.assertEqual(where, "keyring")
        self.assertEqual(seen[0][1], b"pasted-token")
        self.assertEqual(seen[0][0][0], "/usr/bin/secret-tool")

    def test_run_command_refuses_genos_without_spawning(self):
        with self.assertRaises(panel.CredentialError):
            panel.run_command(["genos", "auth", "token"])
        with self.assertRaises(panel.CredentialError):
            panel.run_command(["/usr/bin/genos"])

    def test_0644_file_is_refused_and_0600_is_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            os.chmod(temporary, 0o700)
            path = os.path.join(temporary, "credentials.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"hosts": {ORIGIN: {"token": "sekret"}}}, handle)
            os.chmod(path, 0o644)

            def read_file(origin):
                return panel.read_token_file(temporary, origin)

            with self.assertRaises(panel.CredentialError) as caught:
                panel.resolve_credential(ORIGIN, environ={}, keyring_lookup=lambda _origin: None, read_file=read_file)
            self.assertIn("chmod 0600", str(caught.exception))
            self.assertNotIn("sekret", str(caught.exception))

            os.chmod(path, 0o600)
            resolved = panel.resolve_credential(
                ORIGIN,
                environ={},
                keyring_lookup=lambda _origin: None,
                read_file=read_file,
            )
            self.assertEqual(resolved.token, "sekret")
            self.assertEqual(resolved.source, "file")

    def test_env_skips_a_loose_file(self):
        def read_file(_origin):
            raise AssertionError("file was read")

        def keyring(_origin):
            raise AssertionError("keyring was read")

        resolved = panel.resolve_credential(
            ORIGIN,
            environ={"GENOS_TOKEN": "env-token"},
            keyring_lookup=keyring,
            read_file=read_file,
        )
        self.assertEqual(resolved.source, "env")

    def test_file_fallback_is_0600_and_replaces_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            os.chmod(temporary, 0o700)
            victim = os.path.join(temporary, "victim")
            with open(victim, "w", encoding="utf-8") as handle:
                handle.write("must survive")
            link = os.path.join(temporary, "credentials.json")
            os.symlink(victim, link)

            def run(argv, input_bytes=None, timeout=10):
                self.assertNotEqual(os.path.basename(argv[0]), "genos")
                self.assertEqual(input_bytes, b"file-token")
                return panel.CommandResult(1, b"", b"")

            where = panel.store_token(
                ORIGIN,
                "file-token",
                run=run,
                write_file=lambda origin, token: panel.write_token_file(temporary, origin, token),
            )
            self.assertEqual(where, "file")
            with open(victim, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "must survive")
            self.assertFalse(os.path.islink(link))
            self.assertEqual(stat.S_IMODE(os.stat(link).st_mode), 0o600)
            document = json.loads(Path(link).read_text(encoding="utf-8"))
            self.assertEqual(document["hosts"][ORIGIN]["token"], "file-token")

    def test_config_directory_write_and_current_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            os.chmod(temporary, 0o700)
            env = {"XDG_CONFIG_HOME": temporary, "HOME": temporary}
            panel.write_token_to_config("http://127.0.0.1:8000", "local-token", env)
            directory = os.path.join(temporary, "genos")
            path = os.path.join(directory, "credentials.json")
            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(panel.read_token_from_config("http://127.0.0.1:8000", env), "local-token")
            config = os.path.join(directory, "config.toml")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write('current = "http://example.com"\ncurrentHost = "http://genos.localhost:8000"\n')
            self.assertEqual(panel.resolve_origin(env), "http://genos.localhost:8000")
            self.assertEqual(panel.resolve_origin({"GENOS_HOST": "https://genos.example"}, read_config=lambda: "http://example.com"), ORIGIN)

    def test_origin_rules(self):
        self.assertEqual(panel.canonical_origin("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(panel.canonical_origin("http://localhost:8000"), "http://localhost:8000")
        self.assertEqual(panel.canonical_origin("http://genos.localhost:8000/"), "http://genos.localhost:8000")
        self.assertEqual(panel.canonical_origin("https://Genos.Example/"), "https://genos.example")
        for raw in ("http://example.com", "http://127.0.0.1.example.com", "https://user:pass@genos.example", "ftp://genos.example"):
            with self.assertRaises(panel.OriginError):
                panel.canonical_origin(raw)


class SourceTests(unittest.TestCase):
    def test_python_source_has_no_genos_subprocess_command(self):
        source = (ROOT / "genos_panel.py").read_text(encoding="utf-8")
        self.assertNotIn("local.env", source)
        tree = ast.parse(source)
        command_names = {"run", "Popen", "call", "check_call", "check_output", "run_command", "system", "execv", "execve"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name not in command_names:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self.assertNotEqual(os.path.basename(first.value), "genos")
            if isinstance(first, ast.List) and first.elts and isinstance(first.elts[0], ast.Constant):
                value = first.elts[0].value
                if isinstance(value, str):
                    self.assertNotEqual(os.path.basename(value), "genos")


class FakeServerTests(unittest.TestCase):
    def setUp(self):
        self.state = {"hits": [], "polls": 0, "mode": "menu", "servers": []}
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def _read(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length > 65536:
                    return b""
                return self.rfile.read(length) if length else b""

            def _send(self, code, body, location=None):
                data = body if isinstance(body, bytes) else body.encode()
                self.send_response(code)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _hit(self, body):
                state["hits"].append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "idempotency": self.headers.get("Idempotency-Key"),
                        "body": body,
                    }
                )

            def do_GET(self):
                self._hit(b"")
                if state["mode"] == "redirect":
                    self._send(302, b"", location="http://127.0.0.1/stolen")
                    return
                if state["mode"] == "big":
                    self._send(200, b"x" * (panel.BODY_MAX + 1))
                    return
                self._send(200, json.dumps({"servers": state["servers"]}).encode())

            def do_POST(self):
                body = self._read()
                self._hit(body)
                if self.path == "/api/v1/auth/device/codes":
                    uri = f"http://127.0.0.1:{state['port']}/device"
                    self._send(
                        200,
                        json.dumps(
                            {
                                "deviceCode": "device-1",
                                "userCode": "ABCD-EFGH",
                                "verificationUri": uri,
                                "interval": 0,
                                "expiresIn": 30,
                            }
                        ).encode(),
                    )
                    return
                if self.path == "/api/v1/auth/device/tokens":
                    state["polls"] += 1
                    if state["polls"] == 1:
                        self._send(400, b'{"error":"authorization_pending"}')
                    else:
                        self._send(200, b'{"token":"issued-token"}')
                    return
                if self.path.endswith("/actions"):
                    self._send(202, b'{"disposition":"accepted"}')
                    return
                self._send(404, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.state["port"] = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.state['port']}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_redirect_is_not_followed_and_body_is_capped(self):
        self.state["mode"] = "redirect"
        with self.assertRaises(panel.ProtocolError) as caught:
            panel.http_request(self.origin, "GET", "/api/v1/servers", {"Accept": "application/json"}, None)
        self.assertIn("redirect refused", str(caught.exception))
        self.assertEqual(len(self.state["hits"]), 1)
        self.state["hits"].clear()
        self.state["mode"] = "big"
        with self.assertRaises(panel.ProtocolError) as caught:
            panel.http_request(self.origin, "GET", "/api/v1/servers", {"Accept": "application/json"}, None)
        self.assertIn("exceeds", str(caught.exception))

    def test_list_and_quiet_restart_against_fake_server(self):
        self.state["servers"] = [
            {
                "id": "server-1",
                "name": "Alpha",
                "game": {"name": "Factorio"},
                "status": "Stopped",
                "notableUpdates": [],
                "metrics": {"playerCount": 0},
            }
        ]
        credential = panel.Resolved("sekret-value", "env")
        listed = panel.do_list(origin=self.origin, credential=credential, transport=panel.HttpTransport())
        self.assertEqual(listed["servers"][0]["gameName"], "Factorio")
        self.assertEqual(listed["servers"][0]["actions"], ["start", "stop", "restart"])
        self.assertNotIn("sekret-value", json.dumps(listed))
        self.assertEqual(self.state["hits"][0]["authorization"], "Bearer sekret-value")
        self.assertEqual(self.state["hits"][0]["path"], "/api/v1/servers")
        acted = panel.do_action(
            "server-1",
            "restart",
            False,
            origin=self.origin,
            credential=credential,
            transport=panel.HttpTransport(),
        )
        self.assertEqual(acted["action"], "restart")
        post = self.state["hits"][-1]
        self.assertEqual(post["path"], "/api/v1/servers/server-1/actions")
        self.assertEqual(json.loads(post["body"]), {"type": "restart", "confirmUnsavedProgressLoss": False})
        self.assertTrue(post["idempotency"])
        self.assertNotIn(b"sekret-value", post["body"])

    def test_device_login_polls_and_stores_without_leaking_the_token(self):
        stored = {}

        def store(origin, token):
            stored["origin"] = origin
            stored["token"] = token
            return "keyring"

        events = panel.device_login(self.origin, transport=panel.HttpTransport(), store=store, sleep=lambda _seconds: None)
        blob = json.dumps(events)
        self.assertNotIn("issued-token", blob)
        self.assertEqual(stored["token"], "issued-token")
        self.assertEqual(stored["origin"], self.origin)
        self.assertEqual(events[0]["userCode"], "ABCD-EFGH")
        self.assertTrue(str(events[0]["verificationUri"]).startswith(self.origin))
        self.assertEqual(self.state["polls"], 2)
        for hit in self.state["hits"]:
            self.assertIsNone(hit["authorization"])
            self.assertNotIn("genos", os.path.basename(hit["path"]))


if __name__ == "__main__":
    unittest.main()
