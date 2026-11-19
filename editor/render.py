"""FFmpeg rendering from a validated plan; all files here are worker scratch."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from .plan import FORMATS, PlanError, require, validate


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(arguments, cwd):
    try:
        return subprocess.run(arguments, cwd=cwd, check=True, capture_output=True, timeout=900)
    except (subprocess.SubprocessError, OSError) as error:
        raise PlanError("The media worker could not process this file. Check its format and cut times.") from error


def probe(path):
    result = run([os.environ.get("FFPROBE", "ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams",
                  "-show_format", "-of", "json", str(Path(path).resolve())], Path(path).parent)
    data = json.loads(result.stdout)
    video = next((stream for stream in data["streams"] if stream["codec_type"] == "video"), None)
    require(video is not None, "This source has no video stream.")
    require(video.get("color_transfer") not in ("smpte2084", "arib-std-b67"),
            "HDR footage needs SDR normalization before this engine can render it.")
    return {"duration": float(data["format"]["duration"]), "video": video,
            "audio": any(stream["codec_type"] == "audio" for stream in data["streams"])}


def wrap(draw, text, font, width):
    lines, current = [], ""
    for character in text:
        if current and draw.textlength(current + character, font=font) > width:
            lines.append(current.strip())
            current = ""
        current += character
    if current:
        lines.append(current.strip())
    return lines


def text_overlay(path, width, height, rows, font_path, colour, background=None):
    image = Image.new("RGBA", (width, height), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for text, size, top, bottom in rows:
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap(draw, text, font, width - int(width * .16))
        line_height = size * 1.4
        require(len(lines) * line_height <= bottom - top, "Text does not fit its safe area. Shorten it before rendering.")
        y = top
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            x = (width - draw.textlength(line, font=font)) / 2
            draw.rectangle((x - 12, y - 5, x + box[2] - box[0] + 12, y + line_height - 3), fill=(0, 0, 0, 190))
            draw.text((x, y), line, font=font, fill=colour, stroke_width=max(1, size // 25), stroke_fill="black")
            y += line_height
    image.save(path)


def ffmpeg(arguments, cwd):
    return run([os.environ.get("FFMPEG", "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-filter_complex_threads", "1", "-protocol_whitelist", "file,pipe", *arguments], cwd)


def render(plan, files, directory, format_name, quality):
    """Return an independently encoded MP4 and its measured output metadata."""
    duration = validate(plan)
    require(format_name in plan["formats"], "This format was not selected.")
    require(quality in ("preview", "master"), "Choose preview or master quality.")
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    width, height = FORMATS[format_name]
    if quality == "preview":
        scale = 540 / min(width, height)
        width, height = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
    font = files[plan["style"]["font_key"]]
    logo = files[plan["style"]["logo_key"]]
    require(Path(font).suffix.lower() in (".ttf", ".otf"), "Choose a TTF or OTF font.")
    try:
        with Image.open(logo) as image:
            image.verify()
    except (OSError, ValueError) as error:
        raise PlanError("Use a PNG logo for the engine reference test.") from error
    normalise = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
    media = {}
    for clip_id, clip in plan["clips"].items():
        media[clip_id] = (Path(files[clip["original_key"]]).resolve(), probe(files[clip["original_key"]]))
    for segment in plan["segments"]:
        require(segment["out"] <= media[segment["clip"]][1]["duration"] + .02, "A selected cut extends past the source clip.")
    first = plan["segments"][0]
    ffmpeg(["-ss", str(first["in"]), "-i", str(media[first["clip"]][0]),
            "-frames:v", "1", "first-frame.png"], root)
    # Cards and every segment each carry their own audio, including explicit silence.
    parts = []
    for name, copy, seconds in (("title", plan["title"], 2), ("end", plan["cta"], 3)):
        png = root / f"{name}.png"
        text_overlay(png, width, height, [(copy, max(28, width // 18), height * .35, height * .75)],
                     font, plan["style"]["text"], ImageColor.getrgb(plan["style"]["background"]) + (255,))
        if name == "title":
            with Image.open(root / "first-frame.png") as source:
                backdrop = ImageOps.fit(source.convert("RGBA"), (width, height))
                shade = Image.new("RGBA", (width, height), (0, 0, 0, 85))
                backdrop = Image.alpha_composite(backdrop, shade)
                # Build the text separately so real footage is visible from frame zero.
                text_overlay(root / "title-text.png", width, height,
                             [(copy, max(28, width // 18), height * .35, height * .75)],
                             font, plan["style"]["text"])
                with Image.open(root / "title-text.png") as text:
                    Image.alpha_composite(backdrop, text.convert("RGBA")).save(png)
        args = ["-loop", "1", "-i", png.name, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-i", str(logo), "-filter_complex",
                f"[2:v]scale={max(40, width // 8)}:-1[logo];[0:v][logo]overlay=W-w-{width//20}:{height//30},format=yuv420p[v]",
                "-map", "[v]", "-map", "1:a", "-t", str(seconds), "-r", "30", "-c:v", "libx264",
                "-preset", "veryfast", "-threads", "2", "-c:a", "aac", "-ar", "48000", "-ac", "2", f"{name}.mp4"]
        ffmpeg(args, root)
    parts.append(root / "title.mp4")
    for index, segment in enumerate(plan["segments"]):
        path, info = media[segment["clip"]]
        seconds = segment["out"] - segment["in"]
        args = ["-ss", str(segment["in"]), "-i", str(path)]
        if segment.get("audio", "speech") == "speech":
            require(info["audio"], f"Segment {index + 1} has no audio. Choose mute to keep it silent.")
            audio = "0:a:0"
        else:
            args.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
            audio = "1:a:0"
        args.extend(["-i", str(logo)])
        logo_index = 2 if audio == "1:a:0" else 1
        framing = normalise
        if segment.get("frame") == "center_crop":
            framing = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30,format=yuv420p"
        filters = [f"[0:v]trim=duration={seconds},setpts=PTS-STARTPTS,{framing}[base]",
                   f"[{audio}]atrim=duration={seconds},asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,apad=whole_dur={seconds},atrim=duration={seconds}[a]",
                   f"[{logo_index}:v]scale={max(40,width//8)}:-1[logo]",
                   f"[base][logo]overlay=W-w-{width//20}:{height//30}[branded]"]
        base, overlay_index = "branded", logo_index + 1
        rows = []
        if segment.get("name_card"):
            person = plan["people"][segment["name_card"]]
            rows.append((" · ".join((person["name"], person["title"], person["company"])),
                         max(24, width // 30), height * .12, height * .28))
        if rows:
            png = root / f"name-{index}.png"
            text_overlay(png, width, height, rows, font, plan["style"]["text"])
            args.extend(["-i", png.name])
            filters.append(f"[{base}][{overlay_index}:v]overlay[vname]")
            base, overlay_index = "vname", overlay_index + 1
        for caption_index, caption in enumerate(segment.get("captions", [])):
            png = root / f"caption-{index}-{caption_index}.png"
            text_overlay(png, width, height, [(caption["text"], max(28, width // 24), height * .76, height * .94)],
                         font, plan["style"]["text"])
            args.extend(["-i", png.name])
            next_base = f"captioned{caption_index}"
            filters.append(f"[{base}][{overlay_index}:v]overlay=enable='gte(t,{caption['start']})*lt(t,{caption['end']})'[{next_base}]")
            base, overlay_index = next_base, overlay_index + 1
        output = root / f"segment-{index}.mp4"
        args.extend(["-filter_complex", ";".join(filters), "-map", f"[{base}]", "-map", "[a]", "-t", str(seconds),
                     "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-ar", "48000", "-ac", "2", output.name])
        ffmpeg(args, root)
        parts.append(output)
    parts.append(root / "end.mp4")
    (root / "concat.txt").write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
    ffmpeg(["-f", "concat", "-safe", "1", "-i", "concat.txt", "-c", "copy", "assembled.mp4"], root)
    ffmpeg(["-i", "assembled.mp4", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:v", "copy",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", str(duration), "-movflags", "+faststart", "output.mp4"], root)
    output = root / "output.mp4"
    metadata = probe(output)
    require(abs(metadata["duration"] - duration) <= .1, "The output duration did not match the edit plan.")
    require(metadata["duration"] <= plan["target_seconds"] + .04, "The output exceeds the target length.")
    require(metadata["audio"], "The output lost its audio stream.")
    require((metadata["video"]["width"], metadata["video"]["height"]) == (width, height), "The output format is incorrect.")
    ffmpeg(["-i", output.name, "-vf", "select=eq(n\,0)", "-frames:v", "1", "thumbnail.jpg"], root)
    return {"path": output, "thumbnail": root / "thumbnail.jpg", "checksum": checksum(output),
            "duration": metadata["duration"], "width": width, "height": height,
            "checks": {"plan": True, "duration": True, "format": True, "audio_stream": True, "checksum": True},
            "review_required": ["audio sync within 3 ms", "pause boundaries", "face-safe layouts", "flash detection",
                                "final measured loudness", "colour contrast"]}
