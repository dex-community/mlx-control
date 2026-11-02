#!/usr/bin/env python3
"""Gestione dei modelli MLX locali (download da Hugging Face, attivazione).

Comandi:
  list-installed            -> JSON: [{path, name, size_gb}]
  list-remote [query]       -> JSON: [{id, downloads, size_gb, tags}]
  download <repo_id>        -> scarica il modello, stampa righe JSON di progresso
  set-active <path>         -> imposta il modello servito dal server
  active                    -> percorso del modello attivo
"""
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
MODELS_ROOT = os.path.join(HOME, ".lmstudio", "models")
INSTALL_DIR = os.path.join(HOME, ".minimax", "local-models")
ACTIVE_FILE = os.path.join(INSTALL_DIR, "current-model.txt")
VENV_PY = os.path.join(INSTALL_DIR, "mlxenv", "bin", "python")
HF_API = "https://huggingface.co/api/models"

# Modelli non testuali (ASR, TTS, embedding): non servono a MiniMax Code.
NON_TEXTUAL = {
    "automatic-speech-recognition",
    "text-to-speech",
    "text-to-audio",
    "feature-extraction",
    "sentence-similarity",
    "audio-text-to-text",
    "image-text-to-text",
    "video-text-to-text",
}


def human_gb(num_bytes: float) -> float:
    return round(num_bytes / (1024 ** 3), 2)


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- modelli locali

def list_installed() -> list:
    out = []
    if not os.path.isdir(MODELS_ROOT):
        return out
    for entry in sorted(os.listdir(MODELS_ROOT)):
        org_dir = os.path.join(MODELS_ROOT, entry)
        if not os.path.isdir(org_dir):
            continue
        for name in sorted(os.listdir(org_dir)):
            path = os.path.join(org_dir, name)
            if not os.path.isdir(path):
                continue
            if not os.path.exists(os.path.join(path, "config.json")):
                continue
            out.append({
                "path": path,
                "name": f"{entry}/{name}",
                "size_gb": human_gb(dir_size(path)),
            })
    return out


def active_model() -> str:
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE) as fh:
            value = fh.read().strip()
            if value and os.path.isdir(value):
                return value
    default = os.path.join(MODELS_ROOT, "LiquidAI", "LFM3-1.7B-MLX-4bit")
    return default if os.path.isdir(default) else ""


def set_active(path: str) -> None:
    if not os.path.isdir(path):
        raise SystemExit(f"ERR: percorso non valido: {path}")
    os.makedirs(INSTALL_DIR, exist_ok=True)
    with open(ACTIVE_FILE, "w") as fh:
        fh.write(path)
    print(f"modello attivo: {path}")


# ------------------------------------------------------------ modelli remoti

def list_remote(query: str = "") -> list:
    import urllib.parse
    import urllib.request

    params = {
        "filter": "mlx",
        "sort": "downloads",
        "direction": "-1",
        "limit": "40",
    }
    if query:
        params["search"] = query
    url = f"{HF_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "lfm3-mlx-control"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        models = json.load(resp)

    out = []
    for m in models:
        tags = m.get("tags", [])
        if NON_TEXTUAL & set(tags):
            continue
        if "text-generation" not in tags and "conversational" not in tags:
            continue
        out.append({
            "id": m.get("id", ""),
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "tags": [t for t in tags if t in ("mlx", "4bit", "8bit", "6bit", "text-generation")],
        })
    return out[:25]


def download(repo_id: str) -> None:
    """Scarica un repo Hugging Face in ~/.lmstudio/models/<org>/<nome>."""
    if "/" not in repo_id:
        raise SystemExit(f"ERR: id Hugging Face non valido: {repo_id}")
    org, name = repo_id.split("/", 1)
    target = os.path.join(MODELS_ROOT, org, name)
    os.makedirs(target, exist_ok=True)

    script = f"""
import json, sys
from huggingface_hub import snapshot_download
path = snapshot_download(
    repo_id={repo_id!r},
    local_dir={target!r},
    ignore_patterns=["*.gguf", "original/*", "*.pth", "*.bin"],
)
print("DONE " + path)
"""
    proc = subprocess.Popen(
        [VENV_PY, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    last_percent = -1
    for line in proc.stdout:
        line = line.rstrip()
        match = re.search(r"(\d{1,3})%\|", line)
        if match:
            percent = int(match.group(1))
            if percent != last_percent:
                last_percent = percent
                print(json.dumps({"status": "progress", "percent": percent}), flush=True)
        elif line.startswith("DONE "):
            print(json.dumps({"status": "done", "path": line[5:]}), flush=True)
        elif line.strip():
            print(json.dumps({"status": "log", "message": line[:200]}), flush=True)
    proc.wait()
    if proc.returncode != 0:
        print(json.dumps({"status": "error", "message": f"exit {proc.returncode}"}), flush=True)
        sys.exit(proc.returncode)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "list-installed":
        print(json.dumps(list_installed()))
    elif cmd == "list-remote":
        print(json.dumps(list_remote(sys.argv[2] if len(sys.argv) > 2 else "")))
    elif cmd == "download":
        if len(sys.argv) < 3:
            raise SystemExit("ERR: manca repo_id")
        download(sys.argv[2])
    elif cmd == "set-active":
        if len(sys.argv) < 3:
            raise SystemExit("ERR: manca il percorso")
        set_active(sys.argv[2])
    elif cmd == "active":
        print(active_model())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
