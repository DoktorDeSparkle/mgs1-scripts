#!/usr/bin/env python3
"""
MGS1 Undub Studio — local GUI for the mgs1-scripts translation toolkit.

Usage:
    python3 gui/app.py [--port 8470] [--no-browser]

Requires only the Python standard library for the GUI itself; the underlying
extraction/recompile scripts have their own requirements (see ../requirements.txt).
"""

import argparse
import os
import socket
import threading
import webbrowser
import sys

if sys.version_info < (3, 10):
    sys.exit("Undub Studio needs Python 3.10+ (the toolkit uses match/case).")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import api, core


def local_ip():
    """Best-guess LAN address of this machine (no packets are actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))   # picks the default-route interface
            return s.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            return None if ip.startswith("127.") else ip
        except OSError:
            return None


def main():
    parser = argparse.ArgumentParser(description="MGS1 Undub Studio GUI")
    parser.add_argument("--port", type=int, default=8470)
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (use 0.0.0.0 for remote access; "
                             "NO authentication — only do this on a trusted network)")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open a browser tab automatically")
    args = parser.parse_args()

    # Re-open the last project automatically, if there was one.
    last = core.last_project_root()
    if last:
        try:
            core.PROJECT.open(last)
            print(f"Re-opened project: {last}")
        except Exception as exc:
            print(f"(could not re-open last project: {exc})")

    server = api.serve(args.port, args.host)
    url = f"http://127.0.0.1:{args.port}/"
    if args.host == "0.0.0.0":
        print(f"MGS1 Undub Studio listening on ALL interfaces, port {args.port}")
        ip = local_ip()
        print(f"  local:  {url}")
        if ip:
            print(f"  remote: http://{ip}:{args.port}/")
        else:
            print("  remote: http://<this-machine's-IP>:%d/" % args.port)
        print("WARNING: there is no authentication — anyone on the network can "
              "read/write project files and run builds. Use only on a trusted LAN.")
    else:
        print(f"MGS1 Undub Studio running at {url}")

    if core.MISSING_DEPS:
        print("\nMissing toolkit dependencies:")
        for dep in core.MISSING_DEPS:
            print(f"  - {dep['pip']}  (needed for: {dep['neededFor']})")
        print(f"  Fix with:  pip install -r "
              f"{os.path.join(core.SCRIPTS_DIR, 'requirements.txt')}\n")

    print(f"Toolkit: {core.SCRIPTS_DIR}")
    print("Ctrl+C to stop.")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye.")


if __name__ == "__main__":
    main()
