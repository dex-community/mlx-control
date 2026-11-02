#!/usr/bin/env python3
"""Utility condivise per leggere e modificare ~/.minimax/config.yaml.

La configurazione contiene chiavi API reali: le modifiche sono fatte sempre
in modo chirurgico, riga per riga, senza riscrivere il file e senza toccare
le chiavi esistenti.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Iterable

HOME = os.path.expanduser("~")
CONFIG_PATH = os.environ.get("MINIMAX_CONFIG") or os.path.join(HOME, ".minimax", "config.yaml")
INSTALL_DIR = os.environ.get("LFM3_INSTALL_DIR") or os.path.join(HOME, ".minimax", "local-models")
CONTROL_DIR = os.environ.get("MLX_CONTROL_DIR") or os.path.join(HOME, ".mlxControl")

PROVIDER_TEMPLATE = """  mlx:
    name: LFM3 MLX (locale)
    kind: custom
    enabled: true
    api: openai-completions
    options:
      baseURL: {base_url}
      authMode: api-key
      apiKey: sk-mlx-local
    models:
      lfm3-1.7b-mlx:
        name: LFM3-1.7B-MLX-4bit (locale)
        configuration_source: discovered
        enabled: true
        attachment: false
        tool_call: true
        temperature: true
        modalities:
          input:
            - text
          output:
            - text
        limit:
          context: {context}
          output: {output}
"""


def read_lines(path: str = CONFIG_PATH) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        return handle.readlines()


def write_lines(lines: Iterable[str], path: str = CONFIG_PATH) -> None:
    """Scrive il file conservando un backup datato."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def custom_provider_range(lines: list[str]) -> tuple[int, int]:
    """Indici (inizio, fine) del blocco 'custom_provider:' a livello radice."""
    start = None
    for index, line in enumerate(lines):
        if _indent_of(line) == 0 and line.strip().startswith("custom_provider:"):
            start = index
            break
    if start is None:
        return (-1, -1)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _indent_of(lines[index]) == 0 and lines[index].strip():
            end = index
            break
    return (start, end)


def provider_range(lines: list[str], provider: str) -> tuple[int, int]:
    """Indici (inizio, fine) del blocco di un provider dentro custom_provider."""
    start_block, end_block = custom_provider_range(lines)
    if start_block < 0:
        return (-1, -1)
    start = None
    for index in range(start_block + 1, end_block):
        line = lines[index]
        if _indent_of(line) == 2 and line.strip().rstrip(":") == provider:
            start = index
            break
    if start is None:
        return (-1, -1)
    end = end_block
    for index in range(start + 1, end_block):
        if _indent_of(lines[index]) == 2 and lines[index].strip():
            end = index
            break
    return (start, end)


def set_provider_base_url(lines: list[str], provider: str, base_url: str) -> tuple[list[str], bool]:
    """Sostituisce options.baseURL del provider indicato."""
    start, end = provider_range(lines, provider)
    if start < 0:
        return (lines, False)
    in_options = False
    changed = False
    out: list[str] = []
    for index, line in enumerate(lines):
        if index < start or index >= end:
            out.append(line)
            continue
        stripped = line.strip()
        indent = _indent_of(line)
        if indent == 4 and stripped.rstrip(":") == "options":
            in_options = True
            out.append(line)
            continue
        if in_options and indent == 6 and stripped.startswith("baseURL:"):
            out.append(f"      baseURL: {base_url}\n")
            changed = True
            in_options = False
            continue
        if in_options and indent <= 4:
            in_options = False
        out.append(line)
    return (out, changed)


def ensure_provider(
    lines: list[str],
    provider: str = "mlx",
    base_url: str = "http://127.0.0.1:8080/v1",
    context: int = 262144,
    output: int = 8192,
) -> tuple[list[str], bool]:
    """Garantisce che il provider esista; lo inserisce se manca."""
    start, end = provider_range(lines, provider)
    if start >= 0:
        return set_provider_base_url(lines, provider, base_url)

    block = PROVIDER_TEMPLATE.format(base_url=base_url, context=context, output=output)
    block_lines = block.splitlines(keepends=True)
    block_start, block_end = custom_provider_range(lines)
    if block_start < 0:
        # custom_provider non esiste: lo creiamo in fondo al file.
        out = list(lines)
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append("custom_provider:\n")
        out.extend(block_lines)
        return (out, True)
    out = list(lines)
    out[block_end:block_end] = block_lines
    return (out, True)


def set_default_model(lines: list[str], model: str | None) -> tuple[list[str], bool]:
    """Imposta (o rimuove) defaultModel; None lascia il file invariato."""
    if not model:
        return (lines, False)
    out: list[str] = []
    changed = False
    for line in lines:
        if _indent_of(line) == 0 and line.strip().startswith("defaultModel:"):
            out.append(f"defaultModel: {model}\n")
            changed = True
            continue
        out.append(line)
    if not changed:
        out.insert(0, f"defaultModel: {model}\n")
    return (out, True)
