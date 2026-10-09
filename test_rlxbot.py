import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import bot
from editor import parse_byte_range
from shift_metadata import shift_metadata
from video_cache import video_cache_key


class RlxbotTests(unittest.TestCase):
    def test_suffix_byte_range_reads_from_end(self):
        self.assertEqual(parse_byte_range("bytes=-1024", 10_000), (8_976, 9_999))

    def test_open_ended_byte_range_reads_to_end(self):
        self.assertEqual(parse_byte_range("bytes=9000-", 10_000), (9_000, 9_999))

    def test_cache_key_identifies_the_actual_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mp4 = root / "example.mp4"
            mkv = root / "example.mkv"
            mp4.write_bytes(b"proxy")
            mkv.write_bytes(b"master")
            self.assertNotEqual(video_cache_key(mp4), video_cache_key(mkv))

    def test_discord_frame_number_is_one_based(self):
        scene = {
            "id": "T3/1",
            "title": "Test",
            "location": "Test",
            "in_frame": 0,
            "out_frame": 10,
            "keyframe_frame": 0,
        }
        body = bot.make_body(scene, Fraction(30), "Test", "Test")
        self.assertIn("**Frame:** 1", body)
        self.assertIn("**In:** 00:00.000", body)

    def test_scene_links_include_bracketed_post_titles(self):
        post = {"id": "T3/2", "title": "[T3/2] Second", "body": "Body"}
        saved_by_id = {
            "T3/1": {
                "title": "[T3/1] Old title",
                "thread_id": "111",
            },
            "T3/3": {
                "title": "[T3/3] Third",
                "thread_id": "333",
            },
        }
        planned_by_id = {
            "T3/1": {"title": "[T3/1] First"},
            "T3/2": post,
            "T3/3": {"title": "[T3/3] Third"},
        }

        linked = bot.add_scene_links(
            post, saved_by_id, "guild", planned_by_id
        )

        self.assertIn(
            "**Previous scene:** [[T3/1] First]"
            "(https://discord.com/channels/guild/111)",
            linked["body"],
        )
        self.assertIn(
            "**Next scene:** [[T3/3] Third]"
            "(https://discord.com/channels/guild/333)",
            linked["body"],
        )

    def test_shift_removes_leading_scene_and_updates_timecodes(self):
        metadata = {
            "version": 2,
            "video": "proxy.mp4",
            "frame_rate": "30/1",
            "frame_count": 100,
            "scenes": [
                {
                    "title": "Untitled",
                    "in_frame": 0,
                    "out_frame": 31,
                    "keyframe_frame": None,
                },
                {
                    "id": "T3/1",
                    "title": "Scene",
                    "in_frame": 31,
                    "out_frame": 100,
                    "keyframe_frame": 40,
                },
            ],
        }
        shifted, dropped = shift_metadata(
            metadata,
            -31,
            video="master.mkv",
            source="rockstargames.com",
        )
        self.assertEqual(dropped, 1)
        self.assertEqual(shifted["frame_count"], 69)
        self.assertEqual(shifted["video"], "master.mkv")
        self.assertEqual(shifted["source"], "rockstargames.com")
        self.assertEqual(len(shifted["scenes"]), 1)
        scene = shifted["scenes"][0]
        self.assertEqual(scene["in_frame"], 0)
        self.assertEqual(scene["out_frame"], 69)
        self.assertEqual(scene["keyframe_frame"], 9)
        self.assertEqual(scene["keyframe_timecode"], "00:00:00:09")


if __name__ == "__main__":
    unittest.main()
