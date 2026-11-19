"""Translate the reviewed scene and speaker tables into the immutable edit plan."""
from .plan import PlanError, require, validate


def build_plan(workspace, project, version, template, title, cta, target, formats, style, assets, people_rows, cut_rows):
    assets = {str(asset["id"]): asset for asset in assets if asset["kind"] == "footage"}
    people, by_name = {}, {}
    for index, row in enumerate(people_rows):
        if not any(row.get(key) for key in ("Name", "Title", "Company")):
            continue
        name = str(row.get("Name") or "").strip()
        require(name and name not in by_name, "Every speaker needs a unique name in this edit.")
        person_id = f"p{index + 1}"
        by_name[name] = person_id
        people[person_id] = {
            "name": name, "title": str(row.get("Title") or "").strip(),
            "company": str(row.get("Company") or "").strip(),
            "confirmed": row.get("Confirmed") is True,
            "consent": row.get("Permission") is True,
        }
    clips, segments = {}, []
    for row in cut_rows:
        if not row.get("Include", True):
            continue
        source = str(row.get("Source") or "")
        require(source in assets, "Choose uploaded footage for every included scene.")
        asset = assets[source]
        clip_id = "clip-" + source
        clips[clip_id] = {"original_key": asset["object_key"]}
        visible = [name.strip() for name in str(row.get("People") or "").split(",") if name.strip()]
        require(all(name in by_name for name in visible), "List each visible person in the speaker table using the same name.")
        name_card = str(row.get("Name card") or "").strip()
        require(not name_card or name_card in visible, "A name card must identify someone listed in that scene.")
        try:
            start = float(row.get("In (s)", 0))
            end = float(row.get("Out (s)", 0))
        except (TypeError, ValueError):
            raise PlanError("Enter numeric in/out times for each included scene.") from None
        segment = {
            "clip": clip_id, "in": start, "out": end, "role": row.get("Role", "broll"),
            "people": [by_name[name] for name in visible],
            "pause_checked": row.get("Pause checked") is True,
            "audio": row.get("Audio", "speech"), "frame": row.get("Framing", "fit"),
            "captions": [],
        }
        if name_card:
            segment["name_card"] = by_name[name_card]
        caption = str(row.get("Caption") or "").strip()
        if caption:
            segment["captions"] = [{"start": 0, "end": end - start, "text": caption}]
        segments.append(segment)
    plan = {
        "workspace_id": workspace, "project_id": project, "version": version, "template": template,
        "title": title.strip(), "cta": cta.strip(), "target_seconds": target,
        "formats": list(formats), "style": style, "clips": clips, "people": people, "segments": segments,
    }
    validate(plan)
    return plan
