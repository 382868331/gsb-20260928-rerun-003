#!/usr/bin/env python3
"""启动声音后期制作工作室服务。"""

import argparse
import os

from studio.server import run


def main() -> None:
    parser = argparse.ArgumentParser(description="声音后期制作工作室")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    default_session = os.path.join(os.path.dirname(__file__), "..", "session")
    default_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    parser.add_argument("--session", default=default_session)
    parser.add_argument("--frontend-dist", default=default_dist)
    args = parser.parse_args()
    run(args.host, args.port, os.path.abspath(args.session),
        os.path.abspath(args.frontend_dist))


if __name__ == "__main__":
    main()
