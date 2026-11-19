"""Validate an immutable, versioned edit plan before any rendering starts."""
import math
import re

FORMATS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}



class PlanError(ValueError):
    pass


def require(value, message):
    if not value:
        raise PlanError(message)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def text(value, maximum=200):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and not any(ord(c) < 32 for c in value)


def object_key(value):
    return (isinstance(value, str) and 0 < len(value) <= 512 and not value.startswith("/")
            and all(part not in ("", ".", "..") for part in value.split("/"))
            and not any(c in value for c in ("\\", "\x00", "?", "#", ":")))


def validate(plan):
    require(isinstance(plan, dict), "The edit plan must be an object.")
    for key in ("workspace_id", "project_id"):
        require(isinstance(plan.get(key), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", plan[key]), f"Invalid {key}.")
    require(type(plan.get("version")) is int and plan["version"] > 0, "Use a positive integer version.")
    require(plan.get("template") in ("interview_reel", "session_interview", "event_recap", "ad_cutdown"), "Choose a supported template.")
    formats = plan.get("formats")
    require(isinstance(formats, list) and formats and all(isinstance(f, str) and f in FORMATS for f in formats)
            and len(formats) == len(set(formats)), "Choose unique output formats.")
    require(number(plan.get("target_seconds")) and 0 < plan["target_seconds"] <= 95, "Set a target of at most 95 seconds.")
    require(text(plan.get("title")), "Enter the confirmed project title.")
    require(text(plan.get("cta")), "Enter the confirmed end-card text.")
    style = plan.get("style")
    require(isinstance(style, dict), "Choose the project's style.")
    for key in ("background", "text", "accent"):
        require(isinstance(style.get(key), str) and re.fullmatch("#[0-9a-fA-F]{6}", style[key]), f"Invalid style colour {key}.")
    require(object_key(style.get("font_key")), "Choose a licensed font file from storage.")
    require(style.get("font_licensed") is True, "Confirm the font licence.")
    require(object_key(style.get("logo_key")), "Choose the actual logo file from storage.")
    people = plan.get("people", {})
    require(isinstance(people, dict), "People must be keyed by person ID.")
    clips = plan.get("clips")
    require(isinstance(clips, dict) and clips, "Add at least one footage clip.")
    for clip_id, clip in clips.items():
        require(text(clip_id, 80) and isinstance(clip, dict) and object_key(clip.get("original_key")), "Invalid clip storage key.")
    segments = plan.get("segments")
    require(isinstance(segments, list) and 0 < len(segments) <= 100, "Select one to 100 segments.")
    duration = 2 + 3  # Title and end card.
    for index, segment in enumerate(segments, 1):
        require(isinstance(segment, dict), f"Segment {index} must be an object.")
        clip = segment.get("clip")
        require(isinstance(clip, str) and clip in clips, f"Segment {index} references missing footage.")
        start, end = segment.get("in"), segment.get("out")
        require(number(start) and number(end) and 0 <= start < end, f"Invalid cut times in segment {index}.")
        duration += end - start
        require(segment.get("role") in ("ask", "answer", "session", "broll"), f"Choose a role for segment {index}.")
        require(segment.get("pause_checked") is True, f"Confirm natural-pause boundaries for segment {index}.")
        visible = segment.get("people")
        require(isinstance(visible, list) and all(isinstance(p, str) and p in people for p in visible), f"Confirm visible people in segment {index}.")
        for person_id in visible:
            person = people[person_id]
            require(isinstance(person, dict) and person.get("confirmed") is True and person.get("consent") is True,
                    f"Confirm identity and permission for {person_id}.")
        person_id = segment.get("name_card")
        if person_id is not None:
            require(isinstance(person_id, str) and person_id in visible, f"The name card in segment {index} must identify a visible person.")
            person = people[person_id]
            require(all(text(person.get(field), 100) for field in ("name", "title", "company")),
                    f"Complete the name, title, and company for {person_id}.")
        audio = segment.get("audio", "speech")
        require(audio in ("speech", "mute"), f"Invalid audio mode in segment {index}.")
        frame = segment.get("frame", "fit")
        require(frame in ("fit", "center_crop"), f"Invalid framing in segment {index}.")
        if index > 1 and segments[index - 2].get("clip") == clip:
            require(segments[index - 2].get("frame", "fit") == frame, "Consecutive cuts from the same shot need steady framing.")
        captions = segment.get("captions", [])
        require(isinstance(captions, list), f"Invalid captions in segment {index}.")
        last_end = 0
        for caption in captions:
            require(isinstance(caption, dict) and text(caption.get("text"), 180), "Caption text must be confirmed, single-line text.")
            a, b = caption.get("start"), caption.get("end")
            require(number(a) and number(b) and last_end <= a < b <= end - start, "Captions must be ordered inside their segment.")
            last_end = b
    require(duration <= plan["target_seconds"], f"The edit is {duration:.2f}s including cards, above the target.")
    return duration
