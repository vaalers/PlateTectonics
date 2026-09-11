"""Console entry point: ``pt-gui`` starts the Streamlit interface.

Any extra arguments are passed straight to ``streamlit run`` (for example
``pt-gui --server.port 8502`` or ``pt-gui --server.headless true``).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "Streamlit is not installed in this environment.\n"
            "Install it with:  pip install streamlit    (or: pip install -e '.[gui]')\n"
            "then run pt-gui again.",
            file=sys.stderr,
        )
        return 1

    app = Path(__file__).with_name("app.py")
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--browser.gatherUsageStats", "false",
        "--theme.base", "light",
        *argv,
    ]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
