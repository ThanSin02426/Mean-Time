from __future__ import annotations

import argparse
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a YouTube upload refresh token from a Desktop OAuth client JSON file")
    parser.add_argument("client_secret_json", type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if not args.client_secret_json.exists():
        raise SystemExit(f"File not found: {args.client_secret_json}")
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret_json), SCOPE)
    credentials = flow.run_local_server(port=args.port, access_type="offline", prompt="consent")
    raw = json.loads(args.client_secret_json.read_text(encoding="utf-8"))
    client = raw.get("installed") or raw.get("web") or {}
    print("\nStore these as GitHub Actions secrets:\n")
    print(f"YOUTUBE_CLIENT_ID={client.get('client_id', '')}")
    print(f"YOUTUBE_CLIENT_SECRET={client.get('client_secret', '')}")
    print(f"YOUTUBE_REFRESH_TOKEN={credentials.refresh_token or ''}")
    if not credentials.refresh_token:
        raise SystemExit("Google did not return a refresh token. Revoke the previous grant and run again with consent.")


if __name__ == "__main__":
    main()
