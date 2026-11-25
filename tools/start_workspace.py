"""Start the loopback editor using this workspace's optional portable rendering tools.

The key stays in a mode-0600 file. Starting this process does not submit a model
request. Both computation and personal-example sharing require an explicit choice.
"""

import argparse
import os
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "po_delu"
STATE = Path.home() / ".local/share/po-delu"
CACHE = Path.home() / ".cache/agm-docs-v208"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18477)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--renderer-cache", type=Path, default=CACHE)
    a = parser.parse_args()
    directory = a.state / "state"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret = a.state / "openrouter.secret"
    if not secret.is_file() or secret.stat().st_mode & 0o077:
        raise SystemExit("Expected a private key file (mode 0600): " + str(secret))
    ledger = ROOT / "evaluation/v1/runs/billing/budget.sqlite"
    if not ledger.is_file():
        raise SystemExit(
            "The existing budget ledger is required for this installation."
        )
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", a.port)) == 0:
            raise SystemExit("The chosen port is already in use.")
    env = os.environ.copy()
    env.update(
        PO_DELU_MEMORY_DB=str(directory / "preferences.sqlite3"),
        PO_DELU_BUDGET_DB=str(ledger),
        PO_DELU_KEY_FILE=str(secret),
        PO_DELU_PORT=str(a.port),
        PO_DELU_BIND="127.0.0.1",
    )
    soffice = a.renderer_cache / "lo/opt/libreoffice26.8/program"
    if (soffice / "soffice").is_file():
        libraries = [
            str(a.renderer_cache / "deps/usr/lib/x86_64-linux-gnu"),
            str(a.renderer_cache / "deps/lib/x86_64-linux-gnu"),
        ]
        if env.get("LD_LIBRARY_PATH"):
            libraries.append(env["LD_LIBRARY_PATH"])
        env.update(
            PATH=str(soffice) + os.pathsep + env.get("PATH", ""),
            LD_LIBRARY_PATH=os.pathsep.join(libraries),
            FONTCONFIG_FILE=str(a.renderer_cache / "fonts.conf"),
            EDITOR_PDF_FONT=str(
                a.renderer_cache / "deps/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ),
        )
    python = APP / ".venv/bin/python"
    with (directory / "server.log").open("ab") as log:
        proc = subprocess.Popen(
            [str(python), "serve_loopback.py"],
            cwd=APP,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    (directory / "server.pid").write_text(str(proc.pid) + "\n")
    print(f"http://127.0.0.1:{a.port}/ (pid {proc.pid})")


if __name__ == "__main__":
    main()
