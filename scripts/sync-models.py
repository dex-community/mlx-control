#!/usr/bin/env python3
"""Sincronizza i modelli locali con le configurazioni degli agenti.

In base ai modelli effettivamente presenti su disco (~/.mlxControl/models)
genera, in modo dinamico e idempotente:

  1. MiniMax Code: le voci `models:` dei provider custom `mlx` (server MLX
     diretto) e `mlx-caveman` (modello + Caveman) in ~/.minimax/config.yaml;
  2. opencode: i provider `mlx` e `mlx-caveman` in
     ~/.config/opencode/opencode.jsonc;
  3. pi (pi.dev): i provider `mlx` e `mlx-caveman` in
     ~/.pi/agent/models.json;
  4. l'elenco degli alias che il server MLX deve esporre, scritto in
     ~/.mlxControl/model-aliases.env e letto da start-mlx-server.sh.

Ogni target viene aggiornato solo se esiste gia' il suo file di configurazione
(un target non installato viene saltato, senza errori).

Uso:
  sync-models.py            -> sincronizza (nessuna modifica se è già allineato)
  sync-models.py --check    -> dice solo se serve un aggiornamento (exit 0/1)
  sync-models.py --aliases  -> stampa solo gli alias "nome=percorso"
  sync-models.py --json     -> output JSON
  sync-models.py --target minimax|opencode|pi   -> sincronizza solo un target

Il modello attivo (current-model.txt) mantiene la chiave storica
`lfm3-1.7b-mlx`, usata dal defaultModel di MiniMax Code e dal proxy di
compattazione; gli altri modelli ricevono una chiave derivata dal nome della
cartella (es. lfm3-1.7b-mlx-8bit).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

HOME = os.path.expanduser("~")
CONTROL_DIR = os.environ.get("MLX_CONTROL_DIR") or os.path.join(HOME, ".mlxControl")
INSTALL_DIR = os.environ.get("LFM3_INSTALL_DIR") or os.path.join(HOME, ".minimax", "local-models")
MODELS_ROOT = os.path.join(CONTROL_DIR, "models")
ACTIVE_FILE = os.path.join(INSTALL_DIR, "current-model.txt")
CONFIG_PATH = os.environ.get("MINIMAX_CONFIG") or os.path.join(HOME, ".minimax", "config.yaml")
ALIASES_FILE = os.path.join(CONTROL_DIR, "model-aliases.env")
OPENCODE_CONFIG = os.path.join(HOME, ".config", "opencode", "opencode.jsonc")
PI_MODELS = os.path.join(HOME, ".pi", "agent", "models.json")

# Chiave storica: il modello attivo resta selezionabile con il nome usato
# dal defaultModel e dal proxy di compattazione.
LEGACY_KEY = "lfm3-1.7b-mlx"
PROVIDERS = ("mlx", "mlx-caveman")
DEFAULT_CONTEXT = 262144
DEFAULT_OUTPUT = 8192

# Indirizzi dei provider: il modello diretto e il modello + Caveman.
DIRECT_URL = "http://127.0.0.1:8080/v1"
CAVEMAN_URL = "http://127.0.0.1:8787/compat/mlx/v1"
PI_API_KEY = "sk-mlx-local"
TARGETS = ("minimax", "opencode", "pi")


# ----------------------------------------------------------------- modelli

def slug(name: str) -> str:
    """Nome della cartella -> chiave stabile per config.yaml (es. lfm3-1.7b-mlx-8bit)."""
    text = re.sub(r"[^a-z0-9.]+", "-", name.strip().lower()).strip("-.")
    return text or "modello"


def active_model() -> str:
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE, encoding="utf-8") as handle:
            path = handle.read().strip()
        if path and os.path.isdir(path):
            return path
    return ""


def installed_models() -> list[dict]:
    """Modelli presenti su disco: [{path, name, key, active}]."""
    out: list[dict] = []
    if not os.path.isdir(MODELS_ROOT):
        return out
    active = active_model()
    for org in sorted(os.listdir(MODELS_ROOT)):
        org_dir = os.path.join(MODELS_ROOT, org)
        if not os.path.isdir(org_dir):
            continue
        for folder in sorted(os.listdir(org_dir)):
            path = os.path.join(org_dir, folder)
            if not os.path.isdir(path):
                continue
            if not os.path.exists(os.path.join(path, "config.json")):
                continue
            is_active = os.path.abspath(path) == os.path.abspath(active) if active else False
            out.append({
                "path": path,
                "name": folder,
                # Chiave stabile per ogni modello (es. lfm3-1.7b-mlx-8bit).
                "key": slug(folder),
                "active": is_active,
            })
    # Chiavi univoche: se due organizzazioni hanno cartelle omonime, la
    # seconda conserva l'organizzazione come prefisso.
    used: dict[str, int] = {}
    for model in out:
        used[model["key"]] = used.get(model["key"], 0) + 1
    for model in out:
        if used.get(model["key"], 0) > 1 and not model["active"]:
            org = os.path.basename(os.path.dirname(model["path"]))
            model["key"] = f"{slug(org)}-{model['key']}"
    return out


def public_models(models: list[dict]) -> list[dict]:
    """Elenco dei modelli esposti agli agenti.

    Ogni modello ha la propria chiave stabile; il modello attivo e' esposto
    anche con la chiave storica (LEGACY_KEY), usata dal defaultModel di
    MiniMax Code e dal proxy di compattazione.
    """
    out: list[dict] = []
    active = next((m for m in models if m["active"]), None)
    if active is not None:
        out.append({
            "key": LEGACY_KEY,
            "name": active["name"],
            "legacy": True,
            "active": True,
        })
    for model in models:
        if model["key"] == LEGACY_KEY:
            continue
        out.append({
            "key": model["key"],
            "name": model["name"],
            "legacy": False,
            "active": model["active"],
        })
    return out


def alias_pairs(models: list[dict]) -> list[tuple[str, str]]:
    """Alias "nome=percorso" per il server MLX.

    Ogni modello ha un alias stabile derivato dal nome della cartella; il
    modello attivo e' esposto anche con la chiave storica (LEGACY_KEY),
    usata dal defaultModel di MiniMax Code e dal proxy di compattazione.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for model in models:
        key = model["key"]
        if key in seen:
            continue
        seen.add(key)
        pairs.append((key, model["path"]))
    active = next((m for m in models if m["active"]), None)
    if active is not None and LEGACY_KEY not in seen:
        pairs.insert(0, (LEGACY_KEY, active["path"]))
    return pairs


