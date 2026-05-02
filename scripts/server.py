"""scripts/server.py — Run the ballast spec server.

Usage: python scripts/server.py [--host HOST] [--port PORT]

Defaults to 127.0.0.1:8765 (loopback only).  To expose on all interfaces
for remote access set --host 0.0.0.0, but you MUST also:
  - Set the BALLAST_TOKEN env var to a strong secret so the server
    validates X-Ballast-Token on every request.
  - Place the server behind a firewall or reverse proxy.
"""
import argparse
import uvicorn

from ballast.core.server import app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ballast spec server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    args = parser.parse_args()

    if args.host == "0.0.0.0":
        import os
        if not os.environ.get("BALLAST_TOKEN"):
            print(
                "WARNING: Binding to 0.0.0.0 without BALLAST_TOKEN set. "
                "The spec server will be accessible without authentication. "
                "Set BALLAST_TOKEN or restrict access with a firewall."
            )

    uvicorn.run(app, host=args.host, port=args.port)
