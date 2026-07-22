"""
Optional Remotion (https://www.remotion.dev/) full composition renderer.

When ``video_renderer = "remotion"`` in config.toml, MoneyPrinterTurbo scaffolds
a standalone Remotion project per output video under
``storage/tasks/<task_id>/remotion-<title-slug>-<task8>-<index>/``, stages media
into that project's ``public/``, and renders from it so the composition stays
editable in Studio.

Setup:
  1. Install Node.js 18+
  2. ``cd remotion && npm install``
  3. Set ``video_renderer = "remotion"`` (or choose Remotion in the WebUI)

Remotion license: companies may need a paid license —
https://www.remotion.dev/docs/license
"""

from __future__ import annotations

import copy
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


class RemotionSeedError(ValueError):
    """Raised when a Remotion style seed path is invalid."""


@dataclass(frozen=True)
class RemotionSeedProject:
    path: str
    task_id: str
    label: str
    mtime: float


@dataclass(frozen=True)
class RemotionSeedPackage:
    path: str
    subtitle_style: dict[str, Any]
    transition_duration_in_frames: Optional[int]
    dominant_transition: Optional[str]
    bgm_path: str
    bgm_volume: Optional[float]
    voice_volume: Optional[float]
    font_path: str


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


def template_dir() -> str:
    """Shared Remotion template used to scaffold per-task editable projects."""
    return os.path.join(config.root_dir, "remotion")


def project_dir() -> str:
    """Backward-compatible alias for the shared template directory."""
    return template_dir()


def standalone_project_dir(
    task_id: str,
    index: int,
    *,
    title: Optional[str] = None,
) -> str:
    """Path to the per-video Remotion project folder (unique per task + index)."""
    return os.path.join(
        utils.task_dir(task_id),
        project_folder_name(task_id, index, title=title),
    )


def project_folder_name(
    task_id: str,
    index: int,
    *,
    title: Optional[str] = None,
) -> str:
    """
    Unique Remotion project directory name shown in Studio.

    Uses a slug of the task title (video subject) plus a short task id so
    projects are readable and never collide across tasks.
    """
    short_id = re.sub(r"[^a-zA-Z0-9]+", "", str(task_id or ""))[:8].lower() or "task"
    slug = _slugify_title(title)
    if slug:
        return f"remotion-{slug}-{short_id}-{index}"
    return f"remotion-{short_id}-{index}"


def npm_package_name(
    task_id: str,
    index: int,
    *,
    title: Optional[str] = None,
) -> str:
    """Valid npm package name for the standalone Remotion project."""
    folder = project_folder_name(task_id, index, title=title)
    # npm names: lowercase, URL-safe; keep under a reasonable length.
    name = folder.lower().replace("_", "-")
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")
    return (name or f"mpt-remotion-{index}")[:214]


def _slugify_title(title: Optional[str], *, max_len: int = 40) -> str:
    raw = str(title or "").strip().lower()
    if not raw:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if not slug:
        return ""
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


def is_requested() -> bool:
    value = str(config.app.get("video_renderer", "moviepy") or "moviepy").strip().lower()
    return value == "remotion"


def _node_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npm") is not None


def _package_installed() -> bool:
    return os.path.isdir(os.path.join(template_dir(), "node_modules", "remotion"))


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


_SUBTITLE_STYLE_KEYS = (
    "fontSize",
    "color",
    "strokeColor",
    "strokeWidth",
    "position",
    "customPosition",
    "backgroundColor",
    "roundedBackground",
)

_TRANSITION_NAME_TO_MODE = {
    "FadeIn": VideoTransitionMode.fade_in,
    "FadeOut": VideoTransitionMode.fade_out,
    "SlideIn": VideoTransitionMode.slide_in,
    "SlideOut": VideoTransitionMode.slide_out,
    "ZoomIn": VideoTransitionMode.zoom_in,
    "ZoomOut": VideoTransitionMode.zoom_out,
}


