"""Standalone verification for the scene-timeline exact-duration
combine (Renderify Scene Templates, Phase 2). Run manually from
the repo root:  python3 test/scene_timeline_check.py

Generates ffmpeg fixtures, renders scenes [stock 3500ms, empty
2000ms, image 1500ms] through the exact path, and asserts the
combined output is frame-exact. Also covers the missing-file
failure, the last-scene audio adjustment, and that the legacy
combine still works without a timeline.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.schema import SceneTimelineItem
from app.services import video
from app.services.task import _adjust_scene_timeline_to_audio
from app.utils import utils


def ffmpeg(*args):
    result = subprocess.run(
        [utils.get_ffmpeg_binary(), "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fixture failed: {result.stderr[-500:]}")


def probe(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames,r_frame_rate,pix_fmt",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1", path,
        ],
        capture_output=True,
        text=True,
    )
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    values["audio_streams"] = len([l for l in audio.stdout.splitlines() if l.strip()])
    return values


def main():
    local_videos = utils.storage_dir("local_videos", create=True)
    clip = os.path.join(local_videos, "check-clip.mp4")
    still = os.path.join(local_videos, "check-still.png")
    work = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene-check")
    os.makedirs(work, exist_ok=True)
    narration = os.path.join(work, "narration.wav")
    short_narration = os.path.join(work, "narration-6.5s.wav")
    long_narration = os.path.join(work, "narration-9s.wav")

    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", clip)
    ffmpeg("-f", "lavfi", "-i", "color=c=0x336699:s=720x1280",
           "-frames:v", "1", still)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=3.5", narration)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=6.5", short_narration)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=9", long_narration)

    scenes = [
        SceneTimelineItem(scene_id="s1", duration_ms=3500, source="stock", file="check-clip.mp4"),
        SceneTimelineItem(scene_id="s2", duration_ms=2000, source="empty", background_color="#112233"),
        SceneTimelineItem(scene_id="s3", duration_ms=1500, source="stock", file="check-still.png"),
    ]

    combined = os.path.join(work, "combined.mp4")
    video.combine_scene_timeline(combined, scenes, narration, threads=2)
    info = probe(combined)
    assert info["nb_read_frames"] == "210", info
    assert info["r_frame_rate"] == "30/1", info
    assert info["pix_fmt"] == "yuv420p", info
    assert info["audio_streams"] == 0, info
    print(f"exact path: {info['nb_read_frames']} frames, {info['duration']}s, "
          f"{info['pix_fmt']}, no audio — OK")

    try:
        video.combine_scene_timeline(
            os.path.join(work, "bad.mp4"),
            [SceneTimelineItem(scene_id="gone1", duration_ms=1000,
                               source="stock", file="nope.mp4")],
            narration,
        )
        raise AssertionError("missing file did not raise")
    except RuntimeError as exc:
        assert "gone1" in str(exc), exc
        print(f"missing file: {exc} — OK")

    adjusted = _adjust_scene_timeline_to_audio(scenes, short_narration)
    assert adjusted[-1].duration_ms == 1000, adjusted[-1]
    assert adjusted[0].duration_ms == 3500
    print("last-scene adjustment: 1500ms -> 1000ms for 6.5s audio — OK")

    try:
        _adjust_scene_timeline_to_audio(scenes, long_narration)
        raise AssertionError("oversized delta did not raise")
    except ValueError as exc:
        assert "7000" in str(exc) and "9000" in str(exc), exc
        print(f"oversized delta: {exc} — OK")

    legacy = os.path.join(work, "legacy.mp4")
    video.combine_videos(
        combined_video_path=legacy,
        video_paths=[clip],
        audio_file=narration,
        threads=2,
    )
    info = probe(legacy)
    assert float(info["duration"]) >= 3.5, info
    print(f"legacy path: {info['duration']}s for 3.5s narration — OK")

    print("scene_timeline_check: PASS")


if __name__ == "__main__":
    main()
