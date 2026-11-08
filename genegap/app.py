"""Start the unified local GeneGap app: python app.py."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "data" / "cache" / "matplotlib"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "app_runs")
    args = parser.parse_args(argv)
    try:
        from web.service import AppService
        from web.server import make_server
    except ImportError as exc:
        print(f"A dependency is missing: {exc}. Run pip install -r requirements.txt in your project environment.", file=sys.stderr)
        return 1
    service = AppService(args.data_dir, legacy_roots=[ROOT / "integration" / "runs"])
    try:
        server = make_server(service, args.host, args.port)
    except (OSError, ValueError) as exc:
        service.close()
        print(f"Could not start GeneGap: {exc}. Try --port 8001 if the port is occupied.", file=sys.stderr)
        return 1
    print(f"GeneGap is ready: http://{args.host}:{server.server_address[1]}", flush=True)
    print("Recorded demo works without API keys. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
