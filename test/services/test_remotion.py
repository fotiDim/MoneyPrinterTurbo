import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.schema import VideoAspect, VideoConcatMode, VideoParams, VideoTransitionMode
from app.services import remotion
from app.services import task as tm


class RemotionServiceTests(unittest.TestCase):
    def test_is_requested_reads_config(self):
        with patch.object(remotion.config, "app", {"video_renderer": "remotion"}):
            self.assertTrue(remotion.is_requested())
        with patch.object(remotion.config, "app", {"video_renderer": "moviepy"}):
            self.assertFalse(remotion.is_requested())
        with patch.object(remotion.config, "app", {}):
            self.assertFalse(remotion.is_requested())

    def test_is_enabled_requires_node_and_package(self):
        with (
            patch.object(remotion, "is_requested", return_value=True),
            patch.object(remotion, "_node_available", return_value=True),
            patch.object(remotion, "_package_installed", return_value=True),
        ):
            self.assertTrue(remotion.is_enabled())
        with (
            patch.object(remotion, "is_requested", return_value=True),
            patch.object(remotion, "_node_available", return_value=False),
            patch.object(remotion, "_package_installed", return_value=True),
        ):
            self.assertFalse(remotion.is_enabled())

    def test_ensure_ready_raises_when_requested_but_missing_deps(self):
        with (
            patch.object(remotion, "is_requested", return_value=True),
            patch.object(remotion, "_node_available", return_value=False),
            patch.object(remotion, "_package_installed", return_value=False),
        ):
            with self.assertRaises(remotion.RemotionNotReadyError) as ctx:
                remotion.ensure_ready()
        self.assertIn("Node.js", str(ctx.exception))

    def test_parse_srt_cues(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_path = Path(tmp_dir) / "subtitle.srt"
            subtitle_path.write_text(
                "1\n"
                "00:00:00,000 --> 00:00:01,000\n"
                "Hello\n"
                "\n"
                "2\n"
                "00:00:01,500 --> 00:00:02,500\n"
                "World\n",
                encoding="utf-8",
            )
            cues = remotion.parse_srt_cues(str(subtitle_path))
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Hello")
        self.assertEqual(cues[0]["startFrame"], 0)
        self.assertEqual(cues[0]["durationInFrames"], 30)
        self.assertEqual(cues[1]["startFrame"], 45)

    def test_build_composition_props_visual_only_skips_audio_and_subs(self):
        params = VideoParams(
            video_subject="test",
            video_aspect=VideoAspect.portrait.value,
            subtitle_enabled=True,
            voice_volume=0.8,
            bgm_volume=0.2,
        )
        clips = [
            {
                "src": "/tmp/a.mp4",
                "startFromSeconds": 0,
                "durationInFrames": 90,
                "speed": 1.0,
                "transition": "FadeIn",
                "slideSide": "left",
            }
        ]
        props = remotion.build_composition_props(
            clips=clips,
            params=params,
            audio_path="/tmp/voice.mp3",
            subtitle_path="/tmp/sub.srt",
            bgm_path="/tmp/bgm.mp3",
            visual_only=True,
        )
        self.assertTrue(props["visualOnly"])
        self.assertEqual(props["narrationSrc"], "")
        self.assertEqual(props["bgmSrc"], "")
        self.assertFalse(props["subtitles"]["enabled"])
        self.assertEqual(props["durationInFrames"], 90)
        self.assertEqual(props["width"], 1080)
        self.assertEqual(props["height"], 1920)

    def test_plan_timeline_clips_respects_speed_and_duration(self):
        fake_clip = MagicMock()
        fake_clip.duration = 10.0
        fake_clip.w = 1080
        fake_clip.h = 1920
        fake_clip.close = MagicMock()

        with patch.object(remotion, "VideoFileClip", return_value=fake_clip):
            clips = remotion.plan_timeline_clips(
                video_paths=["/tmp/material.mp4"],
                audio_duration=4.0,
                video_concat_mode=VideoConcatMode.sequential,
                video_transition_mode=VideoTransitionMode.fade_in,
                max_clip_duration=3,
                clip_speed=1.5,
            )

        self.assertGreaterEqual(len(clips), 1)
        self.assertEqual(clips[0]["speed"], 1.5)
        self.assertEqual(clips[0]["transition"], "FadeIn")
        self.assertEqual(clips[0]["durationInFrames"], 90)
        self.assertEqual(clips[0]["startFromSeconds"], 0.0)

    def test_scaffold_project_copies_src_and_symlinks_node_modules(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template = Path(tmp_dir) / "remotion"
            (template / "src").mkdir(parents=True)
            (template / "src" / "index.ts").write_text("export {}", encoding="utf-8")
            (template / "package.json").write_text("{}", encoding="utf-8")
            (template / "remotion.config.ts").write_text("// cfg", encoding="utf-8")
            (template / "tsconfig.json").write_text("{}", encoding="utf-8")
            (template / "node_modules" / "remotion").mkdir(parents=True)

            task_root = Path(tmp_dir) / "tasks" / "task-1"
            task_root.mkdir(parents=True)

            with (
                patch.object(remotion, "ensure_ready"),
                patch.object(remotion, "template_dir", return_value=str(template)),
                patch.object(
                    remotion.utils, "task_dir", return_value=str(task_root)
                ),
            ):
                project_path = remotion.scaffold_project("task-1", 1)

            project = Path(project_path)
            self.assertTrue((project / "src" / "index.ts").is_file())
            self.assertTrue((project / "package.json").is_file())
            self.assertTrue((project / "README.md").is_file())
            self.assertTrue((project / "public").is_dir())
            self.assertTrue((project / "node_modules").is_symlink())
            self.assertEqual(
                os.path.realpath(project / "node_modules"),
                os.path.realpath(template / "node_modules"),
            )

    def test_stage_props_into_project_uses_simple_public_names(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project = Path(tmp_dir) / "remotion-1"
            (project / "public").mkdir(parents=True)
            source_video = Path(tmp_dir) / "clip.mp4"
            source_audio = Path(tmp_dir) / "voice.mp3"
            source_video.write_bytes(b"video")
            source_audio.write_bytes(b"audio")
            props = {
                "clips": [
                    {
                        "src": str(source_video),
                        "startFromSeconds": 0,
                        "durationInFrames": 30,
                        "speed": 1.0,
                        "transition": "none",
                        "slideSide": "left",
                    }
                ],
                "narrationSrc": str(source_audio),
                "bgmSrc": "",
                "subtitles": {"fontPath": "", "cues": [], "enabled": False},
            }
            staged = remotion.stage_props_into_project(
                props, project_path=str(project)
            )
            self.assertFalse(os.path.isabs(staged["clips"][0]["src"]))
            self.assertTrue(staged["clips"][0]["src"].startswith("clip-"))
            self.assertTrue(staged["narrationSrc"].startswith("narration-"))
            self.assertTrue((project / "public" / staged["clips"][0]["src"]).is_file())

    def test_render_video_uses_project_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = os.path.join(tmp_dir, "out.mp4")
            props_path = os.path.join(tmp_dir, "input-props.json")
            Path(output_file).write_bytes(b"fake")
            completed = MagicMock(returncode=0, stdout="", stderr="")
            with (
                patch.object(remotion, "ensure_ready"),
                patch.object(remotion.subprocess, "run", return_value=completed) as run,
            ):
                remotion.render_video(
                    output_file=output_file,
                    props={"durationInFrames": 30, "clips": []},
                    props_path=props_path,
                    working_directory=tmp_dir,
                    threads=2,
                )

            self.assertEqual(run.call_args.kwargs["cwd"], tmp_dir)
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[0], "npx")
            self.assertIn("MoneyPrinterVideo", cmd)
            props = json.loads(Path(props_path).read_text(encoding="utf-8"))
            self.assertEqual(props["durationInFrames"], 30)


class RemotionTaskBranchTests(unittest.TestCase):
    def test_generate_final_videos_uses_moviepy_when_remotion_not_requested(self):
        params = VideoParams(video_subject="test", video_count=1)
        with (
            patch.object(tm.remotion, "is_requested", return_value=False),
            patch.object(tm.video, "combine_videos") as combine_videos,
            patch.object(tm.video, "generate_video", return_value=True),
            patch.object(tm.sm.state, "update_task"),
        ):
            result = tm.generate_final_videos(
                task_id="moviepy-task",
                params=params,
                downloaded_videos=["material.mp4"],
                audio_file="audio.mp3",
                subtitle_path="",
                audio_duration=5,
            )
        combine_videos.assert_called_once()
        self.assertEqual(result[3], [])

    def test_generate_final_videos_uses_remotion_when_requested(self):
        params = VideoParams(video_subject="test", video_count=1, bgm_type="")
        clips = [
            {
                "src": "/tmp/a.mp4",
                "startFromSeconds": 0,
                "durationInFrames": 90,
                "speed": 1.0,
                "transition": "none",
                "slideSide": "left",
            }
        ]
        project_path = os.path.join(tempfile.gettempdir(), "mpt-remotion-proj")

        def fake_render(**kwargs):
            Path(kwargs["output_file"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_file"]).write_bytes(b"ok")
            return kwargs["output_file"], clips, project_path

        with (
            patch.object(tm.remotion, "is_requested", return_value=True),
            patch.object(tm.remotion, "ensure_ready"),
            patch.object(
                tm.remotion, "scaffold_project", return_value=project_path
            ),
            patch.object(
                tm.remotion, "render_composition", side_effect=fake_render
            ) as render_composition,
            patch.object(tm.video, "combine_videos") as combine_videos,
            patch.object(tm.video, "generate_video") as generate_video,
            patch.object(tm.sm.state, "update_task"),
            patch.object(
                tm.utils,
                "task_dir",
                side_effect=lambda task_id: os.path.join(
                    tempfile.gettempdir(), f"mpt-remotion-{task_id}"
                ),
            ),
        ):
            finals, combined, warnings, projects = tm.generate_final_videos(
                task_id="remotion-task",
                params=params,
                downloaded_videos=["material.mp4"],
                audio_file="audio.mp3",
                subtitle_path="",
                audio_duration=5,
            )

        combine_videos.assert_not_called()
        generate_video.assert_not_called()
        self.assertEqual(render_composition.call_count, 2)
        self.assertTrue(render_composition.call_args_list[0].kwargs["visual_only"])
        self.assertFalse(render_composition.call_args_list[1].kwargs["visual_only"])
        self.assertEqual(render_composition.call_args_list[0].kwargs["project_path"], project_path)
        self.assertEqual(len(finals), 1)
        self.assertEqual(len(combined), 1)
        self.assertEqual(projects, [project_path])
        self.assertEqual(warnings, [])

    def test_generate_final_videos_remotion_runs_video_music_between_passes(self):
        params = VideoParams(
            video_subject="test",
            video_count=1,
            bgm_type="sonilo",
            bgm_volume=0.3,
            sonilo_bgm_prompt="warm",
        )
        clips = [
            {
                "src": "/tmp/a.mp4",
                "startFromSeconds": 0,
                "durationInFrames": 90,
                "speed": 1.0,
                "transition": "none",
                "slideSide": "left",
            }
        ]
        project_path = os.path.join(tempfile.gettempdir(), "mpt-remotion-bgm-proj")

        def fake_render(**kwargs):
            Path(kwargs["output_file"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_file"]).write_bytes(b"ok")
            return kwargs["output_file"], clips, project_path

        with (
            patch.object(tm.remotion, "is_requested", return_value=True),
            patch.object(tm.remotion, "ensure_ready"),
            patch.object(
                tm.remotion, "scaffold_project", return_value=project_path
            ),
            patch.object(
                tm.remotion, "render_composition", side_effect=fake_render
            ) as render_composition,
            patch.object(
                tm.sonilo,
                "generate_bgm",
                side_effect=lambda **kwargs: kwargs["output_path"],
            ) as generate_bgm,
            patch.object(tm.sm.state, "update_task"),
            patch.object(
                tm.utils,
                "task_dir",
                side_effect=lambda task_id: os.path.join(
                    tempfile.gettempdir(), f"mpt-remotion-bgm-{task_id}"
                ),
            ),
        ):
            tm.generate_final_videos(
                task_id="remotion-sonilo-task",
                params=params,
                downloaded_videos=["material.mp4"],
                audio_file="audio.mp3",
                subtitle_path="",
                audio_duration=5,
            )

        generate_bgm.assert_called_once()
        self.assertEqual(render_composition.call_count, 2)
        final_kwargs = render_composition.call_args_list[1].kwargs
        self.assertFalse(final_kwargs["visual_only"])
        self.assertTrue(str(final_kwargs["bgm_path"]).endswith("sonilo-bgm-1.m4a"))
        self.assertEqual(final_kwargs["project_path"], project_path)


if __name__ == "__main__":
    unittest.main()
