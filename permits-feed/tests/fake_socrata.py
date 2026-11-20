"""A local stand-in for a Socrata portal, for tests and the demo.

Supports the parts of the API the feed uses: /api/views/<id>.json metadata and
/resource/<id>.json with $select, $where (comparisons joined by AND), $order,
$limit and $offset. Values are returned as strings, the way SODA returns them.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_COMPARISON = re.compile(r"^\(?\s*(\w+)\s*(>=|<=|>|<|=)\s*('([^']*)'|-?[\d.]+)\s*\)?$")


def _matches(row: dict, where: str) -> bool:
    for clause in re.split(r"\s+AND\s+", where.strip(), flags=re.IGNORECASE):
        m = _COMPARISON.match(clause.strip())
        if not m:
            raise ValueError(f"fake server can't parse clause: {clause!r}")
        field, op, raw, quoted = m.groups()
        actual = row.get(field)
        if actual in (None, ""):
            return False
        if quoted is not None:
            left, right = str(actual), quoted
        else:
            left, right = float(actual), float(raw)
        ok = {">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right, "=": left == right}[op]
        if not ok:
            return False
    return True


class FakeSocrata:
    def __init__(self, dataset: str, columns: dict[str, str]):
        self.dataset = dataset
        self.columns = dict(columns)  # fieldName -> dataTypeName
        self.rows: list[dict] = []
        self.requests: list[dict] = []
        self.fail_with: int | None = None  # set to an HTTP status to make /resource fail
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                server.requests.append({"path": parsed.path, **params})
                if parsed.path == f"/api/views/{server.dataset}.json":
                    body = {"columns": [{"fieldName": f, "dataTypeName": t} for f, t in server.columns.items()]}
                    return self._send(200, body)
                if parsed.path == f"/resource/{server.dataset}.json":
                    if server.fail_with:
                        return self._send(server.fail_with, {"message": "simulated failure"})
                    try:
                        return self._send(200, server.query(params))
                    except ValueError as exc:
                        return self._send(400, {"message": str(exc)})
                self._send(404, {"message": "not found"})

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.domain = f"127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def query(self, params: dict) -> list[dict]:
        rows = [r for r in self.rows if _matches(r, params["$where"])] if "$where" in params else list(self.rows)
        for key in reversed([k.strip() for k in params.get("$order", "").split(",") if k.strip()]):
            rows.sort(key=lambda r: str(r.get(key, "")))
        offset = int(params.get("$offset", 0))
        rows = rows[offset : offset + int(params.get("$limit", 1000))]
        if "$select" in params:
            fields = [f.strip() for f in params["$select"].split(",")]
            unknown = [f for f in fields if f not in self.columns]
            if unknown:
                raise ValueError(f"No such column: {unknown[0]}")
            # SODA leaves out null fields entirely.
            rows = [{f: r[f] for f in fields if r.get(f) is not None} for r in rows]
        return rows

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
