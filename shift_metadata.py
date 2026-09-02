#!/usr/bin/env python3

import argparse
import json
from fractions import Fraction
from pathlib import Path


def frame_timecode(frame, frame_rate):
    nominal_rate = round(float(frame_rate))
    total_seconds, frame_in_second = divmod(frame, nominal_rate)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame_in_second:02d}"


def shift_metadata(metadata, shift, frame_count=None, video=None, source=None):
    if not isinstance(metadata.get("scenes"), list):
        raise ValueError("Metadata contains no scene list")
    try:
        frame_rate = Fraction(str(metadata["frame_rate"]))
        old_frame_count = int(metadata["frame_count"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError("Metadata has invalid frame information") from error
    if frame_rate <= 0 or old_frame_count <= 0:
        raise ValueError("Metadata has invalid frame information")

    new_frame_count = old_frame_count + shift if frame_count is None else frame_count
    if new_frame_count <= 0:
        raise ValueError("Shifted frame count must be positive")

    shifted_scenes = []
    dropped = 0
    for index, scene in enumerate(metadata["scenes"], start=1):
        try:
            start = int(scene["in_frame"]) + shift
            end = int(scene["out_frame"]) + shift
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Scene {index} has invalid boundaries") from error
        if end <= 0 or start >= new_frame_count:
            dropped += 1
            continue
        start = max(0, start)
        end = min(new_frame_count, end)
        if start >= end:
            dropped += 1
            continue

        keyframe = scene.get("keyframe_frame")
        if keyframe is not None:
            keyframe = int(keyframe) + shift
            if not start <= keyframe < end:
                keyframe = None

        shifted_scenes.append({
            **scene,
            "in_frame": start,
            "out_frame": end,
            "keyframe_frame": keyframe,
            "in_timecode": frame_timecode(start, frame_rate),
            "out_timecode": frame_timecode(end, frame_rate),
            "keyframe_timecode": (
                None if keyframe is None else frame_timecode(keyframe, frame_rate)
            ),
        })

    if shift > 0:
        shifted_scenes.insert(0, {
            "title": "Untitled",
            "location": "t.b.d.",
            "in_frame": 0,
            "out_frame": shift,
            "keyframe_frame": None,
            "in_timecode": frame_timecode(0, frame_rate),
            "out_timecode": frame_timecode(shift, frame_rate),
            "keyframe_timecode": None,
        })
    if not shifted_scenes:
        raise ValueError("Shift removed every scene")

    previous_end = 0
    for index, scene in enumerate(shifted_scenes, start=1):
        if scene["in_frame"] != previous_end:
            raise ValueError(f"Shift created a gap before scene {index}")
        previous_end = scene["out_frame"]
    if previous_end != new_frame_count:
        raise ValueError(
            f"Shifted scenes end at {previous_end}, not frame count {new_frame_count}"
        )

    result = {
        **metadata,
        "frame_count": new_frame_count,
        "scenes": shifted_scenes,
    }
    if video is not None:
        result["video"] = video
    if source is not None:
        result["source"] = source
    return result, dropped


def main():
    parser = argparse.ArgumentParser(
        description="Shift zero-based scene metadata onto a corrected video timeline"
    )
    parser.add_argument("metadata")
    parser.add_argument("shift", type=int, help="Frames to add to every stored frame index")
    parser.add_argument("--frame-count", type=int, help="Override the shifted frame count")
    parser.add_argument("--video", help="Replace the metadata video filename")
    parser.add_argument("--source", help="Replace the human-readable source label")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--in-place", action="store_true", help="Replace the input JSON")
    args = parser.parse_args()
    if args.output and args.in_place:
        parser.error("--output and --in-place cannot be combined")

    source = Path(args.metadata).expanduser().resolve()
    metadata = json.loads(source.read_text(encoding="utf-8"))
    shifted, dropped = shift_metadata(
        metadata,
        args.shift,
        frame_count=args.frame_count,
        video=args.video,
        source=args.source,
    )
    if args.in_place:
        output = source
    elif args.output:
        output = Path(args.output).expanduser().resolve()
    else:
        output = source.with_name(f"{source.stem}.shifted.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(shifted, indent=2) + "\n", encoding="utf-8")
    print(f"Shift: {args.shift:+d} frames")
    print(f"Frame count: {metadata['frame_count']} -> {shifted['frame_count']}")
    print(f"Scenes: {len(metadata['scenes'])} -> {len(shifted['scenes'])} ({dropped} removed)")
    print(output)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"Error: {error}")
