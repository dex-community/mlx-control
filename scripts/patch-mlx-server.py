#!/usr/bin/env python3
"""Applica a mlx_lm/server.py le patch di MLX Control (idempotente).

Le patch servono a rendere il server MLX adatto a MiniMax Code:

 1. alias dei modelli           MLX_LM_MODEL_ALIASES="nome=percorso,nome2"
 2. parametri di campionamento  MLX_LM_DEFAULT_TEMPERATURE/TOP_K/MIN_P/REPETITION_PENALTY
 3. endpoint /v1/responses/input_tokens (conteggio token per i client OpenAI)
 4. rimozione dei tag <think> dall'output
 5. hint di sistema opzionale   MLX_SYSTEM_HINT
 6. riparazione degli argomenti dei tool secondo lo schema dichiarato

Il punto 6 risolve un problema reale: i modelli piccoli (1.7B) emettono
argomenti dei tool quasi corretti ma con forme sbagliate — per esempio
`options: ["a", "b"]` invece di una lista di oggetti, oppure un intero array
serializzato come stringa JSON. Il runtime rifiuta la chiamata e il modello
ripete la stessa domanda all'infinito. Qui gli argomenti vengono riparati
prima di uscire dal server.

Uso:  patch-mlx-server.py [--venv DIR] [--check] [--force]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

REPAIR_CODE = '''
# --- Patched by MLX Control: tool argument repair ---------------------------
def _mlx_tool_schema(tools, tool_name):
    """Schema JSON del tool richiesto (formato OpenAI tool definitions)."""
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if function.get("name") != tool_name:
            continue
        return function.get("parameters") or {}
    return {}


def _mlx_resolve_ref(root, ref):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _mlx_schema_type(schema):
    if not isinstance(schema, dict):
        return None
    if isinstance(schema.get("type"), str):
        return schema["type"]
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(key) or []:
            found = _mlx_schema_type(branch)
            if found:
                return found
    return None


def _mlx_extract_json(text):
    """Estrae il primo blocco JSON bilanciato da una stringa."""
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except Exception:  # noqa: BLE001
                        break
    return None


def _mlx_coerce_json(value):
    """Trasforma una stringa che contiene JSON nel valore corrispondente."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text[:1] not in ("[", "{"):
        return value
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        parsed = _mlx_extract_json(text)
    if isinstance(parsed, (list, dict)):
        return parsed
    return value


def _mlx_slug(text):
    out = []
    for char in str(text).strip().lower():
        if char.isalnum():
            out.append(char)
        elif char in " -_/" and out and out[-1] != "_":
            out.append("_")
    return ("".join(out)[:64]) or "option"


def _mlx_repair(value, schema, root):
    if not isinstance(schema, dict):
        return value
    if "$ref" in schema:
        resolved = _mlx_resolve_ref(root, schema["$ref"])
        if resolved:
            schema = resolved
    kind = _mlx_schema_type(schema)
    value = _mlx_coerce_json(value)
    if kind == "array":
        if isinstance(value, list):
            items = schema.get("items") or {}
            return [_mlx_repair(item, items, root) for item in value]
        return value
    if kind == "object":
        properties = schema.get("properties") or {}
        if isinstance(value, dict):
            repaired = {}
            for key, item in value.items():
                child = properties.get(key)
                repaired[key] = _mlx_repair(item, child, root) if child else item
            if schema.get("additionalProperties") is False and properties:
                repaired = {k: v for k, v in repaired.items() if k in properties}
            return repaired
        if isinstance(value, str):
            text = value.strip()
            if not properties:
                return {"label": text}
            if "label" in properties:
                obj = {"label": text}
                if "id" in properties:
                    obj["id"] = _mlx_slug(text)
                if "description" in properties:
                    obj["description"] = text
                return obj
            first = next(iter(properties))
            obj = {first: text}
            if "id" in properties and first != "id":
                obj["id"] = _mlx_slug(text)
            return obj
    return value


def repair_tool_arguments(arguments, tool_name, tools):
    """Allinea gli argomenti generati dal modello allo schema del tool."""
    if not isinstance(arguments, dict):
        return arguments
    schema = _mlx_tool_schema(tools, tool_name)
    if not schema:
        return arguments
    try:
        return _mlx_repair(arguments, schema, schema)
    except Exception:  # noqa: BLE001
        return arguments
# --- end MLX Control patch ----------------------------------------------------
'''

PATCHES: list[tuple[str, str, str, str]] = [
    # (nome, ancora, testo, "after" | "replace")
    (
        "import os",
        "import json\n",
        "import os\n",
        "after",
    ),
    (
        "alias map",
        '        self._model_map["default_model"] = self.cli_args.model\n',
        """        # MLX Control: alias dei modelli ("nome" o "nome=percorso").
        for _alias in (os.environ.get("MLX_LM_MODEL_ALIASES") or "").split(","):
            _alias = _alias.strip()
            if not _alias:
                continue
            if "=" in _alias:
                _name, _path = _alias.split("=", 1)
                _name = _name.strip()
                _path = _path.strip()
                if _name and _path:
                    self._model_map[_name] = _path
            else:
                self._model_map[_alias] = self.cli_args.model
""",
        "after",
    ),
    (
        "alias in /v1/models",
        '        response = {"object": "list", "data": models}\n',
        """        # MLX Control: alias esposti anche nell'elenco dei modelli.
        for _alias in (os.environ.get("MLX_LM_MODEL_ALIASES") or "").split(","):
            _alias = _alias.strip()
            if _alias:
                models.append(
                    {
                        "id": _alias.split("=", 1)[0].strip(),
                        "object": "model",
                        "created": self.created,
                    }
                )
""",
        "after",
    ),
    (
        "endpoint input_tokens",
        "        if self.path not in request_factories:\n",
        """        # MLX Control: conteggio token in stile OpenAI Responses.
        if self.path.split("?")[0] == "/v1/responses/input_tokens":
            self.handle_input_tokens_request()
            return

""",
        "before",
    ),
    (
        "sampling da env",
        """        self.temperature = self.body.get(
            "temperature", self.response_generator.cli_args.temp
        )
        self.top_p = self.body.get("top_p", self.response_generator.cli_args.top_p)
        self.top_k = self.body.get("top_k", self.response_generator.cli_args.top_k)
        self.min_p = self.body.get("min_p", self.response_generator.cli_args.min_p)
        self.repetition_penalty = self.body.get("repetition_penalty", 0.0)
""",
        """        # MLX Control: valori di default dei parametri di campionamento da env.
        _env = os.environ.get
        self.temperature = self.body.get(
            "temperature",
            float(_env("MLX_LM_DEFAULT_TEMPERATURE", self.response_generator.cli_args.temp)),
        )
        self.top_p = self.body.get(
            "top_p", float(_env("MLX_LM_DEFAULT_TOP_P", self.response_generator.cli_args.top_p))
        )
        self.top_k = self.body.get(
            "top_k", int(_env("MLX_LM_DEFAULT_TOP_K", self.response_generator.cli_args.top_k))
        )
        self.min_p = self.body.get(
            "min_p", float(_env("MLX_LM_DEFAULT_MIN_P", self.response_generator.cli_args.min_p))
        )
        self.repetition_penalty = self.body.get(
            "repetition_penalty", float(_env("MLX_LM_DEFAULT_REPETITION_PENALTY", 0.0))
        )
""",
        "replace",
    ),
    (
        "hint di sistema",
        """        return CompletionRequest(
            "chat",
            "",
            body["messages"],
            body.get("tools") or None,
            body.get("role_mapping"),
        )
""",
        """        # MLX Control: hint di sistema opzionale (env MLX_SYSTEM_HINT).
        hint = os.environ.get("MLX_SYSTEM_HINT", "").strip()
        messages = body["messages"]
        if hint and not (
            messages
            and messages[0].get("role") == "system"
            and hint in str(messages[0].get("content", ""))
        ):
            messages = [{"role": "system", "content": hint}] + list(messages)

        return CompletionRequest(
            "chat",
            "",
            messages,
            body.get("tools") or None,
            body.get("role_mapping"),
        )
""",
        "replace",
    ),
    (
        "metodo input_tokens",
        "    def handle_models_request(self):\n",
        """    def handle_input_tokens_request(self):
        \"\"\"Conteggio token della richiesta, in stile OpenAI Responses.\"\"\"
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode() or "{}")
        except Exception as e:  # noqa: BLE001
            logging.warning(f"input_tokens: cannot parse body ({e})")
            body = {}

        texts = []

        def collect(value):
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for key in ("text", "input_text", "content", "output_text"):
                    if key in value:
                        collect(value[key])

        if body.get("instructions"):
            texts.append(body["instructions"])
        if "input" in body:
            collect(body["input"])
        if not texts and "messages" in body:
            collect(body["messages"])
        if body.get("tools"):
            try:
                texts.append(json.dumps(body["tools"]))
            except Exception:  # noqa: BLE001
                pass

        text = chr(10).join(t for t in texts if t)
        count = 0
        try:
            tokenizer = self.response_generator.model_provider.tokenizer
            count = len(tokenizer.encode(text))
        except Exception as e:  # noqa: BLE001
            logging.warning(f"input_tokens: tokenizer unavailable ({e})")
            count = max(1, len(text) // 4)

        self._set_completion_headers(200)
        self.end_headers()
        self.wfile.write(json.dumps({"input_tokens": count}).encode())
        self.wfile.flush()

""",
        "before",
    ),
    (
        "endpoint clear_cache",
        "        if self.path not in request_factories:\n",
        """        # MLX Control: rilascio dei buffer MLX non piu' usati.
        if self.path.split("?")[0] == "/admin/clear-cache":
            self.handle_clear_cache_request()
            return

""",
        "before",
    ),
    (
        "endpoint clear_cache GET",
        "        if self.path.startswith(\"/v1/models\"):\n",
        """        # MLX Control: rilascio dei buffer MLX anche su GET.
        if self.path.split(\"?\")[0] == "/admin/clear-cache":
            self.handle_clear_cache_request()
            return

""",
        "before",
    ),
    (
        "metodo clear_cache",
        "    def handle_models_request(self):\n",
        """    def handle_clear_cache_request(self):
        \"\"\"Rilascia la cache di allocazione MLX (memoria unificata).

        Dopo una compattazione il server tiene in memoria i buffer del
        contesto elaborato: mx.clear_cache() li restituisce al sistema.
        \"\"\"
        try:
            import mlx.core as mx

            before = {
                "active": mx.get_active_memory(),
                "cache": mx.get_cache_memory(),
                "peak": mx.get_peak_memory(),
            }
            mx.clear_cache()
            payload = {
                "cleared": True,
                "active_before": before["active"],
                "cache_before": before["cache"],
                "peak_before": before["peak"],
                "active_after": mx.get_active_memory(),
                "cache_after": mx.get_cache_memory(),
            }
        except Exception as e:  # noqa: BLE001
            logging.warning(f"clear_cache: fallito ({e})")
            payload = {"cleared": False, "error": str(e)}

        self._set_completion_headers(200)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
        self.wfile.flush()

""",
        "before",
    ),
    (
        "rimozione <think>",
        '        choice = response["choices"][0]\n',
        """        # MLX Control: via i tag <think> residui dall'output.
        if text and "<" in text:
            if "</think>" in text:
                text = text.rsplit("</think>", 1)[-1]
            text = text.replace("<think>", "").replace("</think>", "").strip()

""",
        "after",
    ),
    (
        "riparazione argomenti tool",
        '        tc["arguments"] = json.dumps(tc["arguments"], ensure_ascii=False)\n',
        """        # MLX Control: allinea gli argomenti allo schema del tool.
        try:
            tc["arguments"] = repair_tool_arguments(
                tc.get("arguments"), tc.get("name"), self._tools
            )
        except Exception:  # noqa: BLE001
            pass
""",
        "after",
    ),
]

REPAIR_ANCHOR = "def get_system_fingerprint():"


def find_server(venv: str) -> str:
    base = os.path.join(venv, "lib")
    for root, dirs, files in os.walk(base):
        if "server.py" in files and os.path.basename(root) == "mlx_lm":
            return os.path.join(root, "server.py")
    raise SystemExit(f"ERR: mlx_lm/server.py non trovato in {venv}")


def apply_patches(source: str) -> tuple[str, list[str]]:
    applied: list[str] = []
    for name, anchor, addition, mode in PATCHES:
        if addition.strip() and addition.strip() in source:
            continue  # già applicata
        if anchor not in source:
            print(f"   ! ancora non trovato per '{name}': {anchor.strip()[:60]}")
            continue
        if mode == "replace":
            source = source.replace(anchor, addition, 1)
        elif mode == "before":
            source = source.replace(anchor, addition + anchor, 1)
        else:
            source = source.replace(anchor, anchor + addition, 1)
        applied.append(name)

    if "def repair_tool_arguments(" not in source:
        if REPAIR_ANCHOR not in source:
            print("   ! ancora per il codice di riparazione non trovato")
        else:
            source = source.replace(REPAIR_ANCHOR, REPAIR_CODE.strip() + "\n\n\n" + REPAIR_ANCHOR, 1)
            applied.append("codice di riparazione")
    return source, applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Applica le patch MLX Control a mlx_lm")
    parser.add_argument("--venv", default=os.environ.get("LFM3_INSTALL_DIR", "") + "/mlxenv")
    parser.add_argument("--check", action="store_true", help="mostra solo lo stato")
    parser.add_argument("--force", action="store_true", help="riparti dal file originale")
    args = parser.parse_args()

    venv = os.path.expanduser(args.venv)
    server = find_server(venv)
    print(f"→ {server}")

    backup = server + ".mlxcontrol-orig"
    if args.force and os.path.exists(backup):
        shutil.copy2(backup, server)
        print("   ripristinato il file originale")
    if not os.path.exists(backup):
        shutil.copy2(server, backup)

    source = open(server, encoding="utf-8").read()
    patched, applied = apply_patches(source)
    if args.check:
        print("   patch applicate:", ", ".join(applied) or "(nessuna)")
        return 0
    if patched != source:
        with open(server, "w", encoding="utf-8") as handle:
            handle.write(patched)
        print("   applicate:", ", ".join(applied))
    else:
        print("   già tutto applicato")
    # Verifica di sintassi.
    compile(patched, server, "exec")
    print("   sintassi OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
