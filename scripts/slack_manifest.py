#!/usr/bin/env python3
"""Validate, create, update, or export Slack apps from version-controlled manifests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

API = "https://slack.com/api"
ROOT = Path(__file__).resolve().parents[1]


def manifest_path(app_name: str) -> Path:
    path = ROOT / "slack_apps" / app_name / "manifest.json"
    if not path.exists():
        raise SystemExit(f"Manifest not found: {path}")
    return path


def config_token() -> str:
    token = os.getenv("SLACK_CONFIG_TOKEN", "").strip()
    if not token:
        raise SystemExit("SLACK_CONFIG_TOKEN is not set in this shell")
    return token


def call(method: str, payload: dict) -> dict:
    response = httpx.post(
        f"{API}/{method}",
        headers={"Authorization": f"Bearer {config_token()}"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise SystemExit(f"Slack {method} failed: {data.get('error', data)}")
    return data


def load_manifest(app_name: str) -> dict:
    return json.loads(manifest_path(app_name).read_text(encoding="utf-8"))


def validate(app_name: str) -> None:
    data = call("apps.manifest.validate", {"manifest": load_manifest(app_name)})
    print(f"Manifest valid: {app_name}")
    if data.get("warnings"):
        print(json.dumps(data["warnings"], indent=2))


def create(app_name: str) -> None:
    data = call("apps.manifest.create", {"manifest": load_manifest(app_name)})
    print(f"Created Slack app: {data.get('app_id')}")
    if data.get("credentials", {}).get("client_id"):
        print(f"Client ID: {data['credentials']['client_id']}")
    if data.get("oauth_authorize_url"):
        print("Authorize/install the app at:")
        print(data["oauth_authorize_url"])


def update(app_name: str, app_id: str) -> None:
    call("apps.manifest.update", {"app_id": app_id, "manifest": load_manifest(app_name)})
    print(f"Updated Slack app {app_id} from {manifest_path(app_name)}")


def export(app_id: str) -> None:
    data = call("apps.manifest.export", {"app_id": app_id})
    print(json.dumps(data.get("manifest", {}), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "create"):
        p = sub.add_parser(command)
        p.add_argument("app_name")
    p = sub.add_parser("update")
    p.add_argument("app_name")
    p.add_argument("app_id")
    p = sub.add_parser("export")
    p.add_argument("app_id")
    args = parser.parse_args()

    if args.command == "validate":
        validate(args.app_name)
    elif args.command == "create":
        create(args.app_name)
    elif args.command == "update":
        update(args.app_name, args.app_id)
    elif args.command == "export":
        export(args.app_id)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
