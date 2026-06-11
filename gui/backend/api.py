"""
HTTP API + static file server (stdlib http.server, threaded).
"""

import csv
import io
import json
import os
import posixpath
import re
import shutil
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core, jobs, radio, demovox, preview, translate

PROJECT = core.PROJECT

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

KINDS = ("radio", "demo", "vox", "zmovie")


# ── build orchestration ──────────────────────────────────────────────────────

def build_kind(project, job, kind: str):
    if kind == "demo" or kind == "vox":
        demovox.build(project, job, kind)
        demovox.finalize_stage_without_radio(project, job)
        if os.path.isfile(project.out("RADIO.DAT")):
            job.log_line("NOTE: out/RADIO.DAT exists from an earlier build — "
                         "re-run the Radio build so STAGE.DIR also carries its "
                         "radio call offsets.")
    elif kind == "radio":
        template = demovox.assemble_stage(project, job)
        radio.build(project, job, template)
    elif kind == "zmovie":
        demovox.build_zmovie(project, job)


def build_all(project, job):
    ran = []
    for kind in ("demo", "vox"):
        if demovox.is_extracted(project, kind):
            job.log_line(f"=== Building {kind.upper()} ===")
            demovox.build(project, job, kind)
            ran.append(kind)
        else:
            job.log_line(f"--- {kind} not extracted, skipping")
    if radio.is_extracted(project):
        job.log_line("=== Building RADIO (+ STAGE.DIR) ===")
        template = demovox.assemble_stage(project, job)
        radio.build(project, job, template)
        ran.append("radio")
    else:
        job.log_line("--- radio not extracted, skipping")
        if ran:
            demovox.finalize_stage_without_radio(project, job)
    if demovox.is_extracted(project, "zmovie"):
        job.log_line("=== Building ZMOVIE ===")
        demovox.build_zmovie(project, job)
        ran.append("zmovie")
    else:
        job.log_line("--- zmovie not extracted, skipping")
    if not ran:
        raise RuntimeError("nothing to build — extract at least one file first")
    job.log_line(f"=== Build complete: {', '.join(ran)} ===")
    job.log_line(f"Output folder: {project.out('.')[:-1]}")


# ── status ───────────────────────────────────────────────────────────────────

def state_payload():
    status = {}
    if PROJECT.loaded:
        for kind in KINDS:
            if kind == "radio":
                extracted = radio.is_extracted(PROJECT)
                total, changed = radio.counts(PROJECT) if extracted else (0, 0)
            else:
                extracted = demovox.is_extracted(PROJECT, kind)
                total, changed = (demovox.counts(PROJECT, kind)
                                  if extracted else (0, 0))
            out_name = "RADIO.DAT" if kind == "radio" else demovox.OUT_NAME[kind]
            out_path = os.path.join(PROJECT.config["workspace"], "out", out_name)
            built = os.path.isfile(out_path)
            status[kind] = {
                "extracted": extracted,
                "total": total,
                "translated": changed,
                "built": built,
                "builtTime": os.path.getmtime(out_path) if built else None,
            }
    return {
        "project": PROJECT.config if PROJECT.loaded else None,
        "lastRoot": core.last_project_root(),
        "status": status,
        "jobsRunning": jobs.any_running(),
        "scriptsDir": core.SCRIPTS_DIR,
        "missingDeps": core.MISSING_DEPS,
    }


# ── CSV export / import ──────────────────────────────────────────────────────

