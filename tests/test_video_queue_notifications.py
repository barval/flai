from contextlib import nullcontext
from unittest.mock import Mock, patch

import pytest

from app.queue import RedisRequestQueue


@pytest.mark.unit
def test_video_image_resize_notice_is_not_emitted_twice():
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    queue.app = Mock()
    base = Mock()
    base._ = Mock(side_effect=lambda message, **kwargs: message)
    multimodal = Mock()
    multimodal.generate_video_params_from_image.return_value = ({"prompt": "animate"}, None)
    queue.app.modules = {"base": base, "multimodal": multimodal, "video": Mock()}
    queue.app.config = {}
    queue.logger = Mock()
    queue._publish_stream_event = Mock()
    queue._is_task_cancelled = Mock(return_value=False)
    queue._build_error_response = Mock(return_value={"status": "error"})
    queue._log_gpu_state_before_op = Mock()
    queue._unload_llamacpp_models = Mock()
    queue._unload_video_pipeline = Mock()
    queue._wait_for_vram = Mock(return_value=True)
    queue._get_vram_needed = Mock(return_value=1)
    queue._check_vram_ready = Mock(return_value=True)
    queue._wait_for_vram_full = Mock(return_value=True)
    queue._plan_cpu_video = Mock(return_value=({"prompt": "animate"}, "stop after resize"))
    queue._preload_multimodal_sync = Mock()

    task = {"id": "video-resize", "session_id": "s1", "user_id": "u1"}
    resized_meta = {
        "resized": True,
        "original_size": (1280, 576),
        "new_size": (768, 345),
    }
    with (
        patch("modules.video.resize_video_source_image", return_value=("resized-image", resized_meta)),
        patch("app.queue.save_message", return_value=77) as save_message,
        patch("app.queue.force_locale", return_value=nullcontext()),
    ):
        result = queue._process_video_gen_task_from_image("animate", "image-base64", "s1", "u1", "en", task=task)

    assert result == {"status": "error"}
    save_message.assert_called_once()
    assert not any(call.args[1] == "notice" for call in queue._publish_stream_event.call_args_list)
