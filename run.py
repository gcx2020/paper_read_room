from __future__ import annotations

import os
import webbrowser

import uvicorn

from server.paths import FRONTEND_DIR, ensure_dirs


def main() -> None:
    ensure_dirs()
    host = os.getenv("PAPER_HOST", "0.0.0.0")
    port = int(os.getenv("PAPER_PORT", "8000"))
    if not FRONTEND_DIR.exists():
        raise SystemExit(f"Frontend directory not found: {FRONTEND_DIR}")
    url = f"http://localhost:{port}"
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run("server.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