def export_csv(kind: str) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["kind", "group", "subgroup", "key", "original", "translation"])

    def radio_rows():
        data = radio.get_data(PROJECT)
        if not data.get("extracted"):
            return
        orig, work = data["original"], data["work"]
        for call, voxes in orig.get("calls", {}).items():
            for vox, subs in voxes.items():
                for off, text in subs.items():
                    new = work.get("calls", {}).get(call, {}).get(vox, {}).get(off, text)
                    writer.writerow(["radio", call, vox, off, text, new])
        for key, text in orig.get("freqAdd", {}).items():
            new = work.get("freqAdd", {}).get(key, text)
            writer.writerow(["radio", "freqAdd", "", key, text, new])
        for section in ("prompts", "saves"):
            for key, entries in orig.get(section, {}).items():
                for idx, text in entries.items():
                    new = work.get(section, {}).get(key, {}).get(idx, text)
                    writer.writerow(["radio", section, key, idx, text, new])

    def flat_rows(k):
        data = demovox.get_data(PROJECT, k)
        if not data.get("extracted"):
            return
        orig, work = data["original"], data["work"]
        for entry, subs in orig.items():
            for frame, sub in subs.items():
                wsub = work.get(entry, {}).get(frame, sub)
                writer.writerow([k, entry, "", frame, sub.get("text", ""),
                                 wsub.get("text", "")])

    targets = KINDS if kind == "all" else (kind,)
    for k in targets:
        if k == "radio":
            radio_rows()
        else:
            flat_rows(k)
    return buf.getvalue()


def import_csv(text: str) -> dict:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or [h.strip() for h in header[:4]] != ["kind", "group", "subgroup", "key"]:
        raise RuntimeError("unrecognized CSV — expected the header exported by this tool")

    cached = {}
    applied = skipped = 0
    for row in reader:
        if len(row) < 6:
            skipped += 1
            continue
        kind, group, subgroup, key, _orig, new = row[:6]
        if kind not in KINDS:
            skipped += 1
            continue
        if kind not in cached:
            data = (radio.get_data(PROJECT) if kind == "radio"
                    else demovox.get_data(PROJECT, kind))
            if not data.get("extracted"):
                skipped += 1
                continue
            cached[kind] = data["work"]
        work = cached[kind]
        try:
            if kind == "radio":
                if group == "freqAdd":
                    work.setdefault("freqAdd", {})[key] = new
                elif group in ("prompts", "saves"):
                    work.setdefault(group, {}).setdefault(subgroup, {})[key] = new
                else:
                    work.setdefault("calls", {}).setdefault(group, {}) \
                        .setdefault(subgroup, {})[key] = new
            else:
                entry = work.setdefault(group, {})
                sub = entry.setdefault(key, {"duration": "0", "text": ""})
                sub["text"] = new
            applied += 1
        except (KeyError, AttributeError):
            skipped += 1

    for kind, work in cached.items():
        if kind == "radio":
            radio.save_work(PROJECT, work)
        else:
            demovox.save_work(PROJECT, kind, work)
    return {"applied": applied, "skipped": skipped}


