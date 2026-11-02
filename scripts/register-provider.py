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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from minimax_config import (  # noqa: E402
    CONFIG_PATH,
    ensure_provider,
    provider_range,
    read_lines,
    set_default_model,
    write_lines,
)

LOCAL_MODEL = "custom_provider:mlx/lfm3-1.7b-mlx"
CLOUD_MODEL = "minimax/MiniMax-M3.1-Flash"


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
        default="http://127.0.0.1:8080/v1",
        help="indirizzo del provider (8080 = diretto, 8081 = compattazione veloce)",
    )
    args = parser.parse_args()

    if not os.path.exists(CONFIG_PATH):
        print(f"ERR: non trovo {CONFIG_PATH}. Avvia MiniMax Code almeno una volta.")
        return 1

    lines = read_lines()
    existed = provider_range(lines, "mlx")[0] >= 0
    lines, _ = ensure_provider(lines, "mlx", args.base_url, args.context, args.output)

    model = None
    if args.default_model == "local":
        model = LOCAL_MODEL
    elif args.default_model == "cloud":
        model = CLOUD_MODEL
    lines, _ = set_default_model(lines, model)

    write_lines(lines)
    print(f"provider 'mlx': {'aggiornato' if existed else 'registrato'} in {CONFIG_PATH}")
    if model:
        print(f"modello predefinito: {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
