"""scripts/server.py — Run the ballast spec server.

Usage: python scripts/server.py
Default: http://0.0.0.0:8765
"""
import uvicorn

from ballast.core.server import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
