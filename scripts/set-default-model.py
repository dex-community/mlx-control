#!/usr/bin/env python3
"""Imposta il modello predefinito di MiniMax Code in ~/.minimax/config.yaml.

Uso: set-default-model.py <provider/model>
     es. set-default-model.py minimax/MiniMax-M3.1-Flash
         set-default-model.py custom_provider:mlx/lfm3-1.7b-mlx
"""
import os
import re
import sys

CONFIG = os.path.expanduser("~/.minimax/config.yaml")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    value = sys.argv[1].strip()
    if not value:
        print("ERR: modello vuoto")
        return 2

    with open(CONFIG, encoding="utf-8") as fh:
        text = fh.read()

    new_text, count = re.subn(
        r"^defaultModel:.*$",
        f"defaultModel: {value}",
        text,
        count=1,
        flags=re.M,
    )
    if count == 0:
        # la chiave non c'è: la aggiungiamo in testa al file
        new_text = f"defaultModel: {value}\n" + text

    with open(CONFIG, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    print(f"defaultModel = {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
