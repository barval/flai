#!/usr/bin/env python3
"""
Generate llama.cpp models-preset.ini from PostgreSQL model_configs table.

This script runs before llama-server starts. It reads custom model parameters
from the chat database and generates /models/models-preset.ini so that
llama.cpp router mode uses the admin-configured values.

If no custom configs exist in DB, falls back to hardcoded defaults.
"""

import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv('DATABASE_URL', 'postgresql://flai:flai_password@flai-postgres:5432/flai')
PRESET_PATH = '/models/models-preset.ini'

# Fallback defaults if nothing in DB
# n-gpu-layers = -1 means ALL layers on GPU (maximum speed)
# Models are loaded/unloaded on-demand (flickering mode)
# Only active models used in FLAI v8.0
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
        'ctx-size': '8192',
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
    """Read model_configs from PostgreSQL DB."""
    configs = {}
    try:
        conn = psycopg2.connect(DB_URL)
        conn.cursor_factory = RealDictCursor
        c = conn.cursor()
        c.execute("SELECT * FROM model_configs WHERE module IS NOT NULL AND module != '' AND module != 'chunks' AND module != 'reranker'")
        for row in c.fetchall():
            module = dict(row)
            module_name = module.get('module', '')
            # Use module name directly as section name (chat, reasoning, multimodal, embedding)
            if module_name:
                configs[module_name] = module
        conn.close()
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

    # Modules that need embeddings enabled (reranker disabled in v8.0)
    EMBEDDING_MODULES = {'embedding'}

    # First add all modules from DB (or their selected models)
    processed = set()
    for section_name in db_configs:
        # Get defaults for this section if exists, otherwise start empty
        defaults = DEFAULTS.get(section_name, {})
        lines.append(f'[{section_name}]')
        params = dict(defaults)
        processed.add(section_name)

        # Add embeddings=1 only for embedding models
        if section_name in EMBEDDING_MODULES:
            params['embeddings'] = 'true'

        # Override with DB values
        db_cfg = db_configs[section_name]
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

        # Ensure model path is absolute - handle both single files and subdirectory paths
        model_val = params.get('model', '')
        if model_val and not model_val.startswith('/') and not model_val.startswith('.'):
            import os
            if '/' in model_val:
                # Has subdirectory path like "model.gguf" - just add /models/ prefix
                if not model_val.endswith('.gguf'):
                    model_val = model_val + '.gguf'
                params['model'] = f'/models/{model_val}'
            else:
                # Single filename - check if file exists
                if not model_val.endswith('.gguf'):
                    model_val = model_val + '.gguf'
                if os.path.exists(f'/models/{model_val}'):
                    params['model'] = f'/models/{model_val}'
                else:
                    params['model'] = f'/models/{model_val}'

        # Same for mmproj
        mmproj_val = params.get('mmproj', '')
        if mmproj_val and not mmproj_val.startswith('/') and not mmproj_val.startswith('.'):
            if not mmproj_val.endswith('.gguf'):
                mmproj_val = mmproj_val + '.gguf'
            params['mmproj'] = f'/models/{mmproj_val}'

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
