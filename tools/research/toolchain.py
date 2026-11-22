"""Versioned host analysis tools; independent of firmware and guest policies."""
import json
import os
from pathlib import PurePosixPath

from project_paths import PROJECT_ROOT


def toolchain_config():
    config = json.loads((PROJECT_ROOT / 'tools/toolchain.json').read_text(encoding='utf-8'))
    if config['schema_version'] != 1:
        raise ValueError('Unsupported host toolchain schema')
    return config


def rea_profile(name=None, *, tools_root=None):
    config = toolchain_config()
    name = name if name is not None else config['default_rea_profile']
    if name not in config['rea_profiles']:
        raise ValueError(f'Unknown REA toolchain profile: {name}')
    root = (tools_root if tools_root is not None
            else os.environ.get('A6400_REA_TOOLS_ROOT', config['rea_tools_root']))
    if not PurePosixPath(root).is_absolute():
        raise ValueError('REA tools root must be an absolute Linux path')
    profile = dict(config['rea_profiles'][name], name=name)
    for field in ('node', 'entry', 'ghidra', 'java'):
        profile[field] = str(PurePosixPath(root) / profile[field])
    return profile
