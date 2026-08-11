#!/usr/bin/env python3

import argparse
import hashlib
import html
import json
import mimetypes
import re
import subprocess
import sys
import time
import uuid
from fractions import Fraction
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
VIDEOS_DIR = ROOT / "videos"
SCENES_DIR = ROOT / "scenes"
REVIEWS_DIR = ROOT / "reviews"
DISCORD_DIR = ROOT / "discord"
TOKEN_FILE = ROOT / "untracked" / "token.txt"

DISCORD_API = "https://discord.com/api/v10"
FORUM_CHANNEL_ID = "1214480866085183508"
EXTENDED_LOOK_TAG_ID = "1535197149346922556"
USER_AGENT = "DiscordBot (https://github.com/rolux/rlxbot, 0.1.0)"


class UploadError(RuntimeError):
    pass


def load_metadata(filename):
    metadata_path = Path(filename).expanduser().resolve()
    if not metadata_path.is_file():
        raise FileNotFoundError(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata.get("scenes"), list):
        raise ValueError("Metadata contains no scene list")
    try:
        frame_rate = Fraction(str(metadata["frame_rate"]))
    except (KeyError, ValueError, ZeroDivisionError) as error:
        raise ValueError("Metadata has an invalid frame rate") from error
    if frame_rate <= 0:
        raise ValueError("Metadata has an invalid frame rate")
    return metadata_path, metadata, frame_rate


def resolve_video(metadata, explicit_video=None):
    if explicit_video:
        video_path = Path(explicit_video).expanduser().resolve()
    else:
        video_name = metadata.get("video")
        if not video_name:
            raise ValueError("Metadata does not name its source video; pass --video")
        video_path = (VIDEOS_DIR / Path(video_name).name).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    return video_path


def format_timestamp(frame, frame_rate):
    milliseconds = int(Fraction(frame * 1000, 1) / frame_rate + Fraction(1, 2))
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def make_title(scene):
    title = scene["title"].strip()
    if not title or title.casefold() == "untitled":
        return f"[{scene['id']}]"
    return f"[{scene['id']}] {title}"


def make_body(scene, frame_rate, source, location):
    return (
        f"**Source:** {source}\n"
        f"**In:** {format_timestamp(scene['in_frame'], frame_rate)}, "
        f"**Out:** {format_timestamp(scene['out_frame'], frame_rate)}, "
        f"**Frame:** {scene['keyframe_frame']}\n"
        f"**Location:** {location}"
    )


def build_post_plan(metadata, frame_rate, source, location):
    posts = []
    seen_ids = set()
    out_frame_is_exclusive = metadata.get("version", 1) >= 2
    for index, scene in enumerate(metadata["scenes"], 1):
        keyframe = scene.get("keyframe_frame")
        if keyframe is None:
            continue
        scene_id = scene.get("id")
        title = scene.get("title")
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise ValueError(f"Included scene {index} has no ID")
        if scene_id in seen_ids:
            raise ValueError(f"Duplicate scene ID: {scene_id}")
        seen_ids.add(scene_id)
        if not isinstance(title, str):
            raise ValueError(f"Scene {scene_id} has an invalid title")
        scene_location = scene.get("location", location)
        if not isinstance(scene_location, str) or not scene_location.strip():
            scene_location = location
        for field in ("in_frame", "out_frame", "keyframe_frame"):
            if not isinstance(scene.get(field), int) or scene[field] < 0:
                raise ValueError(f"Scene {scene_id} has an invalid {field}")
        out_frame = scene["out_frame"] if out_frame_is_exclusive else scene["out_frame"] + 1
        if not scene["in_frame"] <= keyframe < out_frame:
            raise ValueError(f"Scene {scene_id} has a keyframe outside its boundaries")
        normalized_scene = {**scene, "out_frame": out_frame}
        post_title = make_title(normalized_scene)
        if len(post_title) > 100:
            raise ValueError(f"Forum title is longer than 100 characters: {post_title}")
        posts.append({
            "id": scene_id,
            "title": post_title,
            "body": make_body(normalized_scene, frame_rate, source, scene_location),
            "frame": keyframe,
        })
    if not posts:
        raise ValueError("Metadata contains no included scenes")
    return posts