def validate_seed_project(path: str) -> str:
    """Resolve and validate a Remotion seed project directory."""
    raw = (path or "").strip()
    if not raw:
        raise RemotionSeedError("Remotion seed path is empty.")
    expanded = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(expanded):
        raise RemotionSeedError(f"Remotion seed project not found: {expanded}")
    src_dir = os.path.join(expanded, "src")
    if not os.path.isdir(src_dir):
        raise RemotionSeedError(
            f"Remotion seed is missing src/: {expanded}"
        )
    return expanded


def list_seed_projects(limit: int = 30) -> List[RemotionSeedProject]:
    """Discover past task remotion-* projects, newest first."""
    tasks_root = utils.storage_dir("tasks")
    if not os.path.isdir(tasks_root):
        return []

    found: List[RemotionSeedProject] = []
    try:
        task_names = os.listdir(tasks_root)
    except OSError:
        return []

    for task_id in task_names:
        task_path = os.path.join(tasks_root, task_id)
        if not os.path.isdir(task_path):
            continue
        try:
            entries = os.listdir(task_path)
        except OSError:
            continue
        for name in entries:
            if not name.startswith("remotion-"):
                continue
            project_path = os.path.join(task_path, name)
            if not os.path.isdir(os.path.join(project_path, "src")):
                continue
            try:
                mtime = os.path.getmtime(project_path)
            except OSError:
                mtime = 0.0
            short_task = task_id if len(task_id) <= 12 else f"{task_id[:8]}…"
            found.append(
                RemotionSeedProject(
                    path=os.path.abspath(project_path),
                    task_id=task_id,
                    label=f"{short_task} / {name}",
                    mtime=mtime,
                )
            )

    found.sort(key=lambda item: item.mtime, reverse=True)
    if limit > 0:
        return found[:limit]
    return found


def _resolve_seed_asset_path(seed_path: str, relative_or_abs: str) -> str:
    if not relative_or_abs:
        return ""
    candidate = relative_or_abs
    if not os.path.isabs(candidate):
        public_candidate = os.path.join(seed_path, "public", candidate)
        if os.path.isfile(public_candidate):
            return os.path.abspath(public_candidate)
        nested = os.path.join(seed_path, candidate)
        if os.path.isfile(nested):
            return os.path.abspath(nested)
        return ""
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)
    return ""


def dominant_transition_from_clips(clips: List[dict[str, Any]]) -> Optional[str]:
    counts: dict[str, int] = {}
    for clip in clips or []:
        name = str(clip.get("transition") or "").strip()
        if not name or name.lower() == "none":
            continue
        if name not in _CONCRETE_TRANSITIONS:
            continue
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def load_seed_package(path: str) -> RemotionSeedPackage:
    """Load style, motion, and BGM metadata from a seed project's input-props."""
    seed_path = validate_seed_project(path)
    props_path = os.path.join(seed_path, "input-props.json")
    props: dict[str, Any] = {}
    if os.path.isfile(props_path):
        try:
            with open(props_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                props = loaded
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"failed to read seed input-props.json: {exc}")

    raw_subs = props.get("subtitles") if isinstance(props.get("subtitles"), dict) else {}
    subtitle_style = {
        key: raw_subs[key] for key in _SUBTITLE_STYLE_KEYS if key in raw_subs
    }

    transition_duration = props.get("transitionDurationInFrames")
    try:
        transition_duration_in_frames = (
            int(transition_duration) if transition_duration is not None else None
        )
    except (TypeError, ValueError):
        transition_duration_in_frames = None

    clips = props.get("clips") if isinstance(props.get("clips"), list) else []
    dominant = dominant_transition_from_clips(clips)

    bgm_src = str(props.get("bgmSrc") or "")
    bgm_path = _resolve_seed_asset_path(seed_path, bgm_src)
    font_src = str(raw_subs.get("fontPath") or "")
    font_path = _resolve_seed_asset_path(seed_path, font_src)

    bgm_volume = props.get("bgmVolume")
    try:
        bgm_volume_value = float(bgm_volume) if bgm_volume is not None else None
    except (TypeError, ValueError):
        bgm_volume_value = None

    voice_volume = props.get("voiceVolume")
    try:
        voice_volume_value = float(voice_volume) if voice_volume is not None else None
    except (TypeError, ValueError):
        voice_volume_value = None

    return RemotionSeedPackage(
        path=seed_path,
        subtitle_style=subtitle_style,
        transition_duration_in_frames=transition_duration_in_frames,
        dominant_transition=dominant,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume_value,
        voice_volume=voice_volume_value,
        font_path=font_path,
    )


