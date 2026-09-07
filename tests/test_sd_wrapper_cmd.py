# tests/test_sd_wrapper_cmd.py
"""Unit tests for sd_wrapper Wan video command building."""

import sys

import pytest

sys.path.insert(0, "services/sd_cpp")


@pytest.mark.unit
def test_build_wan_video_cmd_t2v(monkeypatch, tmp_path):
    monkeypatch.setenv("SD_LOG_DIR", str(tmp_path))
    from sd_wrapper import SD_CLI, WAN_DIFFUSION_MODEL, WAN_T5, WAN_VAE, build_wan_video_cmd

    monkeypatch.setattr("sd_wrapper._check_cuda", lambda: True)
    cmd = build_wan_video_cmd(
        prompt="a cat",
        negative_prompt="blurry",
        width=640,
        height=384,
        num_frames=81,
        frame_rate=24,
        seed=5,
    )
    assert cmd[0] == SD_CLI
    assert "-M" in cmd and cmd[cmd.index("-M") + 1] == "vid_gen"
    assert "--diffusion-model" in cmd
    assert WAN_DIFFUSION_MODEL in cmd
    assert WAN_VAE in cmd
    assert WAN_T5 in cmd
    assert "--cfg-scale" in cmd and cmd[cmd.index("--cfg-scale") + 1] == "1.0"
    assert "--steps" in cmd and cmd[cmd.index("--steps") + 1] == "4"
    assert "--video-frames" in cmd and cmd[cmd.index("--video-frames") + 1] == "81"
    assert "--backend" in cmd and cmd[cmd.index("--backend") + 1] == "te=cpu"
    assert "--init-img" not in cmd


@pytest.mark.unit
def test_build_wan_video_cmd_i2v(monkeypatch, tmp_path):
    monkeypatch.setenv("SD_LOG_DIR", str(tmp_path))
    from sd_wrapper import build_wan_video_cmd

    monkeypatch.setattr("sd_wrapper._check_cuda", lambda: True)
    cmd = build_wan_video_cmd(
        prompt="animate this",
        negative_prompt="",
        width=640,
        height=384,
        num_frames=81,
        frame_rate=24,
        seed=5,
        init_img_path="/tmp/src.png",
    )
    assert "--init-img" in cmd and cmd[cmd.index("--init-img") + 1] == "/tmp/src.png"
