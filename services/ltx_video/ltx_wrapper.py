#!/usr/bin/env python3
"""
HTTP wrapper around LTX-Video inference.
Runs inside the ltxvideo container where PyTorch, CUDA, and models are available.

Endpoints:
  GET  /health                  — healthcheck
  POST /v1/video/generations    — text-to-video or image+text-to-video
"""

import base64
import json
import logging
import os
import sys
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path

import torch
from flask import Flask, jsonify, request
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="[ltx-wrapper] %(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("ltx-wrapper")

app = Flask(__name__)

# ── Configuration from environment ──
MODELS_DIR = os.environ.get("LTX_MODELS_DIR", "/app/models")
PIPELINE_CONFIG = os.environ.get("LTX_PIPELINE_CONFIG", "ltxv-2b-0.9.8-distilled.yaml")
DEFAULT_MODEL = os.environ.get("LTX_DEFAULT_MODEL", "ltxv-2b-0.9.8-distilled.safetensors")
DEFAULT_UPSCALER = os.environ.get("LTX_DEFAULT_UPSCALER", "ltxv-spatial-upscaler-0.9.8.safetensors")
TEXT_ENCODER_PATH = os.environ.get("LTX_TEXT_ENCODER_PATH", "PixArt-alpha/PixArt-XL-2-1024-MS")
HF_HOME = os.environ.get("HF_HOME", "/app/models/huggingface")
DEVICE = os.environ.get("LTX_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

os.environ["HF_HOME"] = HF_HOME
os.environ["HF_HUB_CACHE"] = os.path.join(HF_HOME, "hub")

# ── Global pipeline state ──
_pipeline = None
_pipeline_config_dict = None
_model_path = None
_upscaler_path = None
_pipeline_lock = threading.Lock()


def load_pipeline_config(config_name: str) -> dict:
    """Load YAML pipeline config, searching multiple locations."""
    search_paths = [
        Path(MODELS_DIR) / config_name,
        Path(config_name),
        Path(__file__).parent / config_name,
    ]
    for sp in search_paths:
        if sp.exists():
            logger.info(f"Loading pipeline config from {sp}")
            with open(sp) as f:
                import yaml

                return yaml.safe_load(f)
    raise FileNotFoundError(f"Pipeline config '{config_name}' not found in any of: {search_paths}")


def resolve_model_path(name: str) -> str:
    """Resolve model file path, searching MODELS_DIR first, then HF cache."""
    candidate = Path(MODELS_DIR) / name
    if candidate.exists():
        return str(candidate)
    # Fallback — download from HuggingFace
    logger.info(f"Model {name} not found locally, downloading from HuggingFace...")
    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        repo_id="Lightricks/LTX-Video",
        filename=name,
        repo_type="model",
    )


