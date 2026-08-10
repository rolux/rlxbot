#!/usr/bin/env python3

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VIDEOS_DIR = ROOT / "videos"
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def main():
    parser = argparse.ArgumentParser(
        description="Download the highest-quality YouTube video and audio streams"
    )
    parser.add_argument("video_id", help="11-character YouTube video ID")
    args = parser.parse_args()

    if not YOUTUBE_ID.fullmatch(args.video_id):
        parser.error("video_id must be an 11-character YouTube video ID")
    if shutil.which("yt-dlp") is None:
        raise FileNotFoundError("yt-dlp is not installed or not on PATH")
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError("ffmpeg is not installed or not on PATH")

    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={args.video_id}"
    output_template = str(VIDEOS_DIR / "%(id)s.%(ext)s")
    command = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--remux-video", "mp4",
        "--write-info-json",
        "--output", output_template,
        "--print", "after_move:Downloaded: %(filepath)s",
        url,
    ]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
