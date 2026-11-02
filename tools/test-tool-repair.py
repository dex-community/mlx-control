#!/usr/bin/env python3
"""Test della riparazione degli argomenti dei tool (schema ask_user reale)."""
import importlib.util
import json
import sys

SERVER = sys.argv[1] if len(sys.argv) > 1 else (
    "/Users/io/.minimax/local-models/mlxenv/lib/python3.12/site-packages/mlx_lm/server.py"
)

spec = importlib.util.spec_from_file_location("mlx_server", SERVER)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except Exception as exc:  # il modulo importa mlx: carichiamo solo le funzioni
    print(f"(import completo non riuscito: {exc})")

repair = getattr(module, "repair_tool_arguments", None)
if repair is None:
    # Estrai le funzioni dal sorgente ed eseguile da sole.
    source = open(SERVER, encoding="utf-8").read()
    start = source.index("# --- Patched by MLX Control: tool argument repair")
    end = source.index("# --- end MLX Control patch")
    namespace = {"json": json}
    exec(source[start:end], namespace)
    repair = namespace["repair_tool_arguments"]

OPTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "label": {"type": "string"},
        "description": {"type": "string"},
        "image": {"type": "object"},
    },
    "required": ["label"],
}
STEP = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "header": {"type": "string"},
        "question": {"type": "string"},
        "description": {"type": "string"},
        "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": OPTION},
        "selectionMode": {"anyOf": [{"type": "string", "const": "single"}, {"type": "string", "const": "multiple"}]},
    },
    "required": ["question", "options"],
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mode": {"type": "string"},
        "async": {"type": "boolean"},
        "title": {"type": "string"},
        "steps": {"type": "array", "minItems": 1, "maxItems": 4, "items": STEP},
    },
    "required": ["steps"],
}
TOOLS = [{"type": "function", "function": {"name": "ask_user", "parameters": SCHEMA}}]

# Caso 1: options come stringhe (primo tentativo del modello).
caso1 = {
    "title": "Scrivi un testo breve",
    "steps": [
        {
            "id": "1",
            "description": "Chiedere quale programma.",
            "question": "Quale programma vuoi che spieghi?",
            "options": ["dev1/cli", "dev1/researchVirtualDog", "intero progetto cavalli"],
        }
    ],
    "mode": "questionnaire",
}
# Caso 2: steps serializzato come stringa JSON (secondo tentativo).
caso2 = {
    "title": "Scrivi un testo breve",
    "steps": json.dumps(
        [
            {
                "id": "1",
                "description": "Chiedere quale programma.",
                "question": "Quale programma vuoi che spieghi?",
                "options": [
                    {"id": "a", "label": "dev1/cli", "description": "Il CLI del progetto"},
                    {"id": "b", "label": "non specificato", "description": "Non specificato"},
                ],
                "prompt": "Seleziona una delle opzioni:",
                "mode": "questionnaire",
            }
        ],
        ensure_ascii=False,
    ),
    "async": True,
}

ok = True
for nome, caso in (("options come stringhe", caso1), ("steps come stringa JSON", caso2)):
    out = repair(caso, "ask_user", TOOLS)
    print(f"--- {nome} ---")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:700])
    steps = out.get("steps")
    assert isinstance(steps, list), f"{nome}: steps non è una lista"
    assert steps, f"{nome}: steps vuota"
    for step in steps:
        assert isinstance(step, dict), f"{nome}: step non è un oggetto"
        assert isinstance(step.get("question"), str), f"{nome}: question mancante"
        opts = step.get("options")
        assert isinstance(opts, list) and len(opts) >= 2, f"{nome}: options non valide"
        for opt in opts:
            assert isinstance(opt, dict), f"{nome}: option non è un oggetto"
            assert isinstance(opt.get("label"), str) and opt["label"], f"{nome}: option senza label"
    print(f"    -> OK\n")

print("TUTTI I CASI PASSANO")
