import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import (
    VideoAspect,
    VideoParams,
    normalize_video_sources,
    video_sources_from_params,
)
from app.services import llm


class TestVideoAspect(unittest.TestCase):
    def test_to_resolution_known_aspects(self):
        self.assertEqual(VideoAspect.landscape.to_resolution(), (1920, 1080))
        self.assertEqual(VideoAspect.portrait.to_resolution(), (1080, 1920))
        self.assertEqual(VideoAspect.square.to_resolution(), (1080, 1080))

    def test_to_resolution_rejects_unsupported_value(self):
        with self.assertRaises(ValueError):
            VideoAspect.to_resolution("4:5")


class TestVideoSources(unittest.TestCase):
    def test_normalize_video_sources_dedupes_and_filters(self):
        self.assertEqual(
            normalize_video_sources(["pexels", "PEXELS", "local", "nope"]),
            ["pexels", "local"],
        )

    def test_video_params_accepts_legacy_video_source(self):
        params = VideoParams(video_subject="demo", video_source="pixabay")
        self.assertEqual(params.video_sources, ["pixabay"])
        self.assertEqual(params.video_source, "pixabay")

    def test_video_params_accepts_multi_sources(self):
        params = VideoParams(
            video_subject="demo", video_sources=["local", "pexels", "coverr"]
        )
        self.assertEqual(params.video_sources, ["local", "pexels", "coverr"])
        self.assertEqual(video_sources_from_params(params), ["local", "pexels", "coverr"])

    def test_normalize_material_plan_filters_invalid_slots(self):
        plan = llm._normalize_material_plan(
            [
                {"kind": "local", "file": "logo.png"},
                {"kind": "search", "term": "office desk", "source": "pexels"},
                {"kind": "search", "term": "city night", "source": "unknown"},
                {"kind": "local", "file": "missing.mp4"},
                {"kind": "search", "term": "", "source": "pixabay"},
            ],
            online_sources=["pexels", "pixabay"],
            local_files=["logo.png", "ending.mp4"],
        )
        self.assertEqual(
            plan,
            [
                {"kind": "local", "file": "logo.png"},
                {"kind": "search", "term": "office desk", "source": "pexels"},
                {"kind": "search", "term": "city night", "source": "pexels"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
