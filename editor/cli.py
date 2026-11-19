"""Operator commands; local media are imported to private storage before queueing."""
import argparse
import json
from pathlib import Path

from .plan import PlanError, object_key, require, validate
from .render import checksum, render
from .storage import Storage
from .worker import connect, enqueue


def main():
    parser = argparse.ArgumentParser(description="SuperAgentK Video Editor engine")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "render-local", "enqueue"):
        command = commands.add_parser(name)
        command.add_argument("plan", type=Path)
        if name != "validate":
            command.add_argument("--quality", choices=("preview", "master"), default="preview")
        if name == "render-local":
            command.add_argument("--inputs", type=Path, required=True, help="JSON map from object keys to local media paths")
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--format", choices=("9:16", "4:5", "1:1", "16:9"), required=True)
        if name == "enqueue":
            command.add_argument("--approved-by")
    upload = commands.add_parser("upload")
    upload.add_argument("file", type=Path)
    upload.add_argument("--key", required=True)
    signed = commands.add_parser("download-link")
    signed.add_argument("job")
    args = parser.parse_args()
    try:
        if args.command == "upload":
            require(object_key(args.key), "Invalid object key.")
            mime = {".mp4": "video/mp4", ".mov": "video/quicktime", ".png": "image/png",
                    ".jpg": "image/jpeg", ".ttf": "font/ttf", ".otf": "font/otf"}.get(args.file.suffix.lower())
            require(mime, "Unsupported upload type.")
            store = Storage()
            store.upload(args.key, args.file, mime)
            store.verify(args.key, checksum(args.file))
            print("Saved and checksum verified.")
        elif args.command == "download-link":
            with connect() as conn:
                row = conn.execute("SELECT output, quality FROM video_editor.render_jobs WHERE id=%s AND state='ready'", (args.job,)).fetchone()
            require(row and row["output"], "No completed render is available.")
            require(row["quality"] == "preview" or not row["output"].get("review_required"),
                    "Master export is blocked while PRD quality checks remain pending.")
            print(Storage().sign(row["output"]["video_key"]))
        else:
            plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
            duration = validate(plan)
            if args.command == "validate":
                print(f"Valid edit plan: {duration:.2f} seconds.")
            elif args.command == "enqueue":
                print(json.dumps(enqueue(plan, args.quality, args.approved_by)))
            else:
                files = json.loads(args.inputs.read_text(encoding="utf-8-sig"))
                require(not args.output.exists(), "Choose a new output directory to preserve earlier renders.")
                result = render(plan, files, args.output, args.format, args.quality)
                print(json.dumps({key: str(value) if isinstance(value, Path) else value for key, value in result.items()}))
    except (PlanError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
