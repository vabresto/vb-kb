#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from kb.linkedin_daemon_client import LinkedInDaemonClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LinkedIn daemon HTTP client.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8771")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")
    subparsers.add_parser("state")

    mode_parser = subparsers.add_parser("mode")
    mode_parser.add_argument("mode", choices=["autonomous", "human_control"])
    mode_parser.add_argument("--actor", default="cli")
    mode_parser.add_argument("--reason", default="")

    cmd_parser = subparsers.add_parser("cmd")
    cmd_parser.add_argument("cmd")
    cmd_parser.add_argument("--params-json", default="{}")

    subparsers.add_parser("shutdown")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    client = LinkedInDaemonClient(base_url=args.daemon_url)
    try:
        if args.command == "health":
            print(json.dumps(client.health(), indent=2, sort_keys=True))
            return 0
        if args.command == "state":
            print(json.dumps(client.state(), indent=2, sort_keys=True))
            return 0
        if args.command == "mode":
            payload = client.set_mode(mode=args.mode, actor=args.actor, reason=args.reason)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "cmd":
            params = json.loads(args.params_json)
            if not isinstance(params, dict):
                raise SystemExit("--params-json must decode to a JSON object")
            payload = client.command(cmd=args.cmd, params=params)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "shutdown":
            print(json.dumps(client.shutdown(), indent=2, sort_keys=True))
            return 0
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
