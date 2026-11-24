"""A fake S3 MCP server: the connector, proven without AWS.

The real template launches ``npx -y mcp-server-s3``; that cannot run in CI, and
it cannot prove anything without an AWS account. This stand-in speaks the same
stdio JSON-RPC (one JSON object per line, id echoed) and publishes the same tool
names as the template's ``allow`` list, so ``uh.mcp`` talks to it exactly as it
would talk to the package.

What it fakes, and how honestly:

* **authentication** - the server refuses every tool call unless
  ``AWS_ACCESS_KEY_ID`` (starting with ``AKIA``) and a secret of 8+ characters
  arrive in the environment. They only arrive because ``uh.mcp._parse_server``
  merges ``mcp-secrets.toml`` into the child's env; the test therefore proves
  the whole secret path, not a dict merge.
* **storage** - objects live in a JSON file named by ``FAKE_S3_STORE``, so a
  second process sees what the first one wrote. That is what makes the
  reconnect test mean something.
* **read-only** - ``FAKE_S3_READONLY=1`` makes every write fail the way a real
  denied bucket does, without touching file permissions.
* **presigned links** - ``presigned_url`` returns a loopback URL signed with
  HMAC-SHA256 over ``bucket/key/expiry``; the local HTTP thread answers 200
  before the deadline and 403 after it. Expiry is therefore observed, not
  assumed.

Environment: ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY`` (required),
``FAKE_S3_STORE``, ``S3_BUCKET`` (default bucket), ``FAKE_S3_READONLY``,
``FAKE_S3_PRESIGN_TTL``.

Run by UH as ``python tests/fake_s3_mcp.py`` - a script, not a module, so the
test does not depend on ``tests`` being importable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

TOOLS: tuple[Dict[str, Any], ...] = (
    {
        "name": "list_buckets",
        "description": "List the buckets this account can see.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_objects",
        "description": "List the keys of one bucket.",
        "inputSchema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}, "prefix": {"type": "string"}},
            "required": ["bucket"],
        },
    },
    {
        "name": "get_object",
        "description": "Read one object.",
        "inputSchema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}, "key": {"type": "string"}},
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "put_object",
        "description": "Write one object.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "content": {"type": "string"},
                "contentType": {"type": "string"},
            },
            "required": ["bucket", "key", "content"],
        },
    },
    {
        "name": "delete_object",
        "description": "Delete one object.",
        "inputSchema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}, "key": {"type": "string"}},
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "presigned_url",
        "description": "A time-limited URL for one object.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "expires_in": {"type": "number"},
            },
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "bucket_info",
        "description": "Region and object count of one bucket.",
        "inputSchema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}},
            "required": ["bucket"],
        },
    },
    {
        "name": "delete_bucket",
        "description": "Delete a whole bucket. The S3 template does not allow it.",
        "inputSchema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}},
            "required": ["bucket"],
        },
    },
)


def _store_path() -> Path:
    raw = os.environ.get("FAKE_S3_STORE") or ""
    return Path(raw) if raw else Path(_default_store())


def _default_store() -> str:
    import tempfile

    return str(Path(tempfile.gettempdir()) / "fake-s3-store.json")


def _load() -> Dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"buckets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"buckets": {}}
    if not isinstance(data, dict) or not isinstance(data.get("buckets"), dict):
        return {"buckets": {}}
    return data


def _save(data: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def _bucket(data: Dict[str, Any], name: str, *, create: bool = False) -> Dict[str, Any]:
    buckets: Dict[str, Dict[str, Any]] = data.setdefault("buckets", {})
    if name not in buckets:
        if not create:
            raise KeyError(name)
        buckets[name] = {}
    return buckets[name]


def _signing_key() -> bytes:
    return (os.environ.get("AWS_SECRET_ACCESS_KEY") or "no-secret").encode("utf-8")


def _sign(bucket: str, key: str, expiry: int) -> str:
    message = f"{bucket}/{key}/{expiry}".encode("utf-8")
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()[:32]


class _Objects(BaseHTTPRequestHandler):
    """Serves a presigned object URL: 200 before the deadline, 403 after it."""

    server_version = "fake-s3/1"

    def log_message(self, *args: Any) -> None:  # noqa: A003 - http.server's shape
        return  # the test reads the process' stderr, not access logs

    def _reply(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server's shape
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/", 1)]
        query = parse_qs(parsed.query)
        expiry = int((query.get("exp") or ["0"])[0] or 0)
        signature = (query.get("sig") or [""])[0]
        if len(parts) != 2:
            self._reply(400, b"bad request", "text/plain; charset=utf-8")
            return
        bucket, key = parts
        if signature != _sign(bucket, key, expiry):
            self._reply(403, "signature invalide".encode("utf-8"), "text/plain; charset=utf-8")
            return
        if time.time() > expiry:
            self._reply(403, "lien expire".encode("utf-8"), "text/plain; charset=utf-8")
            return
        try:
            table = _bucket(_load(), bucket)
            item = table[key]
        except KeyError:
            self._reply(404, "objet absent".encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._reply(200, str(item.get("content", "")).encode("utf-8"), "text/plain; charset=utf-8")


class _Presigner:
    """Lazily starts one loopback HTTP server for the whole process."""

    def __init__(self) -> None:
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def url_for(self, bucket: str, key: str, ttl: float) -> str:
        if self._server is None:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Objects)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        expiry = int(time.time() + max(float(ttl), 0.0))
        if ttl <= 0:
            # A zero-second link must be expired the moment it is handed out, not a
            # tick later: truncating `now` to an int leaves a window where
            # `time.time() > expiry` is still false. A test that raced that window
            # failed on a loaded CI runner, which is a bug in the fake, not the test.
            expiry -= 1
        signature = _sign(bucket, key, expiry)
        address = self._server.server_address
        host, port = str(address[0]), int(address[1])
        return f"http://{host}:{port}/{bucket}/{key}?exp={expiry}&sig={signature}"


def _auth_problem() -> Optional[str]:
    access = os.environ.get("AWS_ACCESS_KEY_ID") or ""
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
    if not access or not secret:
        return (
            "InvalidAccessKeyId: missing AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
            "in the server environment"
        )
    if not access.startswith("AKIA"):
        return f"InvalidAccessKeyId: {access[:4]}... is not an access key id"
    if len(secret) < 8:
        return "SignatureDoesNotMatch: the secret access key is too short"
    return None


def _text(value: Any) -> Dict[str, Any]:
    body = json.dumps(value, ensure_ascii=False, indent=1)
    return {
        "content": [{"type": "text", "text": body}],
        "structuredContent": value,
    }


def _error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def call_tool(name: str, arguments: Dict[str, Any], presigner: _Presigner) -> Dict[str, Any]:
    problem = _auth_problem()
    if problem is not None:
        return _error(problem)
    read_only = os.environ.get("FAKE_S3_READONLY") == "1"
    default_bucket = os.environ.get("S3_BUCKET") or "uh-test"
    bucket = str(arguments.get("bucket") or default_bucket)
    data = _load()

    if name == "list_buckets":
        known = sorted(data.get("buckets") or {})
        return _text({"buckets": known or [default_bucket]})

    if name == "list_objects":
        try:
            table = _bucket(data, bucket)
        except KeyError:
            return _error(f"NoSuchBucket: {bucket}")
        prefix = str(arguments.get("prefix") or "")
        keys = sorted(key for key in table if key.startswith(prefix))
        return _text({"bucket": bucket, "keys": keys})

    if name == "get_object":
        key = str(arguments.get("key") or "")
        try:
            item = _bucket(data, bucket)[key]
        except KeyError:
            return _error(f"NoSuchKey: {bucket}/{key}")
        return _text({"bucket": bucket, "key": key, "content": item.get("content", "")})

    if name == "put_object":
        if read_only:
            return _error(f"AccessDenied: bucket {bucket} is read-only for this key")
        key = str(arguments.get("key") or "")
        content = str(arguments.get("content") or "")
        if not key:
            return _error("InvalidRequest: 'key' is required")
        table = _bucket(data, bucket, create=True)
        table[key] = {"content": content, "contentType": str(arguments.get("contentType") or "")}
        _save(data)
        return _text({"bucket": bucket, "key": key, "bytes": len(content.encode("utf-8"))})

    if name == "delete_object":
        if read_only:
            return _error(f"AccessDenied: bucket {bucket} is read-only for this key")
        key = str(arguments.get("key") or "")
        try:
            del _bucket(data, bucket)[key]
        except KeyError:
            return _error(f"NoSuchKey: {bucket}/{key}")
        _save(data)
        return _text({"bucket": bucket, "key": key, "deleted": True})

    if name == "presigned_url":
        key = str(arguments.get("key") or "")
        try:
            _bucket(data, bucket)[key]
        except KeyError:
            return _error(f"NoSuchKey: {bucket}/{key}")
        ttl = arguments.get("expires_in")
        if ttl is None:
            ttl = float(os.environ.get("FAKE_S3_PRESIGN_TTL") or 300)
        url = presigner.url_for(bucket, key, float(ttl))
        return _text({"bucket": bucket, "key": key, "url": url, "expires_in": float(ttl)})

    if name == "bucket_info":
        try:
            table = _bucket(data, bucket)
        except KeyError:
            return _error(f"NoSuchBucket: {bucket}")
        return _text(
            {
                "bucket": bucket,
                "region": os.environ.get("AWS_REGION") or "eu-west-3",
                "objects": len(table),
            }
        )

    if name == "delete_bucket":
        # Offered on purpose: the S3 template's `allow` list does not include it,
        # so a connector that filtered nothing would hand this tool to the model.
        if read_only:
            return _error(f"AccessDenied: bucket {bucket} is read-only for this key")
        try:
            table = _bucket(data, bucket)
        except KeyError:
            return _error(f"NoSuchBucket: {bucket}")
        if table:
            return _error(f"BucketNotEmpty: {bucket} holds {len(table)} object(s)")
        (data.get("buckets") or {}).pop(bucket, None)
        _save(data)
        return _text({"bucket": bucket, "deleted": True})

    return _error(f"UnknownTool: {name}")


def main() -> int:
    presigner = _Presigner()
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        method = str(message.get("method") or "")
        request_id = message.get("id")
        raw_params = message.get("params")
        params: Dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}

        if method == "initialize":
            result: Dict[str, Any] = {
                "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-s3", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": list(TOOLS)}
        elif method == "tools/call":
            tool = str(params.get("name") or "")
            raw_arguments = params.get("arguments")
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            result = call_tool(tool, arguments, presigner)
        elif method.startswith("notifications/"):
            continue  # a notification gets no answer at all
        elif method == "ping":
            result = {}
        else:
            if request_id is None:
                continue
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        if request_id is None:
            continue
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
