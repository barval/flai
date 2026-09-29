"""Deployment-script integrity tests.

Regression: `services/ltx_video/download-t5-encoder.sh` considered the download
"complete" when only `config.json` plus the FIRST safetensors shard existed.
An interrupted run therefore wrote a success banner while
`model-00002-of-00002.safetensors` stayed missing, and LTX-Video crashed at
inference time with "No such file or directory" on the missing shard.

These tests pin the completeness check to cover every declared file so a
partial T5 can never be reported as downloaded again.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "services" / "ltx_video" / "download-t5-encoder.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")

DEPLOY_SH = REPO / "deploy.sh"
DEPLOY_RU_SH = REPO / "deploy-ru.sh"


def _helper_body(name: str) -> str:
    start = SOURCE.index(f"{name}() {{") + len(name)
    end = SOURCE.index("}\n", start)
    return SOURCE[start:end]


def test_t5_completeness_helper_checks_every_declared_file():
    body = _helper_body("t5_encoder_complete")
    assert 'for file in "${!FILES[@]}"' in body, "helper must iterate the FILES map"
    assert '[ ! -f "$TARGET_DIR/$file" ]' in body, "helper must test -f per declared file"


def test_both_auto_methods_gate_on_completeness_helper():
    assert SOURCE.count("if t5_encoder_complete; then") == 2, (
        "docker and git-lfs success paths must use the completeness helper"
    )


def test_partial_shard_check_removed():
    assert 'model-00001-of-00002.safetensors" ]; then' not in SOURCE, (
        "the old config+first-shard-only check must be gone"
    )


def test_deploy_scripts_do_not_skip_partial_t5():
    """Deploy wrapper must not treat an existing directory as 'T5 done' — a
    partial T5 (first shard only) must trigger the download script again."""
    condition = 'if [[ ! -f "$VIDEO_DIR/t5_encoder/text_encoder/model-00002-of-00002.safetensors" ]]; then'
    for script in (DEPLOY_SH, DEPLOY_RU_SH):
        src = script.read_text(encoding="utf-8")
        assert condition in src, f"{script.name} must gate on the final shard file, not the directory"
