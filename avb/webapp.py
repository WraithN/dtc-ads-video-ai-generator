"""Launcher for the FastAPI web app.

The app itself lives in `web/server.py` at the repo root, next to `avb/`
rather than inside it. That is deliberate: `web/` is an application shell
(templates, static assets, HTTP routes) and does not belong in the library
package that `pip install dtc-ads-video-ai-generator` ships.

The consequence is that this launcher has to find `web/` on disk. When the
package is installed, `web/` is not on sys.path, so we locate it relative to
this file's parent directory and fall back gracefully with a clear message
rather than a confusing ImportError.
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path


def _repo_root() -> Path:
    """Directory containing `avb/` -- i.e. the repo root."""
    return Path(__file__).resolve().parent.parent


def _import_app():
    root = _repo_root()
    web_dir = root / "web"
    if not web_dir.is_dir():
        raise RuntimeError(
            f"web app directory not found: {web_dir}\n"
            "This command runs from a source checkout. Installed copies of "
            "dtc-ads-video-ai-generator do not include the web/ application shell."
        )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from web.server import app  # noqa: PLC0415

    return app


def run_server(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> int:
    try:
        import uvicorn
    except ImportError:
        print("web app needs extra deps:  pip install -e '.[web]'", file=sys.stderr)
        return 1

    app = _import_app()
    url = f"http://{host}:{port}"

    print(f"AI Video Builder web app -> {url}")
    print("Ctrl+C to stop")

    if open_browser:
        # Give the server a moment to bind before the browser asks for it,
        # otherwise the first request races the socket and shows an error page.
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_server())