def resolve_seed_bgm_file(seed_path: str) -> str:
    """Return absolute BGM path from a seed project, or empty string."""
    try:
        package = load_seed_package(seed_path)
    except RemotionSeedError:
        return ""
    return package.bgm_path if package.bgm_path and os.path.isfile(package.bgm_path) else ""


def resolve_transition_mode_with_seed(
    transition_mode: Optional[VideoTransitionMode],
    package: Optional[RemotionSeedPackage],
) -> Optional[VideoTransitionMode]:
    """Prefer an explicit concrete mode; otherwise use the seed's dominant transition."""
    value = getattr(transition_mode, "value", transition_mode)
    if value in _CONCRETE_TRANSITIONS:
        return transition_mode if isinstance(transition_mode, VideoTransitionMode) else VideoTransitionMode(value)
    if package and package.dominant_transition:
        mapped = _TRANSITION_NAME_TO_MODE.get(package.dominant_transition)
        if mapped is not None:
            return mapped
    return transition_mode


def merge_seed_defaults_into_params(params: VideoParams) -> VideoParams:
    """
    Prefill unset/weak style fields from a Remotion seed.

    Used by CLI (and reusable by WebUI). Explicit concrete transition modes and
    custom BGM files are left alone.
    """
    seed = str(getattr(params, "remotion_seed", None) or "").strip()
    if not seed:
        return params
    package = load_seed_package(seed)

    mode_value = getattr(params.video_transition_mode, "value", params.video_transition_mode)
    if mode_value not in _CONCRETE_TRANSITIONS and package.dominant_transition:
        mapped = _TRANSITION_NAME_TO_MODE.get(package.dominant_transition)
        if mapped is not None:
            params.video_transition_mode = mapped

    style = package.subtitle_style
    if "fontSize" in style and params.font_size == 60:
        try:
            params.font_size = int(style["fontSize"])
        except (TypeError, ValueError):
            pass
    if "color" in style and (not params.text_fore_color or params.text_fore_color == "#FFFFFF"):
        params.text_fore_color = str(style["color"])
    if "strokeColor" in style and (
        not params.stroke_color or params.stroke_color == "#000000"
    ):
        params.stroke_color = str(style["strokeColor"])
    if "strokeWidth" in style and params.stroke_width == 1.5:
        try:
            params.stroke_width = float(style["strokeWidth"])
        except (TypeError, ValueError):
            pass
    if "position" in style and (
        not params.subtitle_position
        or params.subtitle_position == config.ui.get("subtitle_position", "bottom")
    ):
        params.subtitle_position = str(style["position"])
    if "customPosition" in style and params.custom_position == float(
        config.ui.get("custom_position", 70.0)
    ):
        try:
            params.custom_position = float(style["customPosition"])
        except (TypeError, ValueError):
            pass
    if "backgroundColor" in style:
        bg = style["backgroundColor"]
        if params.text_background_color in (False, None, ""):
            params.text_background_color = bg if bg else False
    if "roundedBackground" in style and not params.rounded_subtitle_background:
        params.rounded_subtitle_background = bool(style["roundedBackground"])

    if package.bgm_volume is not None and params.bgm_volume == 0.2:
        params.bgm_volume = package.bgm_volume
    if package.voice_volume is not None and params.voice_volume == 1.0:
        params.voice_volume = package.voice_volume

    if package.bgm_path and os.path.isfile(package.bgm_path):
        bgm_type = str(params.bgm_type or "").strip().lower()
        if bgm_type in ("", "random") and not (params.bgm_file or "").strip():
            params.bgm_type = "custom"
            params.bgm_file = package.bgm_path

    return params