# ------------------------------------------------------------- config.yaml

def read_lines(path: str = CONFIG_PATH) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        return handle.read().split("\n")


def write_lines(lines: list[str], path: str = CONFIG_PATH) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def provider_block(lines: list[str], provider: str) -> tuple[int, int]:
    """Indici (inizio, fine) del blocco `  <provider>:` dentro custom_provider."""
    start = -1
    for index, line in enumerate(lines):
        if line.startswith(f"  {provider}:"):
            start = index
            break
    if start < 0:
        return (-1, -1)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        if line.startswith("    ") or line.startswith("  "):
            # due spazi + testo = altro provider (stesso livello del blocco)
            if line.startswith("  ") and not line.startswith("    ") and not line.startswith("   "):
                end = index
                break
            continue
        end = index
        break
    return (start, end)


def existing_limits(lines: list[str], provider: str) -> dict[str, dict]:
    """Limiti gia' configurati per ogni modello del provider.

    I modelli stanno a indentazione 6 dentro `    models:`; i loro limiti
    sono a indentazione 10 (`          context:` / `          output:`).
    """
    limits: dict[str, dict] = {}
    start, end = provider_block(lines, provider)
    if start < 0:
        return limits
    in_models = False
    current = ""
    for line in lines[start:end]:
        if line.startswith("    models:"):
            in_models = True
            continue
        if not in_models:
            continue
        if not line.strip():
            continue
        if not line.startswith("      "):
            in_models = False
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 6 and stripped.endswith(":"):
            current = stripped[:-1]
            limits.setdefault(current, {})
            continue
        if current and indent >= 10:
            if stripped.startswith("context:"):
                limits[current]["context"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("output:"):
                limits[current]["output"] = stripped.split(":", 1)[1].strip()
    return limits


def model_entry(key: str, label: str, limits: dict) -> list[str]:
    context = limits.get("context", DEFAULT_CONTEXT)
    output = limits.get("output", DEFAULT_OUTPUT)
    return [
        f"      {key}:",
        f"        name: {label}",
        "        configuration_source: discovered",
        "        enabled: true",
        "        attachment: false",
        "        tool_call: true",
        "        temperature: true",
        "        modalities:",
        "          input:",
        "            - text",
        "          output:",
        "            - text",
        "        limit:",
        f"          context: {context}",
        f"          output: {output}",
    ]


def sync_provider(lines: list[str], provider: str, models: list[dict]) -> tuple[list[str], bool]:
    """Riscrrive la sezione models: del provider. Ritorna (righe, modificato)."""
    if not models:
        return (lines, False)
    start, end = provider_block(lines, provider)
    if start < 0:
        return (lines, False)

    models_index = -1
    for index in range(start, end):
        if lines[index].startswith("    models:"):
            models_index = index
            break
    if models_index < 0:
        return (lines, False)

    # fine della sezione models: prima riga non vuota con indent <= 4
    section_end = end
    for index in range(models_index + 1, end):
        line = lines[index]
        if line.strip() and not line.startswith("      "):
            section_end = index
            break

    limits = existing_limits(lines, provider)
    entries: list[str] = []
    active = next((m for m in models if m["active"]), None)
    # Chiave storica: resta sempre valida e punta al modello attivo
    # (e' il defaultModel di MiniMax Code e il modello del proxy).
    legacy_model = active if active is not None else models[0]
    if provider == "mlx-caveman":
        legacy_label = f"{legacy_model['name']} (predefinito, via Caveman)"
        label_of = lambda name: f"{name} (via Caveman)"  # noqa: E731
    else:
        legacy_label = f"{legacy_model['name']} (predefinito)"
        label_of = lambda name: f"{name} (locale)"  # noqa: E731
    entries.extend(model_entry(LEGACY_KEY, legacy_label, limits.get(LEGACY_KEY, {})))
    for model in models:
        key = model["key"]
        if key == LEGACY_KEY:
            continue
        # I modelli nuovi ereditano i limiti della voce storica, cosi' il
        # contesto scelto vale per tutti i modelli registrati.
        model_limits = limits.get(key) or limits.get(LEGACY_KEY, {})
        entries.extend(model_entry(key, label_of(model["name"]), model_limits))

    new_lines = lines[:models_index + 1] + entries + lines[section_end:]
    return (new_lines, new_lines != lines)


def sync_config(models: list[dict]) -> dict:
    """Allinea config.yaml; ritorna un riepilogo."""
    result = {"providers": {}, "changed": False}
    if not os.path.exists(CONFIG_PATH):
        result["error"] = f"config non trovato: {CONFIG_PATH}"
        return result
    lines = read_lines()
    for provider in PROVIDERS:
        lines, changed = sync_provider(lines, provider, models)
        result["providers"][provider] = "aggiornato" if changed else "allineato"
        result["changed"] = result["changed"] or changed
    if result["changed"]:
        backup = f"{CONFIG_PATH}.bak-models-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            with open(CONFIG_PATH, encoding="utf-8") as src:
                content = src.read()
            with open(backup, "w", encoding="utf-8") as dst:
                dst.write(content)
            result["backup"] = backup
        except OSError:
            pass
        write_lines(lines)
    return result


# ------------------------------------------------------------- opencode

def strip_jsonc(text: str) -> str:
    """Rimuove i commenti // e /* */ da un file JSONC (le stringhe sono intatte)."""
    out = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "/":
                while index < length and text[index] != "\n":
                    index += 1
                continue
            if nxt == "*":
                index += 2
                while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                    index += 1
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def opencode_models(models: list[dict], suffix: str) -> dict:
    """Voci `models` per opencode: {id: {name}}."""
    entries: dict[str, dict] = {}
    for model in public_models(models):
        label = f"{model['name']}{suffix}"
        if model["legacy"]:
            label = f"{model['name']} (predefinito){suffix}"
        entries[model["key"]] = {"name": label}
    return entries


def sync_opencode(models: list[dict]) -> dict:
    """Allinea i provider mlx e mlx-caveman di opencode."""
    result = {"changed": False, "path": OPENCODE_CONFIG}
    if not os.path.exists(OPENCODE_CONFIG):
        result["skipped"] = "opencode non configurato"
        return result
    with open(OPENCODE_CONFIG, encoding="utf-8") as handle:
        raw = handle.read()
    try:
        data = json.loads(strip_jsonc(raw))
    except json.JSONDecodeError as exc:
        result["error"] = f"config opencode non valido: {exc}"
        return result

    provider = data.setdefault("provider", {})
    wanted = {
        "mlx": {
            "name": "MLX Control (LFM3 locale)",
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": DIRECT_URL, "apiKey": PI_API_KEY},
            "models": opencode_models(models, " (locale)"),
        },
        "mlx-caveman": {
            "name": "MLX Control + Caveman (compressione)",
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": CAVEMAN_URL, "apiKey": PI_API_KEY},
            "models": opencode_models(models, " (via Caveman)"),
        },
    }
    for key, value in wanted.items():
        current = provider.get(key)
        if current == value:
            continue
        # Si conserva il resto della configurazione del provider esistente.
        if isinstance(current, dict):
            merged = dict(current)
            merged.update(value)
            provider[key] = merged
        else:
            provider[key] = value
        result["changed"] = True

    if not result["changed"]:
        result["state"] = "allineato"
        return result

    backup = f"{OPENCODE_CONFIG}.bak-models-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(OPENCODE_CONFIG, backup)
    result["backup"] = backup
    with open(OPENCODE_CONFIG, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    result["state"] = "aggiornato"
    return result


# ------------------------------------------------------------------- pi

def pi_models(models: list[dict], suffix: str) -> list[dict]:
    """Voci `models` per pi: [{id, name, contextWindow, maxTokens}]."""
    entries = []
    for model in public_models(models):
        label = f"{model['name']}{suffix}"
        if model["legacy"]:
            label = f"{model['name']} (predefinito){suffix}"
        entries.append({
            "id": model["key"],
            "name": label,
            "contextWindow": DEFAULT_CONTEXT,
            "maxTokens": DEFAULT_OUTPUT,
        })
    return entries


def sync_pi(models: list[dict]) -> dict:
    """Allinea i provider mlx e mlx-caveman di pi (pi.dev)."""
    result = {"changed": False, "path": PI_MODELS}
    if not os.path.isdir(os.path.dirname(PI_MODELS)):
        result["skipped"] = "pi non installato"
        return result
    data: dict = {}
    if os.path.exists(PI_MODELS):
        try:
            with open(PI_MODELS, encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            result["error"] = f"models.json di pi non valido: {exc}"
            return result
    else:
        result["created"] = True

    providers = data.setdefault("providers", {})
    wanted = {
        "mlx": {
            "baseUrl": DIRECT_URL,
            "api": "openai-completions",
            "apiKey": PI_API_KEY,
            "models": pi_models(models, " (locale)"),
        },
        "mlx-caveman": {
            "baseUrl": CAVEMAN_URL,
            "api": "openai-completions",
            "apiKey": PI_API_KEY,
            "models": pi_models(models, " (via Caveman)"),
        },
    }
    for key, value in wanted.items():
        current = providers.get(key)
        if current == value:
            continue
        if isinstance(current, dict):
            merged = dict(current)
            merged.update(value)
            providers[key] = merged
        else:
            providers[key] = value
        result["changed"] = True

    if not result["changed"] and not result.get("created"):
        result["state"] = "allineato"
        return result

    if os.path.exists(PI_MODELS):
        backup = f"{PI_MODELS}.bak-models-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(PI_MODELS, backup)
        result["backup"] = backup
    with open(PI_MODELS, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    result["state"] = "aggiornato"
    return result


# ---------------------------------------------------------------- alias

def sync_aliases(models: list[dict]) -> tuple[bool, str]:
    """Scrive il file degli alias per il server MLX."""
    value = aliases_value(models)
    content = (
        "# Generato da sync-models.py: alias \"nome=percorso\" per il server MLX.\n"
        "# Il modello attivo resta il default del server (chiave " + LEGACY_KEY + ").\n"
        f'MLX_LM_MODEL_ALIASES_EXTRA="{value}"\n'
    )
    previous = ""
    if os.path.exists(ALIASES_FILE):
        with open(ALIASES_FILE, encoding="utf-8") as handle:
            previous = handle.read()
    if previous == content:
        return (False, content)
    os.makedirs(CONTROL_DIR, exist_ok=True)
    with open(ALIASES_FILE, "w", encoding="utf-8") as handle:
        handle.write(content)
    return (True, content)


def aliases_value(models: list[dict]) -> str:
    """Valore per MLX_LM_MODEL_ALIASES (nome=percorso separati da virgola).

    La chiave storica non e' inclusa: il server la usa gia' come alias
    implicito del modello attivo (--model), quindi resta sempre valida.
    """
    pairs = [(key, path) for key, path in alias_pairs(models) if key != LEGACY_KEY]
    return ",".join(f"{key}={path}" for key, path in pairs)


# ------------------------------------------------------------------ main

def main() -> int:
    parser = argparse.ArgumentParser(description="Sincronizza i modelli locali con gli agenti")
    parser.add_argument("--check", action="store_true", help="solo verifica: exit 1 se da sincronizzare")
    parser.add_argument("--aliases", action="store_true", help="stampa gli alias per il server MLX")
    parser.add_argument("--json", action="store_true", help="output in JSON")
    parser.add_argument("--target", choices=list(TARGETS), action="append",
                        help="sincronizza solo il target indicato (ripetibile)")
    args = parser.parse_args()

    models = installed_models()
    if not models:
        print("ERR: nessun modello installato in", MODELS_ROOT)
        return 1

    if args.aliases:
        print(aliases_value(models))
        return 0

    targets = args.target or list(TARGETS)

    if args.check:
        # --check non deve scrivere: ricalcoliamo in memoria.
        changed = False
        if "minimax" in targets and os.path.exists(CONFIG_PATH):
            lines = read_lines()
            for provider in PROVIDERS:
                _, provider_changed = sync_provider(lines, provider, models)
                changed = changed or provider_changed
        if "opencode" in targets and os.path.exists(OPENCODE_CONFIG):
            with open(OPENCODE_CONFIG, encoding="utf-8") as handle:
                raw = handle.read()
            try:
                data = json.loads(strip_jsonc(raw))
            except json.JSONDecodeError:
                data = {}
            provider = data.get("provider", {})
            for key, suffix, url in (("mlx", " (locale)", DIRECT_URL),
                                     ("mlx-caveman", " (via Caveman)", CAVEMAN_URL)):
                current = provider.get(key)
                wanted = {
                    "name": "MLX Control (LFM3 locale)" if key == "mlx" else "MLX Control + Caveman (compressione)",
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": url, "apiKey": PI_API_KEY},
                    "models": opencode_models(models, suffix),
                }
                if not isinstance(current, dict):
                    changed = True
                    continue
                if current.get("models") != wanted["models"]:
                    changed = True
        if "pi" in targets and os.path.isdir(os.path.dirname(PI_MODELS)):
            data = {}
            if os.path.exists(PI_MODELS):
                try:
                    with open(PI_MODELS, encoding="utf-8") as handle:
                        data = json.load(handle)
                except json.JSONDecodeError:
                    data = {}
            providers = data.get("providers", {})
            for key, suffix in (("mlx", " (locale)"), ("mlx-caveman", " (via Caveman)")):
                current = providers.get(key)
                if not isinstance(current, dict) or current.get("models") != pi_models(models, suffix):
                    changed = True
        print("da sincronizzare" if changed else "allineato")
        return 1 if changed else 0

    summary: dict = {"targets": {}}
    if "minimax" in targets:
        minimax = sync_config(models)
        summary["targets"]["minimax"] = minimax
        summary["changed"] = summary.get("changed", False) or minimax.get("changed", False)
    if "opencode" in targets:
        opencode = sync_opencode(models)
        summary["targets"]["opencode"] = opencode
        summary["changed"] = summary.get("changed", False) or opencode.get("changed", False)
    if "pi" in targets:
        pi = sync_pi(models)
        summary["targets"]["pi"] = pi
        summary["changed"] = summary.get("changed", False) or pi.get("changed", False)

    aliases_changed, _ = sync_aliases(models)
    summary["aliases"] = "aggiornati" if aliases_changed else "allineati"
    summary["aliases_changed"] = aliases_changed
    summary["changed"] = summary.get("changed", False) or aliases_changed
    summary["models"] = [model["key"] for model in models]
    summary["restart_required"] = aliases_changed
    summary["active"] = next((m["key"] for m in models if m["active"]), "")

    if args.json:
        print(json.dumps(summary))
    else:
        print(f"modelli: {len(models)} ({', '.join(summary['models'])})")
        for name, result in summary["targets"].items():
            state = result.get("state") or result.get("skipped") or result.get("error") or "?"
            print(f"  {name}: {state}")
        print(f"  alias server: {summary['aliases']}")
        if summary["changed"]:
            print("  configurazione aggiornata")
        else:
            print("  tutto allineato")
    return 0


if __name__ == "__main__":
    sys.exit(main())