def limit_post_plan(posts, max_id=None):
    campaign = campaign_id(posts)
    numbered_posts = []
    for post in posts:
        match = re.fullmatch(r"([^/]+)/([1-9]\d*)", post["id"])
        if not match or match.group(1) != campaign:
            raise ValueError(f"Invalid scene ID: {post['id']}")
        numbered_posts.append((int(match.group(2)), post))

    expected_total = len(numbered_posts) if max_id is None else max_id
    if expected_total < 1:
        raise ValueError("--max-id must be at least 1")
    prefix = [(number, post) for number, post in numbered_posts if number <= expected_total]
    numbers = [number for number, _post in prefix]
    if numbers != list(range(1, expected_total + 1)):
        raise ValueError(
            f"Scene IDs must form an ordered, gap-free prefix from {campaign}/1 through {campaign}/{expected_total}"
        )
    return [post for _number, post in prefix]


def frame_path(video_path, frame):
    return SCENES_DIR / video_path.stem / "thumbnails" / f"frame_{frame:08d}.png"


def extract_frame(video_path, frame):
    output_path = frame_path(video_path, frame)
    if output_path.is_file():
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".tmp.png")
    command = [
        "ffmpeg", "-v", "error", "-i", str(video_path),
        "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1",
        "-fps_mode", "vfr", "-y", str(temporary_path),
    ]
    try:
        subprocess.run(command, check=True)
        if not temporary_path.is_file():
            raise UploadError(f"FFmpeg did not extract frame {frame}")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path


