"""Loopback HTTP interface; all application rules live in AppService."""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import AppService


STATIC = Path(__file__).resolve().parent / "static"


def make_server(service: AppService, host="127.0.0.1", port=8000):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("This local reviewer app must bind to 127.0.0.1 or localhost.")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def _send(self, status, content, mime="application/json; charset=utf-8", filename=None):
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            policy = ("default-src 'none'; style-src 'unsafe-inline'; sandbox" if mime == "image/svg+xml" else
                      "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.send_header("Content-Security-Policy", policy)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(content)

        def _valid_host(self):
            actual_port = self.server.server_address[1]
            return self.headers.get("Host", "") in {f"localhost:{actual_port}", f"127.0.0.1:{actual_port}"}

        def _route(self, write=False):
            if not self._valid_host():
                self._send(403, {"error": "Use the local GeneGap address."})
                return
            parsed = urlsplit(self.path)
            parts = parsed.path.strip("/").split("/")
            params = parse_qs(parsed.query)
            body = {}
            if write:
                supplied = self.headers.get("X-GeneGap-Token", "")
                if not secrets.compare_digest(supplied, service.token):
                    self._send(403, {"error": "Reload the app before submitting changes."})
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self._send(415, {"error": "Send a JSON request."})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    self._send(400, {"error": "The request is empty or too large."})
                    return
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("The request must be a JSON object.")
            if not write and parsed.path in {"/", "/styles.css", "/app.js"}:
                names = {"/": ("index.html", "text/html; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}
                name, mime = names[parsed.path]
                self._send(200, (STATIC / name).read_bytes(), mime)
            elif not write and parts == ["api", "status"]:
                self._send(200, service.status())
            elif parts == ["api", "runs"]:
                self._send(202, service.start(body.get("query"), body.get("mode", "offline"))) if write else self._send(200, service.list_runs())
            elif not write and len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                self._send(200, service.job(parts[2]))
            elif len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                run_id = parts[2]
                if len(parts) == 3 and not write:
                    self._send(200, service.detail(run_id))
                elif len(parts) == 4 and parts[3] == "review" and write:
                    self._send(200, service.review(run_id, body))
                elif len(parts) == 4 and parts[3] == "evidence" and write:
                    self._send(200, service.evidence(run_id, body))
                elif len(parts) == 4 and parts[3] == "chart" and not write:
                    fmt = params.get("format", ["svg"])[0]
                    kind = params.get("kind", ["representation"])[0]
                    content = service.chart(run_id, params.get("variant", [""])[0], params.get("mapping", ["A"])[0], kind, fmt)
                    mime = "image/svg+xml" if fmt == "svg" else "image/png"
                    self._send(200, content, mime, f"genegap-{kind}.{fmt}" if params.get("download") else None)
                elif len(parts) == 4 and parts[3] == "export" and not write:
                    final = params.get("final", ["0"])[0] == "1"
                    self._send(200, service.export(run_id, final=final), "application/zip", f"genegap-{'final' if final else 'draft'}-{run_id[-8:]}.zip")
                else:
                    self._send(404, {"error": "This action is not available."})
            else:
                self._send(404, {"error": "Not found."})

        def _handle(self, write=False):
            try:
                self._route(write)
            except KeyError as exc:
                self._send(404, {"error": str(exc).strip("'")})
            except (ValueError, TypeError) as exc:
                self._send(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self._send(500, {"error": f"The action could not finish ({type(exc).__name__}). Your saved run is unchanged; try again."})

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle(True)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