def ensure_pipeline():
    """Lazy-init the LTX-Video pipeline."""
    global _pipeline, _pipeline_config_dict, _model_path, _upscaler_path

    if _pipeline is not None:
        return _pipeline

    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline

        logger.info(f"Initializing LTX-Video pipeline on device: {DEVICE}")
        logger.info(f"Models directory: {MODELS_DIR}")

        start = time.time()

        config = load_pipeline_config(PIPELINE_CONFIG)
        _pipeline_config_dict = config

        model_name = config.get("checkpoint_path", DEFAULT_MODEL)
        _model_path = resolve_model_path(model_name)

        spatial_upscaler = config.get("spatial_upscaler_model_path", DEFAULT_UPSCALER)
        if spatial_upscaler:
            try:
                _upscaler_path = resolve_model_path(spatial_upscaler)
            except Exception:
                logger.warning(f"Spatial upscaler {spatial_upscaler} not found, continuing without")
                _upscaler_path = None
        else:
            _upscaler_path = None

        precision = config.get("precision", "bfloat16")
        sampler = config.get("sampler", "from_checkpoint")
        stg_mode = config.get("stg_mode", "attention_values")

        from ltx_video.models.autoencoders.causal_video_autoencoder import (
            CausalVideoAutoencoder,
        )
        from ltx_video.models.autoencoders.latent_upsampler import LatentUpsampler
        from ltx_video.models.transformers.symmetric_patchifier import SymmetricPatchifier
        from ltx_video.models.transformers.transformer3d import Transformer3DModel
        from ltx_video.pipelines.pipeline_ltx_video import (
            LTXMultiScalePipeline,
            LTXVideoPipeline,
        )
        from ltx_video.schedulers.rf import RectifiedFlowScheduler
        from safetensors import safe_open
        from transformers import T5EncoderModel, T5Tokenizer

        logger.info(f"Loading checkpoint: {_model_path}")

        with safe_open(_model_path, framework="pt") as f:
            metadata = f.metadata()
            config_str = metadata.get("config")
            allowed_inference_steps = json.loads(config_str).get("allowed_inference_steps", None)

        vae = CausalVideoAutoencoder.from_pretrained(_model_path)
        transformer = Transformer3DModel.from_pretrained(_model_path)
        if precision == "bfloat16":
            transformer = transformer.to(torch.bfloat16)

        scheduler = RectifiedFlowScheduler.from_pretrained(_model_path)
        text_encoder = T5EncoderModel.from_pretrained(TEXT_ENCODER_PATH, subfolder="text_encoder")
        patchifier = SymmetricPatchifier(patch_size=1)
        tokenizer = T5Tokenizer.from_pretrained(TEXT_ENCODER_PATH, subfolder="tokenizer")

        if precision == "bfloat16":
            vae = vae.to(torch.bfloat16)
            text_encoder = text_encoder.to(torch.bfloat16)

        # Keep text_encoder on CPU (~8.9 GiB in bf16, T5-XXL) to save VRAM.
        # VAE (~2 GiB) and transformer (~4 GiB) go to GPU.
        # T5 encoder is used once per generation (~1-2 sec on CPU),
        # negligible compared to 30-60 sec total generation time.
        # Monkey-patch .to() so pipeline.__call__ cannot move it to GPU.
        orig_te_to = text_encoder.to

        def _te_to(*a, **kw):
            for arg in a:
                if isinstance(arg, (torch.device, str)) and torch.device(arg).type == "cuda":
                    return orig_te_to(device="cpu")
            dev = kw.get("device")
            if isinstance(dev, (torch.device, str)) and torch.device(dev).type == "cuda":
                kw["device"] = "cpu"
            return orig_te_to(*a, **kw)

        text_encoder.to = _te_to

        # Monkey-patch forward so CUDA inputs are auto-moved to CPU.
        # The pipeline's encode_prompt moves attention_mask to CUDA for latents,
        # but text_encoder lives on CPU.  Catch & fix at the call site.
        _orig_te_forward = text_encoder.forward

        def _te_forward(*a, **kw):
            new_a = tuple(
                arg.to("cpu") if isinstance(arg, torch.Tensor) and arg.device.type == "cuda" else arg for arg in a
            )
            new_kw = {
                k: v.to("cpu") if isinstance(v, torch.Tensor) and v.device.type == "cuda" else v for k, v in kw.items()
            }
            return _orig_te_forward(*new_a, **new_kw)

        text_encoder.forward = _te_forward

        transformer = transformer.to(DEVICE)
        vae = vae.to(DEVICE)
        logger.info("Transformer and VAE on GPU; text_encoder on CPU (VRAM optimization)")

        submodel_dict = {
            "transformer": transformer,
            "patchifier": patchifier,
            "text_encoder": text_encoder,
            "tokenizer": tokenizer,
            "scheduler": scheduler,
            "vae": vae,
            "prompt_enhancer_image_caption_model": None,
            "prompt_enhancer_image_caption_processor": None,
            "prompt_enhancer_llm_model": None,
            "prompt_enhancer_llm_tokenizer": None,
            "allowed_inference_steps": allowed_inference_steps,
        }

        pipeline = LTXVideoPipeline(**submodel_dict)

        # Override _execution_device to CUDA.
        # text_encoder is the first nn.Module in __init__ signature, so
        # pipeline.device defaults to CPU.  We need CUDA there for latents.
        class _FixedPipeline(pipeline.__class__):
            @property
            def _execution_device(self):
                return torch.device(DEVICE)

            def encode_prompt(self, *a, **kw):
                """Fix device mismatch: ensure all returned masks are on the same device."""
                out = super().encode_prompt(*a, **kw)
                # out = (prompt_embeds, prompt_attention_mask,
                #        negative_prompt_embeds, negative_prompt_attention_mask)
                pe, pam, npe, n_pam = out
                dev = self._execution_device
                if pam is not None and n_pam is not None and pam.device != n_pam.device:
                    n_pam = n_pam.to(pam.device)
                return (pe.to(dev), pam.to(dev), npe.to(dev) if npe is not None else None, n_pam)

        pipeline.__class__ = _FixedPipeline

        import gc

        gc.collect()
        torch.cuda.empty_cache()
        free_mem, total_mem = torch.cuda.mem_get_info()
        logger.info(f"VRAM after init: {free_mem / 1024**3:.1f} GiB free / {total_mem / 1024**3:.1f} GiB total")

        if config.get("pipeline_type") == "multi-scale" and _upscaler_path:
            logger.info(f"Loading spatial upscaler: {_upscaler_path}")
            latent_upsampler = LatentUpsampler.from_pretrained(_upscaler_path)
            latent_upsampler = latent_upsampler.to(torch.bfloat16)
            latent_upsampler.eval()
            latent_upsampler = latent_upsampler.to(DEVICE)
            pipeline = LTXMultiScalePipeline(pipeline, latent_upsampler=latent_upsampler)

        _pipeline = pipeline
        elapsed = time.time() - start
        logger.info(f"Pipeline initialized in {elapsed:.1f}s")

        return _pipeline


