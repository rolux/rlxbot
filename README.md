# rlxbot

Local tools for turning a video into scene-by-scene Discord forum posts. The
workflow combines automatic cut detection with a frame-accurate browser editor,
an HTML review step, and a resumable Discord uploader.

The project is intentionally small. `editor.py` prepares and edits scene
metadata; `bot.py` reviews, creates, and updates Discord posts. All state is
stored in local files rather than a database.

## Requirements

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- FFmpeg and FFprobe on `PATH`
- A Discord bot token for uploading
- Optional: `yt-dlp` for `download.py`

Install the Python dependencies:

```sh
uv sync
```

## Open the editor

Put a source video in `videos/` and start the local editor:

```sh
uv run editor.py editor videos/example.mp4 --port 8028
```

If the video has not been prepared yet, this command automatically runs
PySceneDetect and generates the detailed mean and slitscan timelines plus the
complete overview timelines. Existing cut and timeline data is reused on later
runs. Generated data is written beneath `cuts/` and `timelines/`; both are local
working directories and are not tracked by Git.

Open the displayed local URL in a browser. The editor supports adjusting scene
boundaries, splitting and merging scenes, naming scenes, editing locations, and
selecting exact keyframes. Metadata can be loaded and saved as JSON. The active
video is recorded in the page URL, so reloading restores the same selection.

The individual preparation commands remain available when either result needs
to be regenerated explicitly:

```sh
uv run editor.py cuts videos/example.mp4
uv run editor.py timelines videos/example.mp4
```

Useful controls:

- Click the video or press Space to play or pause.
- Left/Right move by one frame; Shift-Left/Right move backward or forward by one second.
- Up/Down jump to the previous or next cut; Shift-Up/Down jump to the beginning or end of the video.
- Comma/period select the previous or next scene; `/` selects the scene at the playhead.
- `[` and `]` jump to the first or last frame of the selected scene; `\` jumps to its keyframe.
- `E` edits the selected scene title.
- `I` sets the in point, `O` sets the out point, and `S` splits the selected scene (where valid).
- `K` sets the keyframe; Shift-K removes it.
- `=` and `-` change volume; `0` toggles mute.
- Click the bottom-right time display to toggle timecode and frame number.

## Review posts

Generate a static HTML review before uploading:

```sh
uv run bot.py review scenes/example.json --max-id 10
```

The review contains the exact forum titles, message bodies, and PNG frames. It
is written to `reviews/` unless `--output` is provided.

## Discord configuration

The target forum channel and forum tag are constants near the top of `bot.py`.
Store the bot token as plain text in:

```text
untracked/token.txt
```

Never commit that file. The bot needs access to the target forum and permission
to create posts, send messages in threads, attach files, and manage threads.

## Upload and update

Without `--execute`, `upload` performs local structural validation and makes no
Discord requests:

```sh
uv run bot.py upload scenes/example.json --max-id 10
```

To create or update the verified sequential prefix:

```sh
uv run bot.py upload scenes/example.json --max-id 10 --execute
```

Discord thread IDs, starter-message IDs, attachment IDs, source information,
and content hashes are saved after every successful operation in
`discord/T3.json`. Subsequent runs skip unchanged posts, edit changed posts,
and create only the next sequential IDs. A previously uploaded ID disappearing
from the complete metadata stops the run.

The default delay is ten seconds between changed posts. Discord `429` responses
are retried automatically using the returned retry interval.

## Delete a test campaign

Deletion is limited to every thread recorded in one campaign state file. First
preview the targets:

```sh
uv run bot.py delete T3
```

Then enable deletion:

```sh
uv run bot.py delete T3 --execute
```

The command still requires typing `yes`, verifies that every recorded thread
belongs to the configured forum, and updates the local state after each
successful deletion. Discord deletion is permanent.

## Optional YouTube download

If `yt-dlp` is installed, the helper accepts an eleven-character YouTube ID and
downloads the highest-quality video and audio streams into `videos/`:

```sh
uv run download.py VIDEO_ID
```

## License

This is free and unencumbered software released into the public domain under
the [Unlicense](LICENSE).