def apply_seed_package_to_props(
    props: dict[str, Any],
    package: RemotionSeedPackage,
) -> dict[str, Any]:
    """Overlay motion duration and brand font from seed; never touch clips/narration/cues."""
    updated = copy.deepcopy(props)
    if package.transition_duration_in_frames is not None:
        updated["transitionDurationInFrames"] = max(
            1, int(package.transition_duration_in_frames)
        )
    if package.bgm_volume is not None and updated.get("bgmSrc"):
        updated["bgmVolume"] = float(package.bgm_volume)
    subtitles = dict(updated.get("subtitles") or {})
    if package.font_path and os.path.isfile(package.font_path):
        subtitles["fontPath"] = package.font_path
    updated["subtitles"] = subtitles
    return updated


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


def _link_or_copy(src: str, dst: str) -> None:
    """Prefer hardlink, then symlink, then copy so large clips are not duplicated."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    if os.path.lexists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
        return
    except OSError:
        pass
    try:
        os.symlink(src, dst)
        return
    except OSError:
        pass
    shutil.copy2(src, dst)


_TEMPLATE_COPY_NAMES = (
    "package.json",
    "remotion.config.ts",
    "tsconfig.json",
    # Placeholder until staging writes the real timeline; Root.tsx imports this
    # as Studio defaultProps so preview works without --props.
    "input-props.json",
)


def _write_standalone_readme(project_path: str) -> None:
    readme_path = os.path.join(project_path, "README.md")
    content = """# MoneyPrinterTurbo Remotion project

Standalone Remotion composition for this generated video.

## Preview / edit

```bash
npx remotion studio
```

`input-props.json` is loaded as composition default props (clips, audio, subtitles).

## Re-render

```bash
npx remotion render src/index.ts MoneyPrinterVideo out.mp4 --props=input-props.json --overwrite
```

`node_modules` is symlinked to the shared repo `remotion/node_modules`.
If Studio fails to start, run `npm install` in the repository `remotion/` folder first.