def write_review(metadata_path, video_path, posts, output_path=None):
    if output_path:
        review_path = Path(output_path).expanduser().resolve()
    else:
        review_path = REVIEWS_DIR / f"{metadata_path.stem}.html"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    for post in posts:
        image_path = extract_frame(video_path, post["frame"])
        cards.append(
            '<article class="post">'
            f'<h2>{html.escape(post["title"])}</h2>'
            f'<pre>{html.escape(post["body"])}</pre>'
            f'<img src="{html.escape(image_path.resolve().as_uri())}" '
            f'alt="{html.escape(post["title"])}">'
            '</article>'
        )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(metadata_path.stem)} upload review</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ max-width: 1200px; margin: 0 auto; padding: 32px; background: #111214; color: #f3f4f6; font: 14px/1.45 -apple-system, BlinkMacSystemFont, sans-serif; }}
  header {{ margin-bottom: 40px; }}
  .post {{ margin: 0 0 64px; padding-top: 24px; border-top: 1px solid #343840; }}
  h1, h2 {{ margin: 0 0 12px; }}
  h2 {{ font-size: 20px; }}
  pre {{ margin: 0 0 16px; white-space: pre-wrap; color: #d9dce1; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }}
  img {{ display: block; width: 100%; height: auto; background: #000; }}
</style>
</head>
<body>
<header><h1>Discord upload review</h1><p>{len(posts)} posts · {html.escape(video_path.name)}</p></header>
{''.join(cards)}
</body>
</html>
"""
    review_path.write_text(document, encoding="utf-8")
    return review_path


def load_token(token_path):
    path = Path(token_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Bot token file is empty: {path}")
    return token


def multipart_body(payload, image_path):
    boundary = f"rlxbot-{uuid.uuid4().hex}"
    attachment_fields = payload.get("attachments") or payload.get("message", {}).get("attachments") or []
    filename = attachment_fields[0].get("filename", image_path.name) if attachment_fields else image_path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts = []

    def add(value):
        parts.append(value.encode("utf-8") if isinstance(value, str) else value)

    add(f"--{boundary}\r\n")
    add('Content-Disposition: form-data; name="payload_json"\r\n')
    add("Content-Type: application/json\r\n\r\n")
    add(json.dumps(payload, ensure_ascii=False))
    add("\r\n")
    add(f"--{boundary}\r\n")
    add(f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n')
    add(f"Content-Type: {content_type}\r\n\r\n")
    add(image_path.read_bytes())
    add("\r\n")
    add(f"--{boundary}--\r\n")
    return boundary, b"".join(parts)


def discord_request(method, path, token, payload=None, image_path=None):
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    if image_path is not None:
        boundary, body = multipart_body(payload, image_path)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        body = None

    while True:
        request = Request(f"{DISCORD_API}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=60) as response:
                response_body = response.read()
                return json.loads(response_body) if response_body else None
        except HTTPError as error:
            response_body = error.read().decode("utf-8", "replace")
            try:
                details = json.loads(response_body)
            except json.JSONDecodeError:
                details = {"message": response_body}
            if error.code == 429:
                retry_after = float(details.get("retry_after", error.headers.get("Retry-After", 1)))
                print(f"Rate limited; retrying in {retry_after:.2f} seconds", flush=True)
                time.sleep(retry_after + 0.1)
                continue
            message = details.get("message", response_body or error.reason)
            code = details.get("code")
            suffix = f" (Discord code {code})" if code is not None else ""
            raise UploadError(f"Discord returned HTTP {error.code}: {message}{suffix}") from error
        except URLError as error:
            raise UploadError(f"Could not reach Discord: {error.reason}") from error


def validate_destination(token):
    channel = discord_request("GET", f"/channels/{FORUM_CHANNEL_ID}", token)
    if channel.get("type") != 15:
        raise UploadError(f"Channel {FORUM_CHANNEL_ID} is not a forum channel")
    tags = {tag["id"]: tag for tag in channel.get("available_tags", [])}
    if EXTENDED_LOOK_TAG_ID not in tags:
        raise UploadError("The Extended Look tag is not available in the target forum")
    return channel, tags[EXTENDED_LOOK_TAG_ID]


def campaign_id(posts):
    campaigns = {post["id"].split("/", 1)[0] for post in posts}
    if len(campaigns) != 1 or "" in campaigns:
        raise ValueError("All scene IDs must use one campaign prefix, such as T3/1")
    return campaigns.pop()


def state_path(campaign, explicit_state=None):
    if explicit_state:
        return Path(explicit_state).expanduser().resolve()
    return DISCORD_DIR / f"{campaign}.json"


def load_state(path, campaign):
    if not path.is_file():
        return {
            "version": 2,
            "campaign": campaign,
            "channel_id": FORUM_CHANNEL_ID,
            "tag_id": EXTENDED_LOOK_TAG_ID,
            "posts": [],
        }
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("channel_id") != FORUM_CHANNEL_ID:
        raise ValueError(f"Upload state does not match this Discord destination: {path}")
    if state.get("campaign", campaign) != campaign:
        raise ValueError(f"Upload state does not match campaign {campaign}: {path}")
    if not isinstance(state.get("posts"), list):
        raise ValueError(f"Invalid upload state: {path}")
    state["version"] = 2
    state["campaign"] = campaign
    state.pop("video", None)
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def validate_recorded_scene_ids(posts, explicit_state=None):
    campaign = campaign_id(posts)
    path = state_path(campaign, explicit_state)
    if not path.is_file():
        return
    state = load_state(path, campaign)
    metadata_ids = {post["id"] for post in posts}
    missing_ids = [
        saved_post.get("scene_id") for saved_post in state["posts"]
        if saved_post.get("scene_id") not in metadata_ids
    ]
    if missing_ids:
        raise ValueError(
            "Previously uploaded scene IDs are missing from the metadata: "
            + ", ".join(missing_ids)
        )


def post_snapshot(post, image_path, video_path):
    snapshot = {
        "title": post["title"],
        "body": post["body"],
        "tag_ids": [EXTENDED_LOOK_TAG_ID],
        "frame": post["frame"],
        "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "source_video": video_path.name,
    }
    snapshot["post_sha256"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return snapshot


def apply_post_update(saved_post, post, snapshot, image_path, token):
    title_changed = saved_post.get("title") != snapshot["title"]
    body_changed = saved_post.get("body") != snapshot["body"]
    image_changed = saved_post.get("image_sha256") != snapshot["image_sha256"]
    changes = [
        name for name, changed in (
            ("title", title_changed), ("body", body_changed), ("image", image_changed)
        ) if changed
    ]
    if not changes:
        return None

    if title_changed:
        discord_request("PATCH", f"/channels/{saved_post['thread_id']}", token, {
            "name": snapshot["title"],
        })

    message_response = None
    if image_changed:
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", post["id"]) + ".png"
        message_response = discord_request(
            "PATCH",
            f"/channels/{saved_post['thread_id']}/messages/{saved_post['message_id']}",
            token,
            payload={
                "content": snapshot["body"],
                "allowed_mentions": {"parse": []},
                "attachments": [{"id": "0", "filename": filename}],
            },
            image_path=image_path,
        )
    elif body_changed:
        message_response = discord_request(
            "PATCH",
            f"/channels/{saved_post['thread_id']}/messages/{saved_post['message_id']}",
            token,
            payload={"content": snapshot["body"], "allowed_mentions": {"parse": []}},
        )

    saved_post.update(snapshot)
    if message_response is not None:
        attachments = message_response.get("attachments") or []
        if attachments:
            saved_post["attachment_id"] = attachments[0].get("id")
            saved_post["attachment_url"] = attachments[0].get("url")
    return changes


def upload_posts(video_path, posts, token_path, delay, explicit_state=None):
    token = load_token(token_path)
    channel, tag = validate_destination(token)
    print(f"Destination: {channel['name']} · tag: {tag['name']}")
    campaign = campaign_id(posts)
    path = state_path(campaign, explicit_state)
    state = load_state(path, campaign)
    existing_numbers = []
    for saved_post in state["posts"]:
        match = re.fullmatch(rf"{re.escape(campaign)}/([1-9]\d*)", saved_post.get("scene_id", ""))
        if not match:
            raise ValueError(f"Invalid scene ID in Discord state: {saved_post.get('scene_id')}")
        existing_numbers.append(int(match.group(1)))
    if sorted(existing_numbers) != list(range(1, max(existing_numbers, default=0) + 1)):
        raise ValueError(f"Discord state contains a gap in the {campaign} sequence: {path}")
    saved_by_id = {post["scene_id"]: post for post in state["posts"]}
    last_created = max(existing_numbers, default=0)
    operations = 0
    for number, post in enumerate(posts, 1):
        image_path = extract_frame(video_path, post["frame"])
        snapshot = post_snapshot(post, image_path, video_path)
        saved_post = saved_by_id.get(post["id"])
        if saved_post is not None:
            if saved_post.get("post_sha256") == snapshot["post_sha256"]:
                print(f"[{number}/{len(posts)}] Unchanged {post['title']}")
                continue
            print(f"[{number}/{len(posts)}] Updating {post['title']}…", flush=True)
            changes = apply_post_update(saved_post, post, snapshot, image_path, token)
            if changes is None:
                saved_post.update(snapshot)
                changes = []
            save_state(path, state)
            operations += 1
            print(f"    updated: {', '.join(changes) or 'state'}", flush=True)
            if number < len(posts) and delay > 0:
                time.sleep(delay)
            continue

        scene_number = int(post["id"].split("/", 1)[1])
        if scene_number != last_created + 1:
            raise UploadError(
                f"Refusing to create {post['id']}; the next sequential ID must be {campaign}/{last_created + 1}"
            )
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", post["id"]) + ".png"
        payload = {
            "name": post["title"],
            "applied_tags": [EXTENDED_LOOK_TAG_ID],
            "message": {
                "content": post["body"],
                "allowed_mentions": {"parse": []},
                "attachments": [{"id": "0", "filename": filename}],
            },
        }
        print(f"[{number}/{len(posts)}] Posting {post['title']}…", flush=True)
        response = discord_request(
            "POST", f"/channels/{FORUM_CHANNEL_ID}/threads", token,
            payload=payload, image_path=image_path,
        )
        message = response.get("message", {})
        saved_post = {
            "scene_id": post["id"],
            "thread_id": response["id"],
            "message_id": message.get("id", response["id"]),
            "attachment_id": (message.get("attachments") or [{}])[0].get("id"),
            "attachment_url": (message.get("attachments") or [{}])[0].get("url"),
            **snapshot,
        }
        state["posts"].append(saved_post)
        saved_by_id[post["id"]] = saved_post
        last_created = scene_number
        save_state(path, state)
        operations += 1
        print(f"    thread {response['id']} saved to {path}", flush=True)
        if number < len(posts) and delay > 0:
            time.sleep(delay)
    if operations == 0:
        print(f"All {len(posts)} posts are unchanged")
    return path


def delete_campaign(campaign, token_path, delay, execute=False, explicit_state=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", campaign):
        raise ValueError(f"Invalid campaign ID: {campaign}")
    path = state_path(campaign, explicit_state)
    if not path.is_file():
        raise FileNotFoundError(path)
    state = load_state(path, campaign)
    posts = state["posts"]
    if not posts:
        print(f"No recorded Discord posts for {campaign}")
        return path

    seen_threads = set()
    for saved_post in posts:
        scene_id = saved_post.get("scene_id")
        thread_id = saved_post.get("thread_id")
        if not isinstance(scene_id, str) or not isinstance(thread_id, str) or not thread_id.isdigit():
            raise ValueError(f"Invalid Discord post record in {path}")
        if thread_id in seen_threads:
            raise ValueError(f"Duplicate Discord thread ID in {path}: {thread_id}")
        seen_threads.add(thread_id)

    print(f"Campaign {campaign}: {len(posts)} recorded Discord posts")
    for saved_post in posts:
        print(f"  {saved_post['scene_id']}  thread {saved_post['thread_id']}")
    if not execute:
        print("No Discord requests were made. Add --execute to begin the confirmed deletion.")
        return path

    try:
        confirmation = input(
            f"Permanently delete all {len(posts)} recorded Discord posts for {campaign}? "
            "Type yes to continue: "
        )
    except EOFError:
        confirmation = ""
    if confirmation.strip().casefold() != "yes":
        print("Deletion cancelled")
        return path

    token = load_token(token_path)
    for saved_post in posts:
        thread = discord_request("GET", f"/channels/{saved_post['thread_id']}", token)
        if str(thread.get("parent_id")) != FORUM_CHANNEL_ID:
            raise UploadError(
                f"Refusing to delete thread {saved_post['thread_id']}; "
                "it is not a post in the configured forum"
            )

    total = len(posts)
    for index, saved_post in enumerate(list(posts), 1):
        print(
            f"[{index}/{total}] Deleting {saved_post['scene_id']} "
            f"(thread {saved_post['thread_id']})…",
            flush=True,
        )
        discord_request("DELETE", f"/channels/{saved_post['thread_id']}", token)
        state["posts"] = [
            record for record in state["posts"]
            if record.get("thread_id") != saved_post["thread_id"]
        ]
        save_state(path, state)
        if index < total and delay > 0:
            time.sleep(delay)
    print(f"Deleted all {total} recorded Discord posts for {campaign}")
    return path


def add_common_arguments(parser):
    parser.add_argument("metadata", help="Scene metadata JSON exported by the editor")
    parser.add_argument("--video", help="Override the source video named by the metadata")
    parser.add_argument("--source", default="Netflix")
    parser.add_argument("--location", default="t.b.d.")
    parser.add_argument("--max-id", type=int, help="Process only the verified sequential prefix through this ID")


def main():
    parser = argparse.ArgumentParser(description="Upload scene-by-scene forum posts to Discord")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser("review", help="Generate an HTML review of every planned post")
    add_common_arguments(review_parser)
    review_parser.add_argument("--output", help="Review HTML output path")

    upload_parser = subparsers.add_parser("upload", help="Upload validated posts to Discord")
    add_common_arguments(upload_parser)
    upload_parser.add_argument("--execute", action="store_true", help="Actually create or update Discord posts")
    upload_parser.add_argument("--delay", type=float, default=10.0, help="Seconds between posts (default: 10)")
    upload_parser.add_argument("--token", default=str(TOKEN_FILE), help="File containing the bot token")
    upload_parser.add_argument("--state", help="Override the Discord upload-state JSON path")

    delete_parser = subparsers.add_parser(
        "delete", help="Delete every Discord post recorded for one campaign"
    )
    delete_parser.add_argument("campaign", help="Campaign ID whose recorded posts should be deleted, such as T3")
    delete_parser.add_argument("--execute", action="store_true", help="Enable deletion after an interactive confirmation")
    delete_parser.add_argument("--delay", type=float, default=10.0, help="Seconds between deletions (default: 10)")
    delete_parser.add_argument("--token", default=str(TOKEN_FILE), help="File containing the bot token")
    delete_parser.add_argument("--state", help="Override the Discord upload-state JSON path")

    args = parser.parse_args()
    if args.command == "delete":
        if args.delay < 0:
            raise ValueError("--delay cannot be negative")
        delete_campaign(args.campaign, args.token, args.delay, args.execute, args.state)
        return

    metadata_path, metadata, frame_rate = load_metadata(args.metadata)
    video_path = resolve_video(metadata, args.video)
    posts = build_post_plan(metadata, frame_rate, args.source, args.location)
    if args.command == "upload":
        validate_recorded_scene_ids(posts, args.state)
    posts = limit_post_plan(posts, args.max_id)

    if args.command == "review":
        review_path = write_review(metadata_path, video_path, posts, args.output)
        print(f"Generated review for {len(posts)} posts")
        print(review_path)
        return

    if not args.execute:
        print(f"Validated {len(posts)} posts for {video_path.name}")
        print("No Discord requests were made. Add --execute to create the posts.")
        return
    if args.delay < 0:
        raise ValueError("--delay cannot be negative")
    upload_posts(video_path, posts, args.token, args.delay, args.state)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, UploadError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
