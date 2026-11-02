#!/usr/bin/env python3
"""Registra (o verifica) MiniMax Code come provider custom 'mlx'.

Uso:
  register-provider.py [--context 262144] [--output 8192] [--default-model local|cloud|none]

Scrive in ~/.minimax/config.yaml il blocco del provider custom 'mlx' se non
esiste, ne corregge baseURL e i limiti se esiste, senza mai toccare le
chiavi API già presenti nel file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from minimax_config import (  # noqa: E402
    CONFIG_PATH,
    INSTALL_DIR,
    ensure_provider,
    provider_range,
    read_lines,
    set_default_model,
    write_lines,
)

LOCAL_MODEL = "custom_provider:mlx/lfm3-1.7b-mlx"
CLOUD_MODEL = "minimax/MiniMax-M3.1-Flash"
FASTCOMPACT_CONFIG = os.path.join(INSTALL_DIR, "fast-compact.json")


def current_mode() -> str:
    """Modalità di compattazione attiva (decide l'indirizzo del provider)."""
    try:
        with open(FASTCOMPACT_CONFIG, encoding="utf-8") as handle:
            data = json.load(handle)
        mode = data.get("mode")
        if isinstance(mode, str) and mode:
            return mode
    except (OSError, json.JSONDecodeError):
        pass
    return "native"


def default_base_url() -> str:
    """Il provider deve puntare al proxy quando la compattazione è accelerata."""
    return "http://127.0.0.1:8081/v1" if current_mode() != "native" else "http://127.0.0.1:8080/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Registra il provider MLX in MiniMax Code")
    parser.add_argument("--context", type=int, default=262144, help="finestra di contesto")
    parser.add_argument("--output", type=int, default=8192, help="token massimi in uscita")
    parser.add_argument(
        "--default-model",
        choices=["local", "cloud", "none"],
        default="none",
        help="imposta anche il modello predefinito",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="indirizzo del provider (default: segue la modalità di compattazione attiva)",
    )
    args = parser.parse_args()

    if not os.path.exists(CONFIG_PATH):
        print(f"ERR: non trovo {CONFIG_PATH}. Avvia MiniMax Code almeno una volta.")
        return 1

    base_url = args.base_url or default_base_url()
    lines = read_lines()
    existed = provider_range(lines, "mlx")[0] >= 0
    lines, _ = ensure_provider(lines, "mlx", base_url, args.context, args.output)

    model = None
    if args.default_model == "local":
        model = LOCAL_MODEL
    elif args.default_model == "cloud":
        model = CLOUD_MODEL
    lines, _ = set_default_model(lines, model)

    write_lines(lines)
    print(f"provider 'mlx': {'aggiornato' if existed else 'registrato'} in {CONFIG_PATH}")
    print(f"baseURL: {base_url} (modalita' compressione: {current_mode()})")
    if model:
        print(f"modello predefinito: {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
