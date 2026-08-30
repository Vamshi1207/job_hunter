#!/usr/bin/env python3
"""Convert Word CVs to native .pages files using Pages.app.

The Docker UI cannot run Pages.app. Run this on the Mac (same repo checkout):

    python3 scripts/macos_pages_helper.py          # convert existing, then serve Docker
    python3 scripts/macos_pages_helper.py --once   # convert existing and exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.cv_export import docx_to_pages_via_app, is_native_pages  # noqa: E402

PORT = int(os.environ.get("JOB_SEARCH_PAGES_HELPER_PORT", "8765"))
APPLICATIONS = (ROOT / "applications").resolve()


def _allowed(path: Path) -> bool:
    try:
        path.resolve().relative_to(APPLICATIONS)
        return True
    except ValueError:
        return False


def convert_one(docx: Path, pages: Path) -> None:
    if not _allowed(docx) or not _allowed(pages):
        raise ValueError(f"path is outside {APPLICATIONS}")
    docx_to_pages_via_app(docx, pages)


def convert_existing() -> int:
    if not APPLICATIONS.is_dir():
        return 0
    count = 0
    for docx in sorted(APPLICATIONS.glob("*/*_CV.docx")):
        pages = Path(str(docx.with_suffix("")) + ".pages")
        if is_native_pages(pages):
            continue
        print(f"converting {docx.relative_to(ROOT)}", flush=True)
        convert_one(docx, pages)
        count += 1
    return count


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/health":
            self.send_error(404)
            return
        payload = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/pages":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            docx = Path(body["docx"])
            pages = Path(body["pages"])
            convert_one(docx, pages)
        except Exception as exc:
            payload = json.dumps({"ok": False, "error": str(exc)}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="convert existing CVs and exit")
    args = parser.parse_args()
    n = convert_existing()
    print(f"converted {n} resume(s)", flush=True)
    if args.once:
        return
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Pages helper listening on http://0.0.0.0:{PORT}/pages", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
