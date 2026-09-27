"""Model Hub env-var configuration.

Hub env keys are cleared before the overrides are applied so the defaults test
never reads a developer's .env (load_dotenv runs at app.config import) or an
exported HUGGINGFACE_TOKEN, and explicit overrides still win.
"""

from flask import Flask

_HUB_KEYS = (
    "MODEL_HUB_ENABLED",
    "MODEL_HUB_MAX_FILE_GB",
    "MODEL_HUB_FREE_MARGIN_GB",
    "MODEL_HUB_SEARCH_LIMIT",
    "MODEL_HUB_TIMEOUT_S",
    "HUGGINGFACE_TOKEN",
)


def _load(**env_overrides):
    data = {
        "SECRET_KEY": "t",
        "TIMEZONE_STR": "UTC",
        **env_overrides,
    }
    import os

    import app.config as config_mod

    old = {k: os.environ.get(k) for k in list(os.environ)}
    try:
        for k in _HUB_KEYS:
            os.environ.pop(k, None)
        for k in data:
            os.environ[k] = data[k]
        app = Flask(__name__)
        config_mod.load_config(app)
        return app.config
    finally:
        for k in data:
            os.environ.pop(k, None)
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def test_model_hub_defaults():
    cfg = _load()
    assert cfg["MODEL_HUB_ENABLED"] is False
    assert cfg["MODEL_HUB_MAX_FILE_GB"] == 40
    assert cfg["MODEL_HUB_FREE_MARGIN_GB"] == 4
    assert cfg["MODEL_HUB_SEARCH_LIMIT"] == 20
    assert cfg["MODEL_HUB_TIMEOUT_S"] == 3600
    assert cfg["HUGGINGFACE_TOKEN"] == ""


def test_model_hub_env_overrides():
    cfg = _load(MODEL_HUB_ENABLED="true", MODEL_HUB_MAX_FILE_GB="12", HUGGINGFACE_TOKEN="hf_x")
    assert cfg["MODEL_HUB_ENABLED"] is True
    assert cfg["MODEL_HUB_MAX_FILE_GB"] == 12
    assert cfg["HUGGINGFACE_TOKEN"] == "hf_x"
