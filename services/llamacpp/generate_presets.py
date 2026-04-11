#!/usr/bin/env python3
"""
Generate llama.cpp models-preset.ini from SQLite model_configs table.

This script runs before llama-server starts. It reads custom model parameters
from the chat database and generates /models/models-preset.ini so that
llama.cpp router mode uses the admin-configured values (ctx-size, n-gpu-layers,
temperature, top-p).

If no custom configs exist in DB, falls back to hardcoded defaults.
"""

import sqlite3
import os
import sys
import configparser

DB_PATH = '/app/data/chat.db'
PRESET_PATH = '/models/models-preset.ini'

# Fallback defaults if nothing in DB
# n-gpu-layers = -1 means ALL layers on GPU (maximum speed)
# Models are loaded/unloaded on-demand (flickering mode)
DEFAULTS = {
    'Qwen3-4B-Instruct-2507-Q4_K_M': {
        'model': '/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
        'n-gpu-layers': '-1',
        'ctx-size': '8192',
        'temperature': '0.1',
        'top-p': '0.1',
    },
    'Qwen3VL-8B-Instruct-Q4_K_M': {
        'model': '/models/Qwen3VL-8B-Instruct-Q4_K_M/Qwen3VL-8B-Instruct-Q4_K_M.gguf',
        'mmproj': '/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-F16.gguf',
        'n-gpu-layers': '-1',
        'ctx-size': '8192',
        'temperature': '0.7',
        'top-p': '0.9',
    },
    'bge-m3-Q8_0': {
        'model': '/models/bge-m3-Q8_0.gguf',
        'n-gpu-layers': '-1',
        'ctx-size': '512',
    },
    'gemma-4-26B-A4B-it-MXFP4_MOE': {
        'model': '/models/gemma-4-26B-A4B-it-MXFP4_MOE.gguf',
        'n-gpu-layers': '-1',
        'ctx-size': '8192',
        'temperature': '0.7',
        'top-p': '0.9',
    },
    'gpt-oss-20b-mxfp4': {
        'model': '/models/gpt-oss-20b-mxfp4.gguf',
        'n-gpu-layers': '-1',
        'ctx-size': '8192',
        'temperature': '0.7',
        'top-p': '0.9',
    },
}


def read_db() -> dict:
    """Read model_configs from SQLite DB."""
    configs = {}
    if not os.path.exists(DB_PATH):
        print(f"[generate_presets] DB not found at {DB_PATH}, using defaults")
        return configs

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            # Check if table exists
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_configs'")
            if not c.fetchone():
                print("[generate_presets] model_configs table not found, using defaults")
                return configs

            c.execute('SELECT * FROM model_configs')
            for row in c.fetchall():
                module = dict(row)
                module_name = module.get('module', '')
                # Map module name to preset section name
                section_map = {
                    'chat': 'Qwen3-4B-Instruct-2507-Q4_K_M',
                    'multimodal': 'Qwen3VL-8B-Instruct-Q4_K_M',
                    'embedding': 'bge-m3-Q8_0',
                    'reasoning': 'gemma-4-26B-A4B-it-MXFP4_MOE',
                }
                section = section_map.get(module_name)
                if section:
                    configs[section] = module
    except Exception as e:
        print(f"[generate_presets] Error reading DB: {e}")

    return configs


def generate_ini(db_configs: dict) -> str:
    """Generate models-preset.ini content from DB configs + defaults."""
    lines = [
        '# llama.cpp model presets — auto-generated from model_configs DB',
        '# Edits to this file will be overwritten on next container restart.',
        '# Use the admin panel to change model parameters.',
        '',
    ]

    for section_name, defaults in DEFAULTS.items():
        lines.append(f'[{section_name}]')

        # Start with defaults
        params = dict(defaults)

        # Override with DB values if present
        db_cfg = db_configs.get(section_name, {})
        overrides = {
            'ctx-size': 'ctx_size',
            'n-gpu-layers': 'n_gpu_layers',
            'temperature': 'temperature',
            'top-p': 'top_p',
            'model': 'model_name',
        }
        for ini_key, db_key in overrides.items():
            val = db_cfg.get(db_key)
            if val is not None and val != '':
                params[ini_key] = str(val)

        for key, val in params.items():
            lines.append(f'{key} = {val}')

        lines.append('')

    return '\n'.join(lines)


def main():
    print("[generate_presets] Starting...")
    db_configs = read_db()
    content = generate_ini(db_configs)

    os.makedirs(os.path.dirname(PRESET_PATH), exist_ok=True)
    with open(PRESET_PATH, 'w') as f:
        f.write(content)

    print(f"[generate_presets] Written {PRESET_PATH}")
    print(content)


if __name__ == '__main__':
    main()
