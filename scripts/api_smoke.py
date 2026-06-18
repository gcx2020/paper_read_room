from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request


BASE = "http://127.0.0.1:8000"


def request(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=5) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else None


def main() -> int:
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.server:app", "--host", "127.0.0.1", "--port", "8000"])
    try:
        for _ in range(30):
            try:
                request("/api/stats")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("server did not start")

        paper = request(
            "/api/papers",
            "POST",
            {"slug": "2026-smoke-test", "title": "Smoke Test Paper", "authors": "Ada Lovelace", "year": 2026, "tags": ["smoke"]},
        )
        listed = request("/api/papers?search=Smoke")
        assert listed["total"] >= 1
        request(f"/api/papers/{paper['id']}", "PUT", {"status": "read", "rating": 5})
        request("/api/papers/batch/tags", "POST", {"ids": [paper["id"]], "tags": ["verified"]})
        request(f"/api/papers/{paper['id']}", "DELETE")
        print("API smoke test passed")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
