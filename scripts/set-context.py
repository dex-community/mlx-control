#!/usr/bin/env python3
"""Aggiorna la finestra di contesto del provider MLX in MiniMax Code.

Due passi necessari:
  1. config.yaml -> custom_provider.mlx.models.lfm3-1.7b-mlx.limit.context
  2. metadati delle sessioni esistenti -> effectiveModelContextWindow
     (il runtime congela il limite alla creazione della sessione: senza questo
      passo le chat vecchie continuano a compattare o a essere rifiutate).

Uso: set-context.py <context_tokens>
"""
import json
import os
import shutil
import sqlite3
import sys
import time

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".minimax", "config.yaml")
DB = os.path.join(HOME, ".minimax", "v2", "sqlite", "runtime-state.sqlite")
BACKUP = os.path.join(HOME, ".minimax", "v2", "sqlite", "runtime-state.backup.sqlite")
PROVIDERS = ("mlx", "mlx-caveman")
MODEL_KEY = "lfm3-1.7b-mlx"


def update_config(value: int) -> None:
    """Riscrive limit.context dei provider MLX, preservando il resto del file."""
    with open(CONFIG, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out, i, changed = [], 0, 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.rstrip() in (f"  {p}:" for p in PROVIDERS):
            provider = line.strip().rstrip(":")
            j = i + 1
            in_model = False
            while j < len(lines):
                cur = lines[j]
                # il blocco provider finisce alla prossima chiave di primo livello
                if cur.strip() and not cur.startswith("    ") and cur.endswith(":") and cur != line:
                    break
                if cur.strip() == f"{MODEL_KEY}:":
                    in_model = True
                    out.append(cur)
                    j += 1
                    continue
                if in_model and cur.startswith("        limit:"):
                    out.append(cur)
                    j += 1
                    while j < len(lines) and lines[j].startswith("          "):
                        if lines[j].strip().startswith("context:"):
                            out.append(f"          context: {value}")
                            changed += 1
                        else:
                            out.append(lines[j])
                        j += 1
                    continue
                out.append(cur)
                j += 1
            i = j
            continue
        i += 1

    if changed == 0:
        raise SystemExit(f"ERR: nessun blocco 'context:' trovato sotto {PROVIDERS} in {CONFIG}")

    with open(CONFIG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print(f"config.yaml: {changed} blocchi context impostati a {value}")


def update_sessions(value: int) -> int:
    if not os.path.exists(DB):
        print("sqlite runtime non trovato: sessioni non allineate")
        return 0
    if not os.path.exists(BACKUP):
        shutil.copy(DB, BACKUP)
    con = sqlite3.connect(DB, timeout=20)
    try:
        rows = con.execute(
            "select session_id, extra_data_json from local_runtime_sessions "
            "where extra_data_json is not null"
        ).fetchall()
        fixed = []
        for sid, blob in rows:
            try:
                data = json.loads(blob)
            except Exception:
                continue
            model = str(data.get("effectiveModel", ""))
            if not model.startswith("custom_provider:mlx"):
                continue
            if data.get("effectiveModelContextWindow") == value:
                continue
            data["effectiveModelContextWindow"] = value
            con.execute(
                "update local_runtime_sessions set extra_data_json=? where session_id=?",
                (json.dumps(data), sid),
            )
            fixed.append(sid)
        con.commit()
    finally:
        con.close()
    for sid in fixed:
        print(f"  sessione allineata: {sid}")
    return len(fixed)


def context_from_config() -> int:
    """Legge limit.context dal provider mlx in config.yaml."""
    with open(CONFIG, encoding="utf-8") as fh:
        text = fh.read()
    parts = text.split("custom_provider:")
    chunk = parts[-1] if len(parts) > 1 else text
    for line in chunk.split("\n"):
        stripped = line.strip()
        if stripped.startswith("context:"):
            try:
                return int(stripped.split(":", 1)[1].strip())
            except ValueError:
                continue
        if stripped.startswith("provider:") or stripped.startswith("defaultModel:"):
            break
    return 262144


def main() -> int:
    sessions_only = "--sessions-only" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if sessions_only:
        value = context_from_config()
        print(f"context da config.yaml: {value}")
    else:
        if len(args) != 1:
            print(__doc__)
            return 2
        try:
            value = int(args[0])
        except ValueError:
            print("ERR: il contesto deve essere un intero")
            return 2
        if value < 4096:
            print("ERR: contesto troppo piccolo")
            return 2
        update_config(value)

    count = update_sessions(value)
    print(f"sessioni allineate: {count} (context window {value})")
    print("NOTA: riavvia MiniMax Code per le nuove sessioni; le esistenti sono già allineate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
