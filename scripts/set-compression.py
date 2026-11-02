#!/usr/bin/env python3
"""Sceglie la modalita' di compattazione del contesto per MiniMax Code.

Modalita' (una sola attiva alla volta):
  native       MiniMax Code usa il proprio checkpoint (il proxy resta inattivo)
  rizzo        il provider 'mlx' passa dal proxy veloce: Rizzo Flow decide cosa
               tenere e il modello scrive un checkpoint breve
  rizzo-only   come sopra, ma senza generazione: il checkpoint e' composto
               direttamente dal materiale compattato

Lo script riscrive in-place ~/.minimax/config.yaml (le chiavi API restano
intatte: viene toccata solo la riga baseURL del provider 'mlx') e aggiorna
~/.minimax/local-models/fast-compact.json.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from minimax_config import INSTALL_DIR, read_lines, set_provider_base_url, write_lines  # noqa: E402

FASTCOMPACT_CONFIG = os.path.join(INSTALL_DIR, "fast-compact.json")

MODES = {
    "native": {"base_url": "http://127.0.0.1:8080/v1", "mode": "native"},
    "rizzo": {"base_url": "http://127.0.0.1:8081/v1", "mode": "rizzo"},
    "rizzo-only": {"base_url": "http://127.0.0.1:8081/v1", "mode": "rizzo-only"},
}


def update_fastcompact_config(mode: str) -> None:
    os.makedirs(INSTALL_DIR, exist_ok=True)
    try:
        with open(FASTCOMPACT_CONFIG, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    data["mode"] = mode
    data.setdefault("level", "fast")
    data.setdefault("max_tokens", 1200)
    data.setdefault("tool_chars", 400)
    data.setdefault("model", "")
    with open(FASTCOMPACT_CONFIG, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        print(f"ERR: modalita' non valida: {mode!r} (usare: {', '.join(MODES)})")
        return 2

    settings = MODES[mode]
    update_fastcompact_config(settings["mode"])

    try:
        lines = read_lines()
    except OSError as error:
        print(f"ERR: impossibile leggere la configurazione: {error}")
        return 1

    new_lines, changed = set_provider_base_url(lines, "mlx", settings["base_url"])
    if not changed:
        print("ERR: provider custom 'mlx' non trovato in config.yaml")
        return 1

    write_lines(new_lines)
    print(f"modalita' compattazione: {mode} (provider mlx -> {settings['base_url']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