def run_inference(
    prompt: str,
    negative_prompt: str = "worst quality, inconsistent motion, blurry, jittery, distorted",
    height: int = 704,
    width: int = 1216,
    num_frames: int = 121,
    frame_rate: int = 30,
    seed: int = -1,
    image_data: str | None = None,
) -> tuple[bytes, int, dict]:
    """Run LTX-Video inference. Returns (mp4_bytes, actual_seed, metadata)."""
    import imageio
    import numpy as np
    from ltx_video.inference import (
        calculate_padding,
        load_image_to_tensor_with_resize_and_crop,
        seed_everething,
    )
    from ltx_video.pipelines.pipeline_ltx_video import ConditioningItem

    pipeline = ensure_pipeline()
    config = _pipeline_config_dict
    device = DEVICE  # use module-level device; components are on mixed devices

    if seed < 0:
        seed = int(time.time() * 1000) % 1000000

    seed_everething(seed)

    # Pad dimensions
    height_padded = ((height - 1) // 32 + 1) * 32
    width_padded = ((width - 1) // 32 + 1) * 32
    num_frames_padded = ((num_frames - 2) // 8 + 1) * 8 + 1
    padding = calculate_padding(height, width, height_padded, width_padded)

    offload_to_cpu = config.get("offload_to_cpu", False)
    if offload_to_cpu and torch.cuda.is_available():
        total_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        offload_to_cpu = total_mem < 30
    else:
        offload_to_cpu = False

    stg_mode = config.get("stg_mode", "attention_values")
    from ltx_video.utils.skip_layer_strategy import SkipLayerStrategy

    mode_map = {
        "attention_values": SkipLayerStrategy.AttentionValues,
        "attention_skip": SkipLayerStrategy.AttentionSkip,
        "residual": SkipLayerStrategy.Residual,
        "transformer_block": SkipLayerStrategy.TransformerBlock,
    }
    skip_layer_strategy = mode_map.get(stg_mode.lower(), SkipLayerStrategy.AttentionValues)

    # Prepare conditioning from image if provided
    conditioning_items = None
    if image_data:
        try:
            img = Image.open(BytesIO(base64.b64decode(image_data))).convert("RGB")
            media_tensor = load_image_to_tensor_with_resize_and_crop(img, height, width, just_crop=False)
            from torch.nn import functional as F

            media_tensor = F.pad(media_tensor, padding)
            conditioning_items = [ConditioningItem(media_tensor, 0, 1.0)]
        except Exception as e:
            logger.error(f"Failed to process conditioning image: {e}")
            raise ValueError(f"Invalid image data: {e}")

    # Run pipeline
    sample_input = {
        "prompt": prompt,
        "prompt_attention_mask": None,
        "negative_prompt": negative_prompt,
        "negative_prompt_attention_mask": None,
    }

    generator = torch.Generator(device=device).manual_seed(seed)

    pipeline_kwargs = {
        k: v
        for k, v in config.items()
        if k
        not in (
            "checkpoint_path",
            "spatial_upscaler_model_path",
            "pipeline_type",
            "precision",
            "sampler",
            "stg_mode",
            "text_encoder_model_name_or_path",
            "prompt_enhancement_words_threshold",
            "prompt_enhancer_image_caption_model_name_or_path",
            "prompt_enhancer_llm_model_name_or_path",
            "offload_to_cpu",
        )
    }

    logger.info(f"Running inference: seed={seed}, prompt='{prompt[:80]}...'")

    images = pipeline(
        **pipeline_kwargs,
        skip_layer_strategy=skip_layer_strategy,
        generator=generator,
        output_type="pt",
        callback_on_step_end=None,
        height=height_padded,
        width=width_padded,
        num_frames=num_frames_padded,
        frame_rate=frame_rate,
        **sample_input,
        media_items=None,
        conditioning_items=conditioning_items,
        is_video=True,
        vae_per_channel_normalize=True,
        image_cond_noise_scale=0.05,
        mixed_precision=False,
        offload_to_cpu=offload_to_cpu,
        enhance_prompt=False,
    ).images

    # Crop padded
    pad_left, pad_right, pad_top, pad_bottom = padding
    pad_bottom = -pad_bottom if pad_bottom else images.shape[3]
    pad_right = -pad_right if pad_right else images.shape[4]
    images = images[:, :, :num_frames, pad_top:pad_bottom, pad_left:pad_right]

    # Convert to MP4 via temp file (FFMPEG format in imageio v2 requires a path).
    video_np = images[0].permute(1, 2, 3, 0).cpu().float().numpy()
    video_np = (video_np * 255).clip(0, 255).astype(np.uint8)

    tmp_path = tempfile.mktemp(suffix=".mp4")
    try:
        with imageio.get_writer(
            tmp_path, format="FFMPEG", fps=frame_rate, codec="libx264", output_params=["-crf", "17"]
        ) as video:
            for frame in video_np:
                video.append_data(frame)
        with open(tmp_path, "rb") as f:
            mp4_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    metadata = {
        "seed": seed,
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
        "padded_height": height_padded,
        "padded_width": width_padded,
        "padded_frames": num_frames_padded,
    }

    return mp4_bytes, seed, metadata


# ── Flask Routes ──


@app.route("/health", methods=["GET"])
def health():
    try:
        if _pipeline is not None:
            return jsonify({"status": "ok", "device": DEVICE, "pipeline_loaded": True})
        return jsonify({"status": "ok", "device": DEVICE, "pipeline_loaded": False})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/v1/unload", methods=["POST"])
def unload_pipeline():
    """Unload the pipeline and free GPU memory for other services (SD, LLM)."""
    global _pipeline
    _pipeline = None
    if torch.cuda.is_available():
        try:
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        except Exception as e:
            logger.warning(f"CUDA cleanup during unload failed: {e}")
    import gc
    gc.collect()
    free_mem, total_mem = torch.cuda.mem_get_info()
    logger.info(f"Pipeline unloaded — VRAM: {free_mem / 1024**3:.1f} GiB free / {total_mem / 1024**3:.1f} GiB total")
    return jsonify({"status": "ok", "freed": True})


@app.route("/v1/video/generations", methods=["POST"])
def generate_video():
    global _pipeline
    gen_start = time.time()

    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400

        prompt = data.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "Missing 'prompt'"}), 400

        negative_prompt = data.get("negative_prompt", "worst quality, inconsistent motion, blurry, jittery, distorted")
        height = int(data.get("height", 512))
        width = int(data.get("width", 896))
        num_frames = int(data.get("num_frames", 257))
        frame_rate = int(data.get("frame_rate", 30))
        seed = int(data.get("seed", -1))
        image_data = data.get("image_data")

        mp4_bytes, actual_seed, meta = run_inference(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=height,
            width=width,
            num_frames=num_frames,
            frame_rate=frame_rate,
            seed=seed,
            image_data=image_data,
        )

        gen_time = round(time.time() - gen_start, 1)
        video_b64 = base64.b64encode(mp4_bytes).decode("utf-8")

        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}.mp4"

        return jsonify(
            {
                "success": True,
                "video_data": video_b64,
                "file_name": filename,
                "file_size": len(mp4_bytes),
                "file_type": "video/mp4",
                "generation_time": gen_time,
                "seed": actual_seed,
                "metadata": meta,
            }
        )

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Inference error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        elapsed = time.time() - gen_start
        logger.info(f"Total request time: {elapsed:.1f}s")
        # Clean up CUDA to avoid fragmentation for next request
        if torch.cuda.is_available():
            try:
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "reset_peak_memory_stats"):
                    torch.cuda.reset_peak_memory_stats()
                free_mem, total_mem = torch.cuda.mem_get_info()
                logger.info(
                    f"VRAM after cleanup: {free_mem / 1024**3:.1f} GiB free / {total_mem / 1024**3:.1f} GiB total"
                )
            except Exception as e:
                logger.warning(f"CUDA cleanup error: {e}")
            global _pipeline
            _pipeline = None
            logger.info("Pipeline reset — will reinitialize on next request")
        import gc

        gc.collect()


# Warm up pipeline on import (triggers for both gunicorn and direct runner).
try:
    ensure_pipeline()
    logger.info("Pipeline ready on startup")
except Exception as e:
    logger.warning(f"Pipeline not ready on startup (will init lazily): {e}")

if __name__ == "__main__":
    port = int(os.environ.get("LTX_PORT", 7872))
    logger.info(f"Starting LTX-Video wrapper on port {port}")
    logger.info(f"Device: {DEVICE}, HF_HOME: {HF_HOME}")
    app.run(host="0.0.0.0", port=port, debug=False)
