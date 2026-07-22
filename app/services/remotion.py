"""
Optional Remotion (https://www.remotion.dev/) full composition renderer.

When ``video_renderer = "remotion"`` in config.toml, MoneyPrinterTurbo composes
clips, transitions, subtitles, and audio via the bundled ``remotion/`` project
instead of MoviePy. Upstream LLM / TTS / material download is unchanged.

Setup:
  1. Install Node.js 18+
  2. ``cd remotion && npm install``
  3. Set ``video_renderer = "remotion"`` (or choose Remotion in the WebUI)

Remotion license: companies may need a paid license —
https://www.remotion.dev/docs/license
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, List, Optional

from loguru import logger
from moviepy import AudioFileClip, VideoFileClip

from app.config import config
from app.models.schema import (
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.services import video as video_service
from app.utils import utils

FPS = 30
COMPOSITION_ID = "MoneyPrinterVideo"
ENTRY_FILE = "src/index.ts"
_TRANSITION_DURATION_SECONDS = 1.0

_SLIDE_SIDES = ("left", "right", "top", "bottom")
_CONCRETE_TRANSITIONS = (
    VideoTransitionMode.fade_in.value,
    VideoTransitionMode.fade_out.value,
    VideoTransitionMode.slide_in.value,
    VideoTransitionMode.slide_out.value,
    VideoTransitionMode.zoom_in.value,
    VideoTransitionMode.zoom_out.value,
)


class RemotionNotReadyError(RuntimeError):
    """Raised when Remotion was requested but Node or npm deps are missing."""


class RemotionRenderError(RuntimeError):
    """Raised when the Remotion CLI render fails."""


@dataclass(frozen=True)
class RemotionReadiness:
    requested: bool
    node_available: bool
    package_installed: bool

    @property
    def ready(self) -> bool:
        return self.requested and self.node_available and self.package_installed

    @property
    def message(self) -> str:
        if not self.requested:
            return "Remotion renderer is not selected (video_renderer != remotion)."
        missing = []
        if not self.node_available:
            missing.append("Node.js 18+ (node/npm on PATH)")
        if not self.package_installed:
            missing.append("remotion dependencies (`cd remotion && npm install`)")
        return (
            "Remotion renderer is enabled but not ready. Install: "
            + "; ".join(missing)
            + "."
        )


def project_dir() -> str:
    return os.path.join(config.root_dir, "remotion")


def is_requested() -> bool:
    value = str(config.app.get("video_renderer", "moviepy") or "moviepy").strip().lower()
    return value == "remotion"


def _node_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npm") is not None


def _package_installed() -> bool:
    return os.path.isdir(os.path.join(project_dir(), "node_modules", "remotion"))


def get_readiness() -> RemotionReadiness:
    return RemotionReadiness(
        requested=is_requested(),
        node_available=_node_available(),
        package_installed=_package_installed(),
    )


def is_enabled() -> bool:
    """True only when Remotion is selected and the local runtime is ready."""
    return get_readiness().ready


def ensure_ready() -> None:
    readiness = get_readiness()
    if readiness.requested and not readiness.ready:
        raise RemotionNotReadyError(readiness.message)


def _seconds_to_frames(seconds: float, *, minimum: int = 1) -> int:
    return max(minimum, int(round(float(seconds) * FPS)))


def _parse_srt_timestamp(value: str) -> float:
    match = re.match(
        r"(?P<h>\d+):(?P<m>\d+):(?P<s>\d+)[,.](?P<ms>\d+)",
        value.strip(),
    )
    if not match:
        return 0.0
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    millis = int(match.group("ms").ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt_cues(subtitle_path: str) -> List[dict[str, Any]]:
    """Parse an SRT file into Remotion subtitle cue props."""
    if not subtitle_path or not os.path.isfile(subtitle_path):
        return []

    raw = open(subtitle_path, "r", encoding="utf-8-sig", errors="replace").read()
    blocks = re.split(r"\n\s*\n", raw.strip(), flags=re.MULTILINE)
    cues: List[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        # Index line is optional; find the arrow line.
        timing_index = 0
        if "-->" not in lines[0] and len(lines) >= 2 and "-->" in lines[1]:
            timing_index = 1
        if "-->" not in lines[timing_index]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[timing_index].split("-->")]
        text = "\n".join(lines[timing_index + 1 :]).strip()
        if not text:
            continue
        start = _parse_srt_timestamp(start_raw)
        end = _parse_srt_timestamp(end_raw)
        if end <= start:
            continue
        cues.append(
            {
                "text": text,
                "startFrame": _seconds_to_frames(start, minimum=0),
                "durationInFrames": max(1, _seconds_to_frames(end - start)),
            }
        )
    return cues


def _resolve_subtitle_background(params: VideoParams) -> Optional[str]:
    if isinstance(params.text_background_color, bool):
        return "#000000" if params.text_background_color else None
    if not params.text_background_color:
        return None
    return str(params.text_background_color)


def _pick_transition(transition_mode) -> tuple[str, str]:
    transition_value = getattr(transition_mode, "value", transition_mode)
    side = random.choice(_SLIDE_SIDES)
    if transition_value in (None, VideoTransitionMode.none.value):
        return "none", side
    if transition_value == VideoTransitionMode.shuffle.value:
        return random.choice(_CONCRETE_TRANSITIONS), side
    if transition_value in _CONCRETE_TRANSITIONS:
        return str(transition_value), side
    return "none", side


def _probe_clip_meta(video_path: str) -> tuple[float, int, int]:
    clip = VideoFileClip(video_path)
    try:
        return float(clip.duration), int(clip.w), int(clip.h)
    finally:
        clip.close()


def plan_timeline_clips(
    video_paths: List[str],
    audio_duration: float,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    clip_speed: float = 1.0,
) -> List[dict[str, Any]]:
    """
    Build Remotion clip props covering narration duration (plus safety margin).

    Mirrors MoviePy ``combine_videos`` timeline rules: source slice length scales
    with playback speed, unique-source prioritization in random mode, and looping
    when materials are shorter than the required duration.
    """
    required_video_duration = video_service._get_required_video_duration(audio_duration)
    normalized_clip_speed = utils.normalize_clip_speed(clip_speed)
    source_clip_duration = max_clip_duration * normalized_clip_speed

    subclipped_items = []
    for video_path in video_paths:
        clip_duration, clip_w, clip_h = _probe_clip_meta(video_path)
        start_time = 0.0
        while start_time < clip_duration:
            end_time = min(start_time + source_clip_duration, clip_duration)
            if end_time > start_time:
                subclipped_items.append(
                    video_service.SubClippedVideoClip(
                        file_path=video_path,
                        start_time=start_time,
                        end_time=end_time,
                        width=clip_w,
                        height=clip_h,
                        source_file_path=video_path,
                    )
                )
            start_time = end_time
            if (
                getattr(video_concat_mode, "value", video_concat_mode)
                == VideoConcatMode.sequential.value
            ):
                break

    subclipped_items = video_service._prioritize_unique_source_clips(
        subclipped_items=subclipped_items,
        concat_mode=video_concat_mode,
    )

    planned: List[dict[str, Any]] = []
    video_duration = 0.0
    for item in subclipped_items:
        if video_duration >= required_video_duration:
            break
        source_seconds = max(0.0, item.end_time - item.start_time)
        output_seconds = min(
            max_clip_duration,
            source_seconds / normalized_clip_speed if normalized_clip_speed else 0.0,
        )
        if output_seconds <= 0:
            continue
        transition, slide_side = _pick_transition(video_transition_mode)
        planned.append(
            {
                "src": os.path.abspath(item.file_path),
                "startFromSeconds": float(item.start_time),
                "durationInFrames": _seconds_to_frames(output_seconds),
                "speed": float(normalized_clip_speed),
                "transition": transition or "none",
                "slideSide": slide_side,
            }
        )
        video_duration += output_seconds

    if not planned:
        raise RemotionRenderError("no valid video materials for Remotion timeline")

    if video_duration < required_video_duration:
        base = list(planned)
        index = 0
        while video_duration < required_video_duration:
            clone = dict(base[index % len(base)])
            planned.append(clone)
            video_duration += clone["durationInFrames"] / float(FPS)
            index += 1

    # Trim the last clip so the timeline does not overshoot by a full max clip.
    total_frames = sum(clip["durationInFrames"] for clip in planned)
    required_frames = _seconds_to_frames(required_video_duration)
    if total_frames > required_frames and planned:
        overflow = total_frames - required_frames
        last = planned[-1]
        last["durationInFrames"] = max(1, last["durationInFrames"] - overflow)

    return planned


def build_composition_props(
    *,
    clips: List[dict[str, Any]],
    params: VideoParams,
    audio_path: str = "",
    subtitle_path: str = "",
    bgm_path: str = "",
    visual_only: bool = False,
) -> dict[str, Any]:
    aspect = VideoAspect(params.video_aspect)
    width, height = aspect.to_resolution()
    duration_in_frames = sum(int(clip["durationInFrames"]) for clip in clips)
    font_path = ""
    if params.subtitle_enabled and not visual_only:
        font_name = params.font_name or "STHeitiMedium.ttc"
        font_path = os.path.abspath(os.path.join(utils.font_dir(), font_name))
        if not os.path.isfile(font_path):
            logger.warning(f"Remotion subtitle font not found: {font_path}")
            font_path = ""

    subtitle_cues = []
    if params.subtitle_enabled and not visual_only:
        subtitle_cues = parse_srt_cues(subtitle_path)

    return {
        "clips": clips,
        "width": width,
        "height": height,
        "fps": FPS,
        "durationInFrames": max(1, duration_in_frames),
        "visualOnly": bool(visual_only),
        "narrationSrc": os.path.abspath(audio_path) if audio_path and not visual_only else "",
        "voiceVolume": float(params.voice_volume or 1.0),
        "bgmSrc": os.path.abspath(bgm_path) if bgm_path and not visual_only else "",
        "bgmVolume": float(params.bgm_volume or 0.0),
        "transitionDurationInFrames": _seconds_to_frames(_TRANSITION_DURATION_SECONDS),
        "subtitles": {
            "enabled": bool(params.subtitle_enabled and not visual_only and subtitle_cues),
            "cues": subtitle_cues,
            "fontPath": font_path,
            "fontSize": int(params.font_size or 60),
            "color": params.text_fore_color or "#FFFFFF",
            "strokeColor": params.stroke_color or "#000000",
            "strokeWidth": float(params.stroke_width or 1.5),
            "position": params.subtitle_position or "bottom",
            "customPosition": float(params.custom_position or 70.0),
            "backgroundColor": _resolve_subtitle_background(params),
            "roundedBackground": bool(params.rounded_subtitle_background),
        },
    }


def write_props_file(props: dict[str, Any], props_path: str) -> str:
    os.makedirs(os.path.dirname(props_path) or ".", exist_ok=True)
    with open(props_path, "w", encoding="utf-8") as handle:
        json.dump(props, handle, ensure_ascii=False, indent=2)
    return props_path


def _remotion_concurrency(threads: Optional[int] = None) -> Optional[int]:
    configured = config.app.get("remotion_concurrency")
    if configured not in (None, ""):
        try:
            value = int(configured)
            return value if value > 0 else None
        except (TypeError, ValueError):
            logger.warning(f"invalid remotion_concurrency={configured!r}, ignoring")
    if threads is not None:
        try:
            value = int(threads)
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def render_video(
    *,
    output_file: str,
    props: dict[str, Any],
    props_path: str,
    threads: Optional[int] = None,
) -> str:
    """Run ``npx remotion render`` and return the output path."""
    ensure_ready()
    write_props_file(props, props_path)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    cmd = [
        "npx",
        "--no-install",
        "remotion",
        "render",
        ENTRY_FILE,
        COMPOSITION_ID,
        os.path.abspath(output_file),
        f"--props={os.path.abspath(props_path)}",
        "--overwrite",
    ]
    concurrency = _remotion_concurrency(threads)
    if concurrency is not None:
        cmd.append(f"--concurrency={concurrency}")

    logger.info(f"Remotion render: {' '.join(cmd)}")
    try:
        completed = subprocess.run(
            cmd,
            cwd=project_dir(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RemotionRenderError(f"failed to launch Remotion CLI: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RemotionRenderError(f"Remotion render failed: {detail}")

    if not os.path.isfile(output_file):
        raise RemotionRenderError(f"Remotion render produced no file: {output_file}")
    return output_file


def render_composition(
    *,
    task_id: str,
    index: int,
    video_paths: List[str],
    audio_file: str,
    subtitle_path: str,
    params: VideoParams,
    audio_duration: float,
    video_concat_mode: VideoConcatMode,
    video_transition_mode: VideoTransitionMode,
    output_file: str,
    visual_only: bool = False,
    bgm_path: str = "",
    clips: Optional[List[dict[str, Any]]] = None,
) -> tuple[str, List[dict[str, Any]]]:
    """
    Plan (or reuse) timeline clips and render one Remotion output.

    Returns ``(output_file, clips)`` so callers can reuse the same timeline for
    a visual-only pass and a final audio pass.
    """
    ensure_ready()
    if clips is None:
        clips = plan_timeline_clips(
            video_paths=video_paths,
            audio_duration=audio_duration,
            video_concat_mode=video_concat_mode,
            video_transition_mode=video_transition_mode,
            max_clip_duration=int(params.video_clip_duration or 5),
            clip_speed=float(params.video_clip_speed or 1.0),
        )

    props = build_composition_props(
        clips=clips,
        params=params,
        audio_path=audio_file,
        subtitle_path=subtitle_path,
        bgm_path=bgm_path,
        visual_only=visual_only,
    )
    suffix = "visual" if visual_only else "final"
    props_path = os.path.join(
        utils.task_dir(task_id),
        f"remotion-props-{index}-{suffix}.json",
    )
    render_video(
        output_file=output_file,
        props=props,
        props_path=props_path,
        threads=params.n_threads,
    )
    return output_file, clips


def probe_audio_duration(audio_file: str) -> float:
    clip = AudioFileClip(audio_file)
    try:
        return float(clip.duration)
    finally:
        clip.close()