# ── request handler ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    # helpers ------------------------------------------------------------

    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if self.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(raw.decode("utf-8"))
        return raw.decode("utf-8")

    def fail(self, exc, code=400):
        self.send_json({"error": str(exc)}, code)

    # static -------------------------------------------------------------

    def serve_static(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        rel = posixpath.normpath(path.lstrip("/"))
        if rel.startswith(".."):
            return self.fail("forbidden", 403)
        full = os.path.join(core.STATIC_DIR, rel)
        if not os.path.isfile(full):
            return self.fail("not found", 404)
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # routing ------------------------------------------------------------

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            if path == "/api/state":
                return self.send_json(state_payload())

            if path == "/api/jobs":
                return self.send_json([j.to_dict(tail=0) for j in jobs.all_jobs()])

            match = re.fullmatch(r"/api/jobs/(\d+)", path)
            if match:
                job = jobs.get(int(match.group(1)))
                if not job:
                    return self.fail("no such job", 404)
                return self.send_json(job.to_dict())

            if path == "/api/font":
                self._require_project()
                return self.send_json(preview.font_payload(PROJECT))

            if path == "/api/charset":
                return self.send_json(preview.charset_payload())

            match = re.fullmatch(r"/api/data/(radio|demo|vox|zmovie)", path)
            if match:
                self._require_project()
                kind = match.group(1)
                data = (radio.get_data(PROJECT) if kind == "radio"
                        else demovox.get_data(PROJECT, kind))
                return self.send_json(data)

            match = re.fullmatch(r"/api/export/(all|radio|demo|vox|zmovie)", path)
            if match:
                self._require_project()
                return self.send_text(export_csv(match.group(1)),
                                      "text/csv; charset=utf-8")

            if path == "/api/browse":
                target = query.get("path", [""])[0] or os.path.expanduser("~")
                target = os.path.abspath(os.path.expanduser(target))
                if not os.path.isdir(target):
                    target = os.path.dirname(target)
                entries = []
                try:
                    for name in sorted(os.listdir(target)):
                        full = os.path.join(target, name)
                        if os.path.isdir(full) and not name.startswith("."):
                            entries.append(name)
                except OSError as exc:
                    return self.fail(exc)
                return self.send_json({"path": target,
                                       "parent": os.path.dirname(target),
                                       "dirs": entries})

            if path.startswith("/api/"):
                return self.fail("unknown endpoint", 404)
            return self.serve_static(path)
        except Exception as exc:
            return self.fail(exc, 500)

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self.read_body()

            if path == "/api/project/open":
                cfg = PROJECT.open(body.get("path", ""))
                return self.send_json({"project": cfg})

            if path == "/api/project/update":
                self._require_project()
                cfg = PROJECT.update(body or {})
                return self.send_json({"project": cfg})

            match = re.fullmatch(r"/api/extract/(radio|demo|vox|zmovie)", path)
            if match:
                self._require_project()
                kind = match.group(1)
                if kind == "radio":
                    job = jobs.start("Extract RADIO.DAT",
                                     lambda j: radio.extract(PROJECT, j))
                else:
                    job = jobs.start(f"Extract {demovox.OUT_NAME[kind]}",
                                     lambda j, k=kind: demovox.extract(PROJECT, j, k))
                return self.send_json({"job": job.id})

            match = re.fullmatch(r"/api/data/(radio|demo|vox|zmovie)/save", path)
            if match:
                self._require_project()
                kind = match.group(1)
                work = body.get("work")
                if not isinstance(work, dict):
                    return self.fail("missing 'work' object")
                if kind == "radio":
                    radio.save_work(PROJECT, work)
                else:
                    demovox.save_work(PROJECT, kind, work)
                return self.send_json({"saved": True})

            match = re.fullmatch(r"/api/build/(all|radio|demo|vox|zmovie)", path)
            if match:
                self._require_project()
                kind = match.group(1)
                if jobs.any_running():
                    return self.fail("a job is already running", 409)
                if kind == "all":
                    job = jobs.start("Build all", lambda j: build_all(PROJECT, j))
                else:
                    job = jobs.start(f"Build {kind}",
                                     lambda j, k=kind: build_kind(PROJECT, j, k))
                return self.send_json({"job": job.id})

            if path == "/api/check":
                bank = int(body.get("bank", 1))
                if "texts" in body:
                    results = [preview.check_text(t, bank)
                               for t in body["texts"]]
                    return self.send_json({"results": results})
                return self.send_json(preview.check_text(body.get("text", ""), bank))

            if path == "/api/zmovie/capacity":
                self._require_project()
                return self.send_json(demovox.zmovie_capacity(
                    PROJECT, body.get("entry", ""), body.get("subs", {})))

            if path == "/api/translate":
                self._require_project()
                texts = body.get("texts", [])
                return self.send_json(
                    {"translations": translate.translate(PROJECT, texts)})

            if path == "/api/import/csv":
                self._require_project()
                text = body if isinstance(body, str) else body.get("csv", "")
                return self.send_json(import_csv(text))

            return self.fail("unknown endpoint", 404)
        except FileNotFoundError as exc:
            return self.fail(exc, 400)
        except RuntimeError as exc:
            return self.fail(exc, 400)
        except Exception as exc:
            return self.fail(exc, 500)

    def _require_project(self):
        if not PROJECT.loaded:
            raise RuntimeError("no project open")


def serve(port: int, host: str = "127.0.0.1"):
    server = ThreadingHTTPServer((host, port), Handler)
    return server