Remotion license: https://www.remotion.dev/docs/license
"""
    with open(readme_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _write_project_package_json(
    project_path: str,
    *,
    task_id: str,
    index: int,
    title: Optional[str] = None,
) -> None:
    """Rewrite package.json name so Studio / npm identity matches this task."""
    package_path = os.path.join(project_path, "package.json")
    if not os.path.isfile(package_path):
        return
    try:
        with open(package_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    data["name"] = npm_package_name(task_id, index, title=title)
    subject = str(title or "").strip()
    if subject:
        data["description"] = f"MoneyPrinterTurbo Remotion project: {subject}"
    with open(package_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def scaffold_project(
    task_id: str,
    index: int,
    *,
    seed_path: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """
    Create a uniquely named Remotion project under the task directory.

    Folder form: ``remotion-<title-slug>-<task8>-<index>/``. When ``seed_path``
    is set, composition ``src/`` is copied from that past project. Packaging
    still comes from the shared template. Media is staged later into ``public/``.
    """
    ensure_ready()
    template = template_dir()
    project_path = standalone_project_dir(task_id, index, title=title)
    os.makedirs(project_path, exist_ok=True)

    src_source_root = template
    if seed_path:
        validated_seed = validate_seed_project(seed_path)
        src_source_root = validated_seed
        logger.info(f"scaffolding Remotion project from seed: {validated_seed}")

    src_src = os.path.join(src_source_root, "src")
    src_dst = os.path.join(project_path, "src")
    if os.path.isdir(src_dst):
        shutil.rmtree(src_dst)
    shutil.copytree(src_src, src_dst)

    for name in _TEMPLATE_COPY_NAMES:
        src_file = os.path.join(template, name)
        if os.path.isfile(src_file):
            shutil.copy2(src_file, os.path.join(project_path, name))

    _write_project_package_json(
        project_path, task_id=task_id, index=index, title=title
    )

    public_path = os.path.join(project_path, "public")
    os.makedirs(public_path, exist_ok=True)

    shared_modules = os.path.join(template, "node_modules")
    if not os.path.isdir(shared_modules):
        raise RemotionNotReadyError(
            "Remotion template node_modules missing. Run `cd remotion && npm install`."
        )
    modules_link = os.path.join(project_path, "node_modules")
    if os.path.lexists(modules_link):
        if os.path.islink(modules_link) or os.path.isfile(modules_link):
            os.remove(modules_link)
        else:
            shutil.rmtree(modules_link)
    os.symlink(shared_modules, modules_link)

    # Copy package-lock when present so the standalone folder looks complete.
    lock_src = os.path.join(template, "package-lock.json")
    if os.path.isfile(lock_src):
        shutil.copy2(lock_src, os.path.join(project_path, "package-lock.json"))

    _write_standalone_readme(project_path)
    logger.info(f"scaffolded Remotion project: {project_path}")
    return project_path


def _stage_one_asset(
    source_path: str,
    staging_dir: str,
    cache: dict[str, str],
    label: str,
) -> str:
    """
    Stage one filesystem asset into a project's ``public/`` folder.

    Returns a public-relative path (e.g. ``clip-1.mp4``) for ``staticFile()``.
    """
    if not source_path:
        return ""
    if source_path.startswith(("http://", "https://", "data:")):
        return source_path

    # Already a staged public-relative basename from an earlier pass in this project.
    candidate = os.path.join(staging_dir, source_path.replace("\\", "/").lstrip("/"))
    if not os.path.isabs(source_path) and os.path.isfile(candidate):
        return source_path.replace("\\", "/")

    abs_source = os.path.abspath(source_path)
    if abs_source in cache:
        return cache[abs_source]
    if not os.path.isfile(abs_source):
        raise RemotionRenderError(f"Remotion media missing: {abs_source}")

    ext = os.path.splitext(abs_source)[1] or ""
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._") or "asset"
    staged_name = f"{safe_label}-{len(cache) + 1}{ext}"
    staged_abs = os.path.join(staging_dir, staged_name)
    _link_or_copy(abs_source, staged_abs)
    cache[abs_source] = staged_name
    return staged_name


def stage_props_into_project(
    props: dict[str, Any],
    *,
    project_path: str,
) -> dict[str, Any]:
    """
    Rewrite absolute media paths into the standalone project's ``public/``.

    Paths become simple public-relative names so Remotion Studio works without
    repo-specific prefixes.
    """
    staged = copy.deepcopy(props)
    staging_dir = os.path.join(project_path, "public")
    os.makedirs(staging_dir, exist_ok=True)
    cache: dict[str, str] = {}

    rewritten_clips = []
    for clip_index, clip in enumerate(staged.get("clips") or []):
        clip = dict(clip)
        clip["src"] = _stage_one_asset(
            clip.get("src", ""),
            staging_dir,
            cache,
            f"clip-{clip_index}",
        )
        rewritten_clips.append(clip)
    staged["clips"] = rewritten_clips

    staged["narrationSrc"] = _stage_one_asset(
        staged.get("narrationSrc", ""),
        staging_dir,
        cache,
        "narration",
    )
    staged["bgmSrc"] = _stage_one_asset(
        staged.get("bgmSrc", ""),
        staging_dir,
        cache,
        "bgm",
    )

    subtitles = dict(staged.get("subtitles") or {})
    subtitles["fontPath"] = _stage_one_asset(
        subtitles.get("fontPath", ""),
        staging_dir,
        cache,
        "font",
    )
    staged["subtitles"] = subtitles
    return staged


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
    working_directory: str,
    threads: Optional[int] = None,
) -> str:
    """Run ``npx remotion render`` from a Remotion project directory."""
    ensure_ready()
    write_props_file(props, props_path)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    # Prefer a project-relative props path when the file lives inside cwd so
    # Studio and CLI share the same input-props.json convention.
    props_arg = os.path.abspath(props_path)
    try:
        props_arg = os.path.relpath(props_path, working_directory)
    except ValueError:
        pass

    cmd = [
        "npx",
        "--no-install",
        "remotion",
        "render",
        ENTRY_FILE,
        COMPOSITION_ID,
        os.path.abspath(output_file),
        f"--props={props_arg}",
        "--overwrite",
    ]
    concurrency = _remotion_concurrency(threads)
    if concurrency is not None:
        cmd.append(f"--concurrency={concurrency}")

    logger.info(f"Remotion render ({working_directory}): {' '.join(cmd)}")
    try:
        completed = subprocess.run(
            cmd,
            cwd=working_directory,
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
    project_path: Optional[str] = None,
) -> tuple[str, List[dict[str, Any]], str]:
    """
    Scaffold (or reuse) a standalone Remotion project, stage media, and render.

    Returns ``(output_file, clips, project_path)``. The project is kept on disk
    for editing in Remotion Studio.
    """
    ensure_ready()
    project_title = str(getattr(params, "video_subject", None) or "").strip() or None
    if project_path is None:
        project_path = scaffold_project(task_id, index, title=project_title)
    elif not os.path.isdir(project_path):
        project_path = scaffold_project(task_id, index, title=project_title)

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
    staged_props = stage_props_into_project(props, project_path=project_path)

    # Canonical props for Studio / re-render. Visual-only passes write a sibling
    # file so the final input-props.json is not overwritten until the final mix.
    if visual_only:
        props_path = os.path.join(project_path, "input-props.visual.json")
    else:
        props_path = os.path.join(project_path, "input-props.json")

    # Debug copy at task root (optional convenience).
    suffix = "visual" if visual_only else "final"
    debug_props_path = os.path.join(
        utils.task_dir(task_id),
        f"remotion-props-{index}-{suffix}.json",
    )
    write_props_file(staged_props, debug_props_path)

    render_video(
        output_file=output_file,
        props=staged_props,
        props_path=props_path,
        working_directory=project_path,
        threads=params.n_threads,
    )
    return output_file, clips, project_path


def list_remotion_projects(limit: int = 30) -> List[RemotionSeedProject]:
    """Alias for discovering past remotion-* projects."""
    return list_seed_projects(limit=limit)


def parse_project_index(folder_name: str) -> int:
    match = re.search(r"-(\d+)$", folder_name or "")
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return 1
    return 1


@dataclass(frozen=True)
class RemotionProjectContext:
    task_id: str
    project_path: str
    index: int
    script: str
    params: dict[str, Any]
    props: dict[str, Any]
    video_terms: Any


def resolve_project_context(project_path: str) -> RemotionProjectContext:
    """Load an existing remotion-* project and its parent task script.json."""
    validated = validate_seed_project(project_path)
    task_id = os.path.basename(os.path.dirname(validated))
    if not task_id or task_id.startswith("remotion"):
        raise RemotionSeedError(
            f"Remotion project is not under storage/tasks/<task_id>/: {validated}"
        )
    index = parse_project_index(os.path.basename(validated))

    script = ""
    params: dict[str, Any] = {}
    video_terms: Any = ""
    script_path = os.path.join(utils.task_dir(task_id), "script.json")
    if os.path.isfile(script_path):
        try:
            with open(script_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                script = str(data.get("script") or "")
                raw_params = data.get("params") or {}
                if isinstance(raw_params, dict):
                    params = raw_params
                video_terms = data.get("search_terms") or ""
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"failed to read task script.json: {exc}")

    props: dict[str, Any] = {}
    props_path = os.path.join(validated, "input-props.json")
    if os.path.isfile(props_path):
        try:
            with open(props_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                props = loaded
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"failed to read project input-props.json: {exc}")

    return RemotionProjectContext(
        task_id=task_id,
        project_path=validated,
        index=index,
        script=script,
        params=params,
        props=props,
        video_terms=video_terms,
    )


def render_existing_project(
    *,
    task_id: str,
    index: int,
    project_path: str,
    props: dict[str, Any],
    output_file: str,
    threads: Optional[int] = None,
) -> str:
    """Re-render an existing Remotion project without scaffolding."""
    ensure_ready()
    if not os.path.isdir(project_path):
        raise RemotionRenderError(f"Remotion project missing: {project_path}")
    staged_props = stage_props_into_project(props, project_path=project_path)
    props_path = os.path.join(project_path, "input-props.json")
    debug_props_path = os.path.join(
        utils.task_dir(task_id),
        f"remotion-props-{index}-final.json",
    )
    write_props_file(staged_props, debug_props_path)
    return render_video(
        output_file=output_file,
        props=staged_props,
        props_path=props_path,
        working_directory=project_path,
        threads=threads,
    )


def props_with_updated_audio(
    props: dict[str, Any],
    *,
    audio_path: str,
    subtitle_path: str,
    params: VideoParams,
    clips: Optional[List[dict[str, Any]]] = None,
    bgm_path: str = "",
    audio_duration: Optional[float] = None,
) -> dict[str, Any]:
    """Update narration/subtitles/BGM on existing props; optionally replace clips."""
    updated = copy.deepcopy(props)
    if clips is not None:
        updated["clips"] = copy.deepcopy(clips)
    updated["narrationSrc"] = os.path.abspath(audio_path) if audio_path else ""
    updated["voiceVolume"] = float(params.voice_volume or 1.0)
    if bgm_path:
        updated["bgmSrc"] = os.path.abspath(bgm_path)
        updated["bgmVolume"] = float(params.bgm_volume or 0.0)
    elif bgm_path == "" and params.bgm_type == "none":
        updated["bgmSrc"] = ""
        updated["bgmVolume"] = 0.0

    subtitle_cues = []
    font_path = ""
    if params.subtitle_enabled:
        subtitle_cues = parse_srt_cues(subtitle_path)
        font_name = params.font_name or "STHeitiMedium.ttc"
        font_path = os.path.abspath(os.path.join(utils.font_dir(), font_name))
        if not os.path.isfile(font_path):
            # Keep prior staged font relative path if present.
            prior = (updated.get("subtitles") or {}).get("fontPath") or ""
            font_path = prior if prior else ""

    updated["subtitles"] = {
        "enabled": bool(params.subtitle_enabled and subtitle_cues),
        "cues": subtitle_cues,
        "fontPath": font_path or (updated.get("subtitles") or {}).get("fontPath", ""),
        "fontSize": int(params.font_size or 60),
        "color": params.text_fore_color or "#FFFFFF",
        "strokeColor": params.stroke_color or "#000000",
        "strokeWidth": float(params.stroke_width or 1.5),
        "position": params.subtitle_position or "bottom",
        "customPosition": float(params.custom_position or 70.0),
        "backgroundColor": _resolve_subtitle_background(params),
        "roundedBackground": bool(params.rounded_subtitle_background),
    }

    clip_list = updated.get("clips") or []
    if clip_list:
        updated["durationInFrames"] = max(
            1, sum(int(clip.get("durationInFrames") or 0) for clip in clip_list)
        )
    elif audio_duration is not None:
        updated["durationInFrames"] = _seconds_to_frames(float(audio_duration))
    updated["visualOnly"] = False
    return updated


def probe_audio_duration(audio_file: str) -> float:
    clip = AudioFileClip(audio_file)
    try:
        return float(clip.duration)
    finally:
        clip.close()
