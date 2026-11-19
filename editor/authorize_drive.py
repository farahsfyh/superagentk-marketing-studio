"""One-time OAuth setup run by the Drive account owner on their computer."""
import argparse
import json
from pathlib import Path


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", type=Path, required=True, help="Downloaded Desktop OAuth client JSON")
    parser.add_argument("--output", type=Path, required=True, help="Save credentials outside the Git checkout")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path to preserve the existing credentials.")
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client), scopes=["https://www.googleapis.com/auth/drive"])
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        parser.error("Google did not supply offline authorization. Repeat setup with consent.")
    data = json.loads(credentials.to_json())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(data))
    args.output.chmod(0o600)
    print("Drive authorization saved to the specified file. Keep it outside Git and in server-side Secrets.")


if __name__ == "__main__":
    main()
