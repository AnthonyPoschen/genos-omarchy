#!/usr/bin/python3
"""Genos bar helper. Calls the HTTP API and does not run the genos program."""

from __future__ import annotations

import json
import os
import re
import secrets
import select
import socket
import ssl
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import http.client

SECRET_TOOL = "/usr/bin/secret-tool"
SERVICE_NAME = "genos"
SERVER_ACTIONS = ("start", "stop", "restart")
MENU_FIELDS = (
    "id",
    "name",
    "gameName",
    "status",
    "playerCount",
    "notableUpdates",
    "actions",
    "tooltip",
    "restartNeedsConfirm",
    "confirmStop",
    "confirmRestart",
    "selectedSetupID",
    "selectedSetupName",
    "canChangeProfile",
)
SETUP_FIELDS = (
    "id",
    "name",
    "gameName",
    "selected",
)
SETUP_MAX = 64
STOPPED_STATUS = "Stopped"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "genos.localhost"})
DEFAULT_ORIGIN = "https://genosservers.com"
BODY_MAX = 262144
CRED_MAX = 65536
CONFIG_MAX = 65536
TOKEN_MAX = 4096
SERVER_MAX = 64
NAME_MAX = 80
TEXT_MAX = 80
UPDATE_MAX = 120
UPDATES_MAX = 20
PLAYER_MAX = 1_000_000
HTTP_TIMEOUT = 10
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MARKUP = str.maketrans({ord("<"): None, ord(">"): None, ord("&"): None})
_CONTROLS = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}


class PanelError(Exception):
    code = "failed"


class CredentialError(PanelError):
    code = "credentials"


class OriginError(CredentialError):
    code = "origin"


class ParseError(PanelError):
    code = "failed"


class ProtocolError(PanelError):
    code = "failed"


class ConfirmRequired(PanelError):
    def __init__(self, action: str, server_id: str, message: str) -> None:
        super().__init__(message)
        self.action = action
        self.server_id = server_id
        self.message = message


@dataclass(frozen=True)
class Resolved:
    token: str
    source: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def actions_for_server(_server: Mapping[str, object] | None = None) -> list[str]:
    return list(SERVER_ACTIONS)


def _sanitize(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    kept: list[str] = []
    for char in value:
        code = ord(char)
        if code < 32 or code == 127 or 128 <= code <= 159 or code in _CONTROLS:
            continue
        kept.append(char)
        if len(kept) >= limit:
            break
    return "".join(kept)


def tooltip_text(value: object, limit: int = TEXT_MAX) -> str:
    return _sanitize(value, limit).translate(_MARKUP)


def display_text(value: object, limit: int = NAME_MAX) -> str:
    return _sanitize(value, limit)


def _whole_number(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"{label} must be a whole number")
    if value < 0 or value > PLAYER_MAX:
        raise ParseError(f"{label} is out of range")
    return value


def _string_list(value: object, limit: int, item_limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ParseError("notableUpdates must be a list")
    if len(value) > limit:
        raise ParseError("notableUpdates is too long")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ParseError("notableUpdates must contain strings")
        if len(item) > item_limit:
            raise ParseError("notable update is too long")
        items.append(item)
    return items


def parse_menu(document: object) -> list[dict[str, object]]:
    if not isinstance(document, dict) or not isinstance(document.get("servers"), list):
        raise ParseError("server list is invalid")
    servers = document["servers"]
    if len(servers) > SERVER_MAX:
        raise ParseError("server list is too long")
    rows: list[dict[str, object]] = []
    for entry in servers:
        if not isinstance(entry, dict):
            raise ParseError("server list is invalid")
        server_id = entry.get("id")
        if not isinstance(server_id, str) or _ID_RE.fullmatch(server_id) is None:
            raise ParseError("server id is invalid")
        if not isinstance(entry.get("name"), str):
            raise ParseError("server name is invalid")
        game = entry.get("game", {})
        if game is None:
            game = {}
        if not isinstance(game, dict) or not isinstance(game.get("name", ""), str):
            raise ParseError("game name is invalid")
        status = entry.get("status", "")
        if not isinstance(status, str):
            raise ParseError("status is invalid")
        metrics = entry.get("metrics")
        if metrics is None:
            player_value = None
        elif not isinstance(metrics, dict):
            raise ParseError("metrics are invalid")
        else:
            player_value = metrics.get("playerCount")
        name = display_text(entry.get("name"), NAME_MAX) or "server"
        updates = _string_list(entry.get("notableUpdates"), UPDATES_MAX, UPDATE_MAX)
        selected_setup_id = entry.get("selectedSetupID", "")
        if selected_setup_id is None:
            selected_setup_id = ""
        if not isinstance(selected_setup_id, str):
            raise ParseError("selectedSetupID is invalid")
        if selected_setup_id != "" and _ID_RE.fullmatch(selected_setup_id) is None:
            raise ParseError("selectedSetupID is invalid")
        selected_setup_name = entry.get("selectedSetupName", "")
        if selected_setup_name is None:
            selected_setup_name = ""
        if not isinstance(selected_setup_name, str):
            raise ParseError("selectedSetupName is invalid")
        status_text = display_text(status, TEXT_MAX)
        row = {
            "id": server_id,
            "name": name,
            "gameName": display_text(game.get("name", ""), NAME_MAX),
            "status": status_text,
            "playerCount": _whole_number(player_value, "playerCount"),
            "notableUpdates": updates,
            "actions": actions_for_server(entry),
            "tooltip": tooltip_text(name),
            "restartNeedsConfirm": False,
            "confirmStop": "",
            "confirmRestart": "",
            "selectedSetupID": selected_setup_id,
            "selectedSetupName": display_text(selected_setup_name, NAME_MAX),
            "canChangeProfile": status_text == STOPPED_STATUS,
        }
        row["restartNeedsConfirm"] = needs_confirmation("restart", row)
        row["confirmStop"] = confirm_message("stop", row)
        row["confirmRestart"] = confirm_message("restart", row) if row["restartNeedsConfirm"] else ""
        rows.append({key: row[key] for key in MENU_FIELDS})
    return rows


def needs_confirmation(action: str, server: Mapping[str, object]) -> bool:
    if action not in SERVER_ACTIONS:
        raise ProtocolError("unsupported action")
    if action == "start":
        return False
    if action == "stop":
        return True
    players = server.get("playerCount")
    if isinstance(players, bool) or (players is not None and not isinstance(players, int)):
        raise ParseError("playerCount must be a whole number")
    updates = server.get("notableUpdates")
    if updates is None:
        updates = []
    if not isinstance(updates, list):
        raise ParseError("notableUpdates must be a list")
    return bool(isinstance(players, int) and players > 0) or len(updates) > 0


def confirm_message(action: str, server: Mapping[str, object]) -> str:
    name = tooltip_text(server.get("name") or "") or "this server"
    if action == "stop":
        return f"Stop {name}?"
    if action == "restart":
        return f"Restart {name}?"
    return ""


def canonical_origin(raw: str) -> str:
    if not isinstance(raw, str):
        raise OriginError("origin is invalid")
    text = raw.strip()
    if text == "" or any(char in text for char in "\r\n\x00\t "):
        raise OriginError("origin is invalid")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise OriginError("origin is invalid") from None
    if parts.username or parts.password:
        raise OriginError("origin must not include userinfo")
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise OriginError("origin must be a scheme and host")
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("https", "http") or host == "":
        raise OriginError("origin scheme must be https, or http on loopback")
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        raise OriginError("http is only allowed for loopback origins")
    if ":" in host:
        netloc = f"[{host}]:{port}" if port else f"[{host}]"
    else:
        netloc = f"{host}:{port}" if port else host
    return f"{scheme}://{netloc}"


def current_host_from_toml(text: str) -> str | None:
    current_host = None
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        match = re.fullmatch(
            r"""(currentHost|current)\s*=\s*(?:"([^"\n]*)"|'([^'\n]*)')""",
            stripped,
        )
        if match is None:
            continue
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if match.group(1) == "currentHost" and current_host is None:
            current_host = value
        elif match.group(1) == "current" and current is None:
            current = value
    if current_host:
        return current_host
    return current or None


def resolve_origin(
    environ: Mapping[str, str] | None = None,
    *,
    read_config: Callable[[], str | None] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    raw = env.get("GENOS_HOST", "")
    if isinstance(raw, str) and raw.strip():
        return canonical_origin(raw)
    if read_config is None:
        read_config = lambda: read_current_host(env)
    found = read_config()
    if not isinstance(found, str) or found.strip() == "":
        raise OriginError("set GENOS_HOST or currentHost in the Genos config")
    return canonical_origin(found)


def _validate_token(token: str) -> None:
    if not isinstance(token, str) or token.strip() == "" or len(token) > TOKEN_MAX:
        raise CredentialError("token is invalid")
    if any(char in token for char in "\r\n\x00"):
        raise CredentialError("token is invalid")
    try:
        token.encode("latin-1")
    except UnicodeEncodeError:
        raise CredentialError("token is invalid") from None


def resolve_credential(
    origin: str,
    *,
    settings_token: str | None = None,
    environ: Mapping[str, str] | None = None,
    keyring_lookup: Callable[[str], str | None] | None = None,
    read_file: Callable[[str], str | None] | None = None,
) -> Resolved:
    if settings_token is not None:
        token = settings_token.strip()
        if token == "":
            raise CredentialError("Authentication not configured.")
        _validate_token(token)
        return Resolved(token, "settings")
    env = os.environ if environ is None else environ
    token = env.get("GENOS_TOKEN")
    if isinstance(token, str) and token.strip() != "":
        _validate_token(token)
        return Resolved(token, "env")
    if keyring_lookup is None:
        keyring_lookup = globals()["keyring_lookup"]
    found = keyring_lookup(origin)
    if found:
        _validate_token(found)
        return Resolved(found, "keyring")
    if read_file is None:
        read_file = lambda item: read_token_from_config(item, env)
    found = read_file(origin)
    if found:
        _validate_token(found)
        return Resolved(found, "file")
    raise CredentialError("Authentication not configured.")


def _path_parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise CredentialError("config path must be absolute")
    parts: list[str] = []
    for name in path.split("/"):
        if name in ("", "."):
            continue
        if name == ".." or "/" in name:
            raise CredentialError("refusing config path")
        parts.append(name)
    if not parts:
        raise CredentialError("refusing config path")
    return parts


def open_directory(path: str) -> int:
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in _path_parts(path):
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise CredentialError("config directory is not owned by you")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_child(parent_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise CredentialError("config directory is not owned by you")
        if name == "genos" and info.st_mode & 0o077:
            raise CredentialError("Genos config directory is too open; chmod 0700 it")
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_genos_config_dir(environ: Mapping[str, str] | None = None, *, create: bool) -> int:
    env = os.environ if environ is None else environ
    config_home = env.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        parent = open_directory(config_home)
    else:
        home = env.get("HOME", "")
        if home.strip() == "":
            raise CredentialError("HOME is not set")
        home_fd = open_directory(home)
        try:
            parent = _open_child(home_fd, ".config", create=create)
        finally:
            os.close(home_fd)
    try:
        return _open_child(parent, "genos", create=create)
    finally:
        os.close(parent)


def _read_fd(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise CredentialError("file exceeds the size limit")
    return data


def _read_named(dirfd: int, name: str, limit: int, *, secret: bool) -> bytes | None:
    if name in (".", "..") or "/" in name:
        raise CredentialError("refusing file name")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dirfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (getattr(os, "ELOOP", 40), getattr(os, "ENOTDIR", 20)):
            raise CredentialError("refusing a non-regular file",) from None
        raise CredentialError("could not open the file") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise CredentialError("refusing a non-regular file")
        mode = stat.S_IMODE(info.st_mode)
        if secret and mode & 0o077:
            raise CredentialError("credentials file is group or world readable; chmod 0600 the credentials file")
        if secret and mode != 0o600:
            raise CredentialError("credentials file must be mode 0600; chmod 0600 the credentials file")
        if info.st_size > limit:
            raise CredentialError("file exceeds the size limit")
        os.set_blocking(fd, True)
        return _read_fd(fd, limit)
    finally:
        os.close(fd)


def _token_from_document(data: bytes, origin: str) -> str | None:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise CredentialError("credentials file is not valid JSON") from None
    hosts = document.get("hosts") if isinstance(document, dict) else None
    if not isinstance(hosts, dict):
        raise CredentialError("credentials file is missing hosts")
    entry = hosts.get(origin)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise CredentialError("credentials entry is invalid")
    token = entry.get("token")
    if token is None:
        return None
    if not isinstance(token, str):
        raise CredentialError("credentials token is invalid")
    _validate_token(token)
    return token


def read_token_from_dirfd(dirfd: int, origin: str) -> str | None:
    data = _read_named(dirfd, "credentials.json", CRED_MAX, secret=True)
    if data is None:
        return None
    return _token_from_document(data, origin)


def read_token_file(directory: str, origin: str) -> str | None:
    dirfd = open_directory(directory)
    try:
        return read_token_from_dirfd(dirfd, origin)
    finally:
        os.close(dirfd)


def read_token_from_config(origin: str, environ: Mapping[str, str] | None = None) -> str | None:
    try:
        dirfd = open_genos_config_dir(environ, create=False)
    except FileNotFoundError:
        return None
    try:
        return read_token_from_dirfd(dirfd, origin)
    finally:
        os.close(dirfd)


def read_current_host(environ: Mapping[str, str] | None = None) -> str | None:
    try:
        dirfd = open_genos_config_dir(environ, create=False)
    except FileNotFoundError:
        return None
    try:
        data = _read_named(dirfd, "config.toml", CONFIG_MAX, secret=False)
    finally:
        os.close(dirfd)
    if data is None:
        return None
    try:
        return current_host_from_toml(data.decode("utf-8"))
    except UnicodeError:
        raise CredentialError("config file is not valid text") from None


def _load_hosts(dirfd: int) -> dict[str, dict[str, str]]:
    try:
        data = _read_named(dirfd, "credentials.json", CRED_MAX, secret=True)
    except CredentialError as exc:
        if "non-regular" in str(exc):
            return {}
        raise
    if data is None:
        return {}
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise CredentialError("credentials file is not valid JSON") from None
    hosts = document.get("hosts") if isinstance(document, dict) else None
    if not isinstance(hosts, dict):
        raise CredentialError("credentials file is missing hosts")
    clean: dict[str, dict[str, str]] = {}
    for key, entry in hosts.items():
        if not isinstance(key, str) or len(key) > 300 or not isinstance(entry, dict):
            continue
        token = entry.get("token")
        if isinstance(token, str):
            try:
                _validate_token(token)
            except CredentialError:
                continue
            clean[key] = {"token": token}
    return clean


def write_token_in_dirfd(dirfd: int, origin: str, token: str) -> None:
    _validate_token(token)
    hosts = _load_hosts(dirfd)
    hosts[origin] = {"token": token}
    payload = (json.dumps({"hosts": hosts}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > CRED_MAX:
        raise CredentialError("credentials file would exceed the size limit")
    temporary = f".credentials.json.{secrets.token_hex(8)}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dirfd,
    )
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise CredentialError("could not write the credentials file")
            view = view[written:]
        os.fsync(fd)
        os.rename(temporary, "credentials.json", src_dir_fd=dirfd, dst_dir_fd=dirfd)
        os.fsync(dirfd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=dirfd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def write_token_file(directory: str, origin: str, token: str) -> None:
    dirfd = open_directory(directory)
    try:
        write_token_in_dirfd(dirfd, origin, token)
    finally:
        os.close(dirfd)


def write_token_to_config(origin: str, token: str, environ: Mapping[str, str] | None = None) -> None:
    dirfd = open_genos_config_dir(environ, create=True)
    try:
        write_token_in_dirfd(dirfd, origin, token)
    finally:
        os.close(dirfd)


def _child_env() -> dict[str, str]:
    env = {"PATH": "/usr/bin", "LANG": "C.UTF-8"}
    for key in ("HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(key, "")
        if value:
            env[key] = value
    return env


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, 15)
    except OSError:
        return
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
        except OSError:
            return
        if waited == pid:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, 9)
    except OSError:
        return


def run_command(argv: list[str], input_bytes: bytes | None = None, timeout: int = 10) -> CommandResult:
    if not argv or not all(isinstance(item, str) for item in argv):
        raise CredentialError("refusing command")
    if os.path.basename(argv[0]) == "genos":
        raise CredentialError("refusing command")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process.pid)
        raise CredentialError("secret service timed out") from None
    if len(stdout) > 8192 or len(stderr) > 4096:
        _kill_group(process.pid)
        raise CredentialError("secret service output was too large")
    return CommandResult(process.returncode, stdout, stderr)


def _token_from_lookup(raw: bytes) -> str | None:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    if not raw or len(raw) > TOKEN_MAX or b"\n" in raw or b"\x00" in raw:
        return None
    try:
        token = raw.decode("utf-8")
    except UnicodeError:
        return None
    try:
        _validate_token(token)
    except CredentialError:
        return None
    return token


def keyring_lookup(origin: str, *, run: Callable[..., CommandResult] | None = None) -> str | None:
    runner = run_command if run is None else run
    try:
        result = runner(
            [SECRET_TOOL, "lookup", "service", SERVICE_NAME, "host", origin],
            input_bytes=None,
            timeout=10,
        )
    except (CredentialError, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return _token_from_lookup(result.stdout)


def store_token(
    origin: str,
    token: str,
    *,
    run: Callable[..., CommandResult] | None = None,
    write_file: Callable[[str, str], None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    _validate_token(token)
    runner = run_command if run is None else run
    try:
        result = runner(
            [SECRET_TOOL, "store", "--label=Genos", "service", SERVICE_NAME, "host", origin],
            input_bytes=token.encode("utf-8"),
            timeout=15,
        )
    except (CredentialError, FileNotFoundError, OSError):
        result = CommandResult(1, b"", b"")
    if result.returncode == 0:
        return "keyring"
    if write_file is None:
        write_file = lambda item, value: write_token_to_config(item, value, environ)
    write_file(origin, token)
    return "file"


def _safe_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ProtocolError("refusing path")
    if any(char in path for char in "\r\n\x00 \\") or any(part in (".", "..") for part in path.split("/")):
        raise ProtocolError("refusing path")
    return path


def _redact(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "[redacted]")
    return text


def _response_message(status: int, data: bytes, token: str) -> str:
    message = f"request failed ({status})"
    code = ""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        document = None
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code")
            raw_message = error.get("message")
            if isinstance(raw_code, str):
                code = raw_code.strip()[:80]
            if isinstance(raw_message, str) and raw_message.strip():
                message = raw_message[:180]
        elif isinstance(document.get("message"), str):
            message = document["message"][:180]
    if code and code not in message:
        message = f"{code}: {message}"
    return tooltip_text(_redact(message, token), 180) or "request failed"


def http_request(
    origin: str,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
    timeout: int = HTTP_TIMEOUT,
) -> tuple[int, bytes]:
    checked = canonical_origin(origin)
    target = _safe_path(path)
    parts = urlsplit(checked)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        if parts.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                parts.hostname,
                port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
        connection.request(method, target, body=body, headers=dict(headers))
        response = connection.getresponse()
        data = response.read(BODY_MAX + 1)
        status = response.status
    except (TimeoutError, socket.timeout, http.client.HTTPException, OSError):
        raise ProtocolError("request failed") from None
    finally:
        try:
            connection.close()
        except Exception:
            pass
    if len(data) > BODY_MAX:
        raise ProtocolError("response exceeds the size limit")
    if 300 <= status < 400:
        raise ProtocolError("redirect refused")
    return status, data


class HttpTransport:
    def request(
        self,
        origin: str,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> tuple[int, bytes]:
        return http_request(origin, method, path, headers, body)


def _auth_headers(token: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    _validate_token(token)
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "genos-omarchy/2026.9.22",
    }
    if extra:
        headers.update(extra)
    return headers


def _json_object(data: bytes) -> dict[str, object]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ProtocolError("response was not valid JSON") from None
    if not isinstance(document, dict):
        raise ProtocolError("response was not an object")
    return document


def fetch_menu(origin: str, token: str, transport: HttpTransport) -> list[dict[str, object]]:
    status, data = transport.request(origin, "GET", "/api/v1/servers", _auth_headers(token), None)
    if status in (401, 403):
        raise CredentialError("That token was not accepted.")
    if status != 200:
        raise ProtocolError(_response_message(status, data, token))
    try:
        return parse_menu(_json_object(data))
    except ParseError as exc:
        raise ParseError(_redact(str(exc), token)) from None


def post_action(origin: str, token: str, server_id: str, action: str, transport: HttpTransport) -> int:
    if action not in SERVER_ACTIONS:
        raise ProtocolError("unsupported action")
    validate_server_id(server_id)
    body = json.dumps({"type": action, "confirmUnsavedProgressLoss": False}, separators=(",", ":")).encode("utf-8")
    headers = _auth_headers(token, {"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())})
    path = "/api/v1/servers/" + quote(server_id, safe="") + "/actions"
    status, data = transport.request(origin, "POST", path, headers, body)
    if status in (401, 403):
        raise CredentialError("That token was not accepted.")
    if status < 200 or status >= 300:
        raise ProtocolError(_response_message(status, data, token))
    return status


def validate_server_id(server_id: str) -> None:
    if not isinstance(server_id, str) or _ID_RE.fullmatch(server_id) is None:
        raise ProtocolError("server id is invalid")


def do_list(
    *,
    origin: str | None = None,
    credential: Resolved | None = None,
    transport: HttpTransport | None = None,
) -> dict[str, object]:
    if origin is None:
        origin = resolve_origin()
    if credential is None:
        credential = resolve_credential(origin)
    rows = fetch_menu(origin, credential.token, transport or HttpTransport())
    return _scrub(
        {"ok": True, "origin": origin, "source": credential.source, "servers": rows},
        credential.token,
    )


def do_action(
    server_id: str,
    action: str,
    confirmed: bool = False,
    *,
    origin: str | None = None,
    credential: Resolved | None = None,
    transport: HttpTransport | None = None,
) -> dict[str, object]:
    validate_server_id(server_id)
    if action not in SERVER_ACTIONS:
        raise ProtocolError("unsupported action")
    if origin is None:
        origin = resolve_origin()
    if credential is None:
        credential = resolve_credential(origin)
    client = transport or HttpTransport()
    row = next((item for item in fetch_menu(origin, credential.token, client) if item["id"] == server_id), None)
    if row is None:
        raise ProtocolError("server is not in the menu")
    if needs_confirmation(action, row) and not confirmed:
        raise ConfirmRequired(action, server_id, confirm_message(action, row))
    status = post_action(origin, credential.token, server_id, action, client)
    return _scrub(
        {"ok": True, "action": action, "serverId": server_id, "status": status},
        credential.token,
    )


def validate_setup_id(setup_id: str) -> None:
    if not isinstance(setup_id, str) or _ID_RE.fullmatch(setup_id) is None:
        raise ProtocolError("setup id is invalid")


def parse_setups(document: object) -> tuple[list[dict[str, object]], str]:
    if not isinstance(document, dict) or not isinstance(document.get("setups"), list):
        raise ParseError("setup list is invalid")
    setups = document["setups"]
    if len(setups) > SETUP_MAX:
        raise ParseError("setup list is too long")
    selected = document.get("selectedSetupID", "")
    if selected is None:
        selected = ""
    if not isinstance(selected, str):
        raise ParseError("selectedSetupID is invalid")
    if selected != "" and _ID_RE.fullmatch(selected) is None:
        raise ParseError("selectedSetupID is invalid")
    rows: list[dict[str, object]] = []
    for entry in setups:
        if not isinstance(entry, dict):
            raise ParseError("setup list is invalid")
        setup_id = entry.get("id")
        if not isinstance(setup_id, str) or _ID_RE.fullmatch(setup_id) is None:
            raise ParseError("setup id is invalid")
        if not isinstance(entry.get("name"), str):
            raise ParseError("setup name is invalid")
        game = entry.get("game", {})
        if game is None:
            game = {}
        if not isinstance(game, dict) or not isinstance(game.get("name", ""), str):
            raise ParseError("game name is invalid")
        name = display_text(entry.get("name"), NAME_MAX) or "profile"
        row = {
            "id": setup_id,
            "name": name,
            "gameName": display_text(game.get("name", ""), NAME_MAX),
            "selected": setup_id == selected,
        }
        rows.append({key: row[key] for key in SETUP_FIELDS})
    return rows, selected


def fetch_setups(origin: str, token: str, server_id: str, transport: HttpTransport) -> tuple[list[dict[str, object]], str]:
    validate_server_id(server_id)
    path = "/api/v1/servers/" + quote(server_id, safe="") + "/setups"
    status, data = transport.request(origin, "GET", path, _auth_headers(token), None)
    if status in (401, 403):
        raise CredentialError("That token was not accepted.")
    if status != 200:
        raise ProtocolError(_response_message(status, data, token))
    try:
        return parse_setups(_json_object(data))
    except ParseError as exc:
        raise ParseError(_redact(str(exc), token)) from None


def put_selected_setup(
    origin: str,
    token: str,
    server_id: str,
    setup_id: str,
    expected_selected_setup_id: str | None,
    transport: HttpTransport,
) -> tuple[int, dict[str, object]]:
    validate_server_id(server_id)
    validate_setup_id(setup_id)
    body_obj: dict[str, object] = {"setupID": setup_id, "expectedSelectedSetupID": expected_selected_setup_id or ""}
    body = json.dumps(body_obj, separators=(",", ":")).encode("utf-8")
    headers = _auth_headers(token, {"Content-Type": "application/json"})
    path = "/api/v1/servers/" + quote(server_id, safe="") + "/selected-setup"
    status, data = transport.request(origin, "PUT", path, headers, body)
    if status in (401, 403):
        raise CredentialError("That token was not accepted.")
    if status < 200 or status >= 300:
        raise ProtocolError(_response_message(status, data, token))
    return status, _json_object(data) if data else {}


def delete_selected_setup(
    origin: str,
    token: str,
    server_id: str,
    expected_selected_setup_id: str | None,
    transport: HttpTransport,
) -> tuple[int, dict[str, object]]:
    validate_server_id(server_id)
    body_obj: dict[str, object] = {"expectedSelectedSetupID": expected_selected_setup_id or ""}
    body = json.dumps(body_obj, separators=(",", ":")).encode("utf-8")
    headers = _auth_headers(token, {"Content-Type": "application/json"})
    path = "/api/v1/servers/" + quote(server_id, safe="") + "/selected-setup"
    status, data = transport.request(origin, "DELETE", path, headers, body)
    if status in (401, 403):
        raise CredentialError("That token was not accepted.")
    if status < 200 or status >= 300:
        raise ProtocolError(_response_message(status, data, token))
    return status, _json_object(data) if data else {}


def _menu_row(origin: str, token: str, server_id: str, transport: HttpTransport) -> dict[str, object]:
    row = next((item for item in fetch_menu(origin, token, transport) if item["id"] == server_id), None)
    if row is None:
        raise ProtocolError("server is not in the menu")
    return row


def _refuse_unless_stopped(row: Mapping[str, object]) -> None:
    if row.get("status") != STOPPED_STATUS:
        raise ProtocolError("server must be Stopped before changing profile")


def confirm_profile_message(action: str, server: Mapping[str, object], setup_name: str = "") -> str:
    name = tooltip_text(server.get("name") or "") or "this server"
    if action == "select-setup":
        profile = tooltip_text(setup_name or "this profile") or "this profile"
        return f"Select {profile} on {name}?"
    if action == "unload-setup":
        return f"Unload profile on {name}?"
    return ""


def do_setups(
    server_id: str,
    *,
    origin: str | None = None,
    credential: Resolved | None = None,
    transport: HttpTransport | None = None,
) -> dict[str, object]:
    validate_server_id(server_id)
    if origin is None:
        origin = resolve_origin()
    if credential is None:
        credential = resolve_credential(origin)
    client = transport or HttpTransport()
    rows, selected = fetch_setups(origin, credential.token, server_id, client)
    return _scrub(
        {
            "ok": True,
            "serverId": server_id,
            "selectedSetupID": selected,
            "setups": rows,
        },
        credential.token,
    )


def do_select_setup(
    server_id: str,
    setup_id: str,
    confirmed: bool = False,
    *,
    expected: str | None = None,
    origin: str | None = None,
    credential: Resolved | None = None,
    transport: HttpTransport | None = None,
    menu_row: Mapping[str, object] | None = None,
) -> dict[str, object]:
    validate_server_id(server_id)
    validate_setup_id(setup_id)
    if expected is not None and expected != "":
        validate_setup_id(expected)
    if origin is None:
        origin = resolve_origin()
    if credential is None:
        credential = resolve_credential(origin)
    client = transport or HttpTransport()
    row = dict(menu_row) if menu_row is not None else _menu_row(origin, credential.token, server_id, client)
    _refuse_unless_stopped(row)
    if not confirmed:
        setup_name = str((menu_row or {}).get("setupName") or "")
        if not setup_name:
            try:
                setups, _selected = fetch_setups(origin, credential.token, server_id, client)
                match = next((item for item in setups if item["id"] == setup_id), None)
                if match is not None:
                    setup_name = str(match.get("name") or "")
            except PanelError:
                setup_name = setup_id
        raise ConfirmRequired(
            "select-setup",
            server_id,
            confirm_profile_message("select-setup", row, setup_name or setup_id),
        )
    expected_value = expected if expected is not None else str(row.get("selectedSetupID") or "")
    status, data = put_selected_setup(origin, credential.token, server_id, setup_id, expected_value, client)
    selected_name = ""
    server = data.get("server") if isinstance(data, dict) else None
    if isinstance(server, dict) and isinstance(server.get("selectedSetupName"), str):
        selected_name = display_text(server.get("selectedSetupName"), NAME_MAX)
    return _scrub(
        {
            "ok": True,
            "action": "select-setup",
            "serverId": server_id,
            "setupId": setup_id,
            "selectedSetupName": selected_name,
            "status": status,
        },
        credential.token,
    )


def do_unload_setup(
    server_id: str,
    confirmed: bool = False,
    *,
    expected: str | None = None,
    origin: str | None = None,
    credential: Resolved | None = None,
    transport: HttpTransport | None = None,
    menu_row: Mapping[str, object] | None = None,
) -> dict[str, object]:
    validate_server_id(server_id)
    if expected is not None and expected != "":
        validate_setup_id(expected)
    if origin is None:
        origin = resolve_origin()
    if credential is None:
        credential = resolve_credential(origin)
    client = transport or HttpTransport()
    row = dict(menu_row) if menu_row is not None else _menu_row(origin, credential.token, server_id, client)
    _refuse_unless_stopped(row)
    if not confirmed:
        raise ConfirmRequired("unload-setup", server_id, confirm_profile_message("unload-setup", row))
    expected_value = expected if expected is not None else str(row.get("selectedSetupID") or "")
    status, _data = delete_selected_setup(origin, credential.token, server_id, expected_value, client)
    return _scrub(
        {
            "ok": True,
            "action": "unload-setup",
            "serverId": server_id,
            "status": status,
        },
        credential.token,
    )



def _pick(document: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in document and document[key] is not None:
            return document[key]
    return None


def _user_code(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9-]{4,32}", value):
        return value
    return ""


def _endpoint(value: str) -> tuple[str, str, int] | None:
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host == "" or scheme not in ("https", "http"):
        return None
    if parts.port is not None:
        port = parts.port
    elif scheme == "https":
        port = 443
    else:
        port = 80
    return scheme, host, port


def safe_verification_uri(origin: str, value: object) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    if any(char in value for char in "<>&"):
        return ""
    try:
        uri = urlsplit(value)
        base = _endpoint(canonical_origin(origin))
        page = _endpoint(value)
    except (ValueError, OriginError):
        return ""
    if uri.username or uri.password or uri.fragment or (uri.query and any(char in uri.query for char in "\r\n")):
        return ""
    if base is None or page is None or page != base:
        return ""
    scheme, host, _port = page
    if scheme == "https" or host in LOOPBACK_HOSTS:
        return value
    return ""


def _interpret_poll(status: int, data: bytes) -> tuple[str, str]:
    document = _json_object(data) if data else {}
    error = document.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code in ("authorization_pending", "slow_down"):
            return "pending", str(code)
        raise ProtocolError("device login was not approved")
    if error is not None:
        raise ProtocolError("device login was not approved")
    if status != 200:
        raise ProtocolError("device login was not approved")
    token = document.get("token")
    if not isinstance(token, str):
        raise ProtocolError("token response did not include a token")
    _validate_token(token)
    return "ok", token


def device_code_request(origin: str, machine_name: str) -> dict[str, str]:
    machine = tooltip_text(machine_name, NAME_MAX).strip() or "unknown"
    return {"clientName": "genos-omarchy", "machineName": machine, "host": canonical_origin(origin)}


def approval_url(origin: str, verification_path: object) -> str:
    if not isinstance(verification_path, str) or not verification_path.startswith("/account?"):
        return ""
    if "code=" not in verification_path or "://" in verification_path:
        return ""
    if any(char in verification_path for char in "\r\n\x00<>& ") or "genos_pat_" in verification_path or "genos_discord_" in verification_path:
        return ""
    return canonical_origin(origin) + verification_path


def device_login(
    origin: str,
    *,
    machine_name: str | None = None,
    transport: HttpTransport | None = None,
    store: Callable[[str, str], str] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    emit: Callable[[dict[str, object]], None] | None = None,
    on_approved: Callable[[str], None] | None = None,
    max_wait: int = 900,
    max_polls: int = 200,
) -> list[dict[str, object]]:
    checked = canonical_origin(origin)
    client = transport or HttpTransport()
    sleeper = time.sleep if sleep is None else sleep
    now = time.monotonic if clock is None else clock
    saver = store or (lambda item, value: store_token(item, value))
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "genos-omarchy/2026.9.25"}
    machine = socket.gethostname() if machine_name is None else machine_name
    start = device_code_request(checked, machine)
    status, data = client.request(
        checked,
        "POST",
        "/api/v1/auth/device/codes",
        headers,
        json.dumps(start, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )
    if status == 404:
        raise ProtocolError(
            "Device login requires an upcoming Genos API. Paste a personal access token and use Connect instead."
        )
    if status != 201:
        raise ProtocolError("device login could not start")
    document = _json_object(data)
    device_code = _pick(document, "deviceCode", "device_code")
    if not isinstance(device_code, str) or len(device_code) > 512 or any(char in device_code for char in "\r\n\x00"):
        raise ProtocolError("device code was invalid")
    verification_path = _pick(document, "verificationPath", "verification_path")
    opened = approval_url(checked, verification_path)
    if opened == "":
        raise ProtocolError(
            "approval path was invalid"
            f" (verificationPath={verification_path!r} keys={sorted(document.keys())})"
        )
    user_code = _user_code(_pick(document, "userCode", "user_code"))
    verification_uri = safe_verification_uri(checked, opened)
    if user_code == "" or verification_uri == "":
        raise ProtocolError(
            "device login code event incomplete"
            f" (userCode={_pick(document, 'userCode', 'user_code')!r}"
            f" verificationPath={verification_path!r}"
            f" verificationUri={verification_uri!r}"
            f" keys={sorted(document.keys())})"
        )
    interval_value = _pick(document, "interval")
    expires_value = _pick(document, "expiresIn", "expires_in")
    try:
        interval = max(1, min(int(interval_value), 30))
        expires = max(1, min(int(expires_value), max_wait))
    except (TypeError, ValueError):
        raise ProtocolError("device login timing was invalid") from None
    events: list[dict[str, object]] = []

    def publish(event: dict[str, object]) -> None:
        events.append(event)
        if emit is not None:
            emit(event)

    publish(
        {
            "event": "code",
            "origin": checked,
            "userCode": user_code,
            "verificationPath": verification_path,
            "verificationUri": verification_uri,
        }
    )
    deadline = now() + expires
    polls = 0
    while now() < deadline and polls < max_polls:
        if interval:
            sleeper(interval)
        polls += 1
        poll_body = json.dumps({"deviceCode": device_code}, separators=(",", ":")).encode("utf-8")
        poll_status, poll_data = client.request(checked, "POST", "/api/v1/auth/device/tokens", headers, poll_body)
        kind, value = _interpret_poll(poll_status, poll_data)
        if kind == "pending":
            if value == "slow_down":
                interval = min(interval + 5, 30)
            continue
        where = saver(checked, value)
        if on_approved is not None:
            on_approved(value)
        publish({"event": "stored", "where": where})
        return events
    raise ProtocolError("device login expired")


def read_stdin_token(timeout: int = 30) -> str:
    fd = sys.stdin.fileno()
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and total <= TOKEN_MAX:
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            break
        chunk = os.read(fd, TOKEN_MAX + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if len(raw) > TOKEN_MAX:
        raise CredentialError("token is too long")
    line = raw.split(b"\n", 1)[0]
    if line.endswith(b"\r"):
        line = line[:-1]
    try:
        token = line.decode("utf-8")
    except UnicodeError:
        raise CredentialError("token is invalid") from None
    _validate_token(token)
    return token


def do_connect(
    *,
    origin: str | None = None,
    read_token: Callable[[], str] | None = None,
    store: Callable[[str, str], str] | None = None,
) -> dict[str, object]:
    token = read_stdin_token() if read_token is None else read_token()
    try:
        checked = resolve_origin() if origin is None else canonical_origin(origin)
        where = store_token(checked, token) if store is None else store(checked, token)
    finally:
        token = ""
    message = (
        "Saved the token in the credentials file because the keyring was unavailable."
        if where == "file"
        else "Saved the token in the keyring."
    )
    return {"ok": True, "stored": where, "message": message}


def _scrub(payload: dict[str, object], token: str) -> dict[str, object]:
    encoded = json.dumps(payload, ensure_ascii=True)
    if token and token in encoded:
        encoded = encoded.replace(token, "[redacted]")
        loaded = json.loads(encoded)
        if isinstance(loaded, dict):
            return loaded
    return payload


def parse_settings_payload(raw: bytes) -> tuple[str | None, str | None]:
    if len(raw) > CRED_MAX:
        raise CredentialError("settings were too large")
    if b"\n" in raw:
        raw = raw.split(b"\n", 1)[0]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    if not raw:
        return None, None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialError("settings were not valid") from exc
    if not isinstance(document, dict):
        raise CredentialError("settings were not valid")
    origin = document.get("origin")
    token = document.get("token")
    if origin is not None and not isinstance(origin, str):
        raise CredentialError("settings were not valid")
    if token is not None and not isinstance(token, str):
        raise CredentialError("settings were not valid")
    return origin, token


def read_settings_payload(timeout: int = 2) -> tuple[str | None, str | None]:
    fd = sys.stdin.fileno()
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and total <= CRED_MAX:
        ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        chunk = os.read(fd, CRED_MAX + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if b"\n" in chunk:
            break
    return parse_settings_payload(b"".join(chunks))


def panel_origin(explicit: str | None, environ: Mapping[str, str] | None = None) -> str:
    if isinstance(explicit, str) and explicit.strip():
        return canonical_origin(explicit)
    env = os.environ if environ is None else environ
    raw = env.get("GENOS_HOST", "")
    if isinstance(raw, str) and raw.strip():
        return canonical_origin(raw)
    return DEFAULT_ORIGIN


def _parse_confirm_flag(arg: str) -> bool | None:
    if arg in ("--confirm", "--confirmed"):
        return True
    return None


def parse_action_args(argv: list[str]) -> tuple[str, str, bool]:
    confirmed = False
    parts: list[str] = []
    for arg in argv:
        flag = _parse_confirm_flag(arg)
        if flag is True:
            confirmed = True
        elif arg.startswith("-"):
            raise ProtocolError("unknown option")
        else:
            parts.append(arg)
    if len(parts) != 2:
        raise ProtocolError("usage: action <id> <start|stop|restart>")
    return parts[0], parts[1], confirmed


def parse_setups_args(argv: list[str]) -> str:
    parts: list[str] = []
    for arg in argv:
        if arg.startswith("-"):
            raise ProtocolError("unknown option")
        parts.append(arg)
    if len(parts) != 1:
        raise ProtocolError("usage: setups <serverID>")
    return parts[0]


def parse_select_setup_args(argv: list[str]) -> tuple[str, str, bool, str | None]:
    confirmed = False
    expected: str | None = None
    parts: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        flag = _parse_confirm_flag(arg)
        if flag is True:
            confirmed = True
            index += 1
            continue
        if arg == "--expected":
            index += 1
            if index >= len(argv):
                raise ProtocolError("usage: select-setup <serverID> <setupID> [--expected <id>] [--confirm]")
            expected = argv[index]
            index += 1
            continue
        if arg.startswith("-"):
            raise ProtocolError("unknown option")
        parts.append(arg)
        index += 1
    if len(parts) != 2:
        raise ProtocolError("usage: select-setup <serverID> <setupID> [--expected <id>] [--confirm]")
    return parts[0], parts[1], confirmed, expected


def parse_unload_setup_args(argv: list[str]) -> tuple[str, bool, str | None]:
    confirmed = False
    expected: str | None = None
    parts: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        flag = _parse_confirm_flag(arg)
        if flag is True:
            confirmed = True
            index += 1
            continue
        if arg == "--expected":
            index += 1
            if index >= len(argv):
                raise ProtocolError("usage: unload-setup <serverID> [--expected <id>] [--confirm]")
            expected = argv[index]
            index += 1
            continue
        if arg.startswith("-"):
            raise ProtocolError("unknown option")
        parts.append(arg)
        index += 1
    if len(parts) != 1:
        raise ProtocolError("usage: unload-setup <serverID> [--expected <id>] [--confirm]")
    return parts[0], confirmed, expected


def emit(payload: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _public_message(exc: BaseException) -> str:
    if isinstance(exc, PanelError):
        text = tooltip_text(str(exc), 180)
        return text or "request failed"
    return "request failed"


def do_login() -> None:
    def reveal(token: str) -> None:
        emit({"event": "session", "token": token})

    device_login(panel_origin(None), on_approved=reveal, emit=lambda event: emit(_scrub(event, "")))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args or args[0] in ("-h", "--help"):
            emit({"ok": False, "error": "usage", "message": "usage: list | action | setups | select-setup | unload-setup | login | connect"})
            return 2
        command = args[0]
        from_stdin = "--from-stdin" in args[1:]
        settings_origin = None
        settings_token = None
        if from_stdin:
            settings_origin, settings_token = read_settings_payload()
        if command == "list":
            origin = panel_origin(settings_origin)
            credential = resolve_credential(origin, settings_token=settings_token) if from_stdin else resolve_credential(origin)
            emit(do_list(origin=origin, credential=credential))
        elif command == "action":
            server_id, action, confirmed = parse_action_args([arg for arg in args[1:] if arg != "--from-stdin"])
            origin = panel_origin(settings_origin)
            credential = resolve_credential(origin, settings_token=settings_token) if from_stdin else resolve_credential(origin)
            try:
                emit(do_action(server_id, action, confirmed, origin=origin, credential=credential))
            except ConfirmRequired as exc:
                emit(
                    {
                        "ok": False,
                        "needsConfirm": True,
                        "action": exc.action,
                        "serverId": exc.server_id,
                        "message": exc.message,
                    }
                )
        elif command == "setups":
            server_id = parse_setups_args([arg for arg in args[1:] if arg != "--from-stdin"])
            origin = panel_origin(settings_origin)
            credential = resolve_credential(origin, settings_token=settings_token) if from_stdin else resolve_credential(origin)
            emit(do_setups(server_id, origin=origin, credential=credential))
        elif command == "select-setup":
            server_id, setup_id, confirmed, expected = parse_select_setup_args(
                [arg for arg in args[1:] if arg != "--from-stdin"]
            )
            origin = panel_origin(settings_origin)
            credential = resolve_credential(origin, settings_token=settings_token) if from_stdin else resolve_credential(origin)
            try:
                emit(
                    do_select_setup(
                        server_id,
                        setup_id,
                        confirmed,
                        expected=expected,
                        origin=origin,
                        credential=credential,
                    )
                )
            except ConfirmRequired as exc:
                emit(
                    {
                        "ok": False,
                        "needsConfirm": True,
                        "action": exc.action,
                        "serverId": exc.server_id,
                        "setupId": setup_id,
                        "message": exc.message,
                    }
                )
        elif command == "unload-setup":
            server_id, confirmed, expected = parse_unload_setup_args(
                [arg for arg in args[1:] if arg != "--from-stdin"]
            )
            origin = panel_origin(settings_origin)
            credential = resolve_credential(origin, settings_token=settings_token) if from_stdin else resolve_credential(origin)
            try:
                emit(
                    do_unload_setup(
                        server_id,
                        confirmed,
                        expected=expected,
                        origin=origin,
                        credential=credential,
                    )
                )
            except ConfirmRequired as exc:
                emit(
                    {
                        "ok": False,
                        "needsConfirm": True,
                        "action": exc.action,
                        "serverId": exc.server_id,
                        "message": exc.message,
                    }
                )
        elif command == "login":
            do_login()
        elif command == "connect":
            emit(do_connect())
        else:
            emit({"ok": False, "error": "usage", "message": "unknown command"})
            return 2
        return 0
    except PanelError as exc:
        emit({"ok": False, "error": exc.code, "message": _public_message(exc)})
        return 1
    except Exception:
        emit({"ok": False, "error": "failed", "message": "request failed"})
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
