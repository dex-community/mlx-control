#!/usr/bin/env python3
"""Proxy "compattazione veloce" per MiniMax Code.

MiniMax Code compatta il contesto chiedendo al *modello di sessione* di
riscrivere la conversazione in un checkpoint: con un modello locale da 1.7B
arrivano a 8.192 token di generazione su un input da oltre 100.000 caratteri,
e l'operazione dura minuti.

Questo proxy si mette davanti al server MLX (127.0.0.1:8080 -> :8081),
riconosce la richiesta di checkpoint e la accorcia:

* modalita' "rizzo": con Rizzo Flow (locale, zero token generati) decide quali
  tool call e quali output sono ancora necessari, butta via quelli obsoleti,
  tronca i restanti e chiede al modello un checkpoint breve;
* modalita' "rizzo-only": nessuna generazione, il checkpoint a otto sezioni
  viene composto direttamente dal materiale compattato (istantaneo);
* modalita' "native": il proxy lascia passare tutto, come MiniMax Code.

Se Rizzo non e' raggiungibile il proxy fa semplicemente passare la richiesta
(troncando comunque gli output dei tool): la sessione continua a funzionare.

Configurazione (file JSON, riletto a ogni richiesta):
  {
    "mode": "rizzo" | "rizzo-only" | "native",
    "max_tokens": 1200,
    "tool_chars": 400,
    "model": "alias-modello-per-il-checkpoint" (opzionale),
    "keepThreshold": 0.5,
    "preserveRecentMessages": 6
  }

Variabili d'ambiente:
  FASTCOMPACT_UPSTREAM   server MLX da usare come upstream (default 127.0.0.1:8080)
  FASTCOMPACT_LISTEN     porta del proxy (default 8081)
  FASTCOMPACT_RIZZO      base URL di Rizzo Flow (default 127.0.0.1:8017)
  FASTCOMPACT_CONFIG     percorso del file di configurazione
  FASTCOMPACT_STATE      file JSON con le statistiche dell'ultima compattazione
"""

from __future__ import annotations

import atexit
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rizzo_compact import (  # noqa: E402
    DEFAULTS,
    RizzoClient,
    build_checkpoint,
    compact,
    message_text,
    prune_messages,
    tool_calls_of,
)

UPSTREAM = os.environ.get("FASTCOMPACT_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
LISTEN = int(os.environ.get("FASTCOMPACT_LISTEN", "8081"))
RIZZO_URL = os.environ.get("FASTCOMPACT_RIZZO", "http://127.0.0.1:8017").rstrip("/")
CONFIG_FILE = os.environ.get(
    "FASTCOMPACT_CONFIG", os.path.expanduser("~/.minimax/local-models/fast-compact.json")
)
STATE_FILE = os.environ.get("FASTCOMPACT_STATE", "")

CHECKPOINT_MARKERS = (
    "Checkpoint control: summarize the preceding conversation now",
    "You are creating a loss-aware checkpoint of a coding-agent conversation",
    "You are a context summarization assistant",
)

BREVITY = (
    "\n\n[Compattazione veloce] Rispondi SOLO con le otto sezioni del protocollo, "
    "in massimo 14 righe in totale. Non ripetere il testo delle tool call: citale per "
    "nome (file, comando, errore) e tieni i dettagli essenziali."
)

DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "rizzo",
    "level": "fast",
    "max_tokens": 1200,
    "tool_chars": 400,
    "model": "",
    "keepThreshold": DEFAULTS["keepThreshold"],
    "preserveRecentMessages": DEFAULTS["preserveRecentMessages"],
    "maxStateTokens": DEFAULTS["maxStateTokens"],
    "maxRequestTokens": DEFAULTS["maxRequestTokens"],
    "truncateHeadChars": DEFAULTS["truncateHeadChars"],
    "rizzo_autostart": True,
    "rizzo_idle_seconds": 900,
}

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Ciclo di vita di Rizzo Flow (on demand)
#
# Rizzo Flow tiene in memoria un modello da 1,7 GB (circa 2 GB di RAM
# residenti). Tenerlo sempre acceso e' costoso per un servizio che serve
# solo durante le compattazioni: qui viene avviato alla prima richiesta e
# spento dopo un periodo di inattivita'. Se l'avvio fallisce il proxy
# ricade sulla compattazione nativa (la sessione continua a funzionare).
# ---------------------------------------------------------------------------

RIZZO_LABEL = "com.minimax.rizzo-flow"
_rizzo_lock = threading.Lock()
_rizzo_last_use = 0.0
_rizzo_state = {"running": False, "started_by_us": False}


def _launchctl(*args: str, timeout: float = 20.0) -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["launchctl", *args], capture_output=True, timeout=timeout, check=False
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _rizzo_healthy(timeout: float = 5.0) -> bool:
    try:
        RizzoClient(RIZZO_URL, timeout=timeout).health()
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_rizzo(autostart: bool = True, timeout: float = 240.0) -> bool:
    """Garantisce che Rizzo Flow sia raggiungibile, avviandolo se serve."""
    global _rizzo_last_use
    with _rizzo_lock:
        if _rizzo_healthy():
            _rizzo_last_use = time.time()
            _rizzo_state["running"] = True
            return True
        if not autostart:
            return False
        uid = os.getuid()
        started = _launchctl("kickstart", "-k", f"gui/{uid}/{RIZZO_LABEL}")
        if not started:
            # Il job potrebbe non essere caricato: lo si ricarica e si ritenta.
            plist = os.path.expanduser(f"~/Library/LaunchAgents/{RIZZO_LABEL}.plist")
            if os.path.exists(plist):
                _launchctl("bootstrap", f"gui/{uid}", plist)
                started = _launchctl("kickstart", "-k", f"gui/{uid}/{RIZZO_LABEL}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2.0)
            if _rizzo_healthy():
                _rizzo_last_use = time.time()
                _rizzo_state["running"] = True
                _rizzo_state["started_by_us"] = True
                return True
        return False


def release_rizzo() -> None:
    """Ferma Rizzo Flow (SIGTERM: il job resta caricato e si puo' riavviare)."""
    with _rizzo_lock:
        uid = os.getuid()
        _launchctl("kill", "SIGTERM", f"gui/{uid}/{RIZZO_LABEL}")
        _rizzo_state["running"] = False
        _rizzo_state["started_by_us"] = False


def _rizzo_idle_watcher() -> None:
    """Spegne Rizzo Flow quando non viene piu' usato da un po'."""
    while True:
        time.sleep(30.0)
        try:
            with _rizzo_lock:
                idle = _rizzo_last_use
                running = _rizzo_state["running"]
            if running and idle and (time.time() - idle) > _rizzo_idle_limit():
                release_rizzo()
        except Exception:  # noqa: BLE001
            continue


def _rizzo_idle_limit() -> float:
    try:
        config = load_config()
        return float(config.get("rizzo_idle_seconds", 900))
    except Exception:  # noqa: BLE001
        return 900.0


# ---------------------------------------------------------------------------
# Memory guard del server MLX
#
# MLX lavora sulla memoria unificata: dopo una compattazione il server tiene
# in memoria i buffer del contesto elaborato e non li restituisce al sistema
# (per liberarli serve riavviarlo). Qui il proxy - che e' il punto di passaggio
# obbligato di MiniMax Code - riavvia il server solo quando non ci sono
# richieste in volo e la memoria supera la soglia configurata.
# ---------------------------------------------------------------------------

MLX_LABEL = "com.minimax.lfm3-mlx-server"
_restart_lock = threading.Lock()
_server_ready = threading.Event()
_server_ready.set()
_inflight = 0
_last_request_end = time.time()


def _server_health_ok(timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"{UPSTREAM}/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def mlx_memory_bytes() -> int:
    """Memoria attiva del server MLX (buffer vivi, non cache)."""
    try:
        with urllib.request.urlopen(f"{UPSTREAM}/admin/clear-cache", timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return int(data.get("active_after") or data.get("active_before") or 0)
    except Exception:  # noqa: BLE001
        return 0


def process_memory_bytes() -> int:
    """Memoria del processo server MLX come la vede il sistema (Metal incluso)."""
    try:
        import subprocess

        found = subprocess.run(
            ["pgrep", "-f", "mlx_lm.server"], capture_output=True, text=True, timeout=10
        ).stdout.split()
        if not found:
            return 0
        out = subprocess.run(
            ["top", "-l", "1", "-pid", found[0], "-stats", "mem"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        # top stampa il valore come "1133M" (o "1,2G") su una riga dedicata.
        for token in reversed(out.replace(",", "").split()):
            match = re.fullmatch(r"([0-9.]+)([KMG])", token)
            if match:
                factor = {"K": 1024, "M": 1024**2, "G": 1024**3}[match.group(2)]
                return int(float(match.group(1)) * factor)
    except Exception:  # noqa: BLE001
        pass
    return 0


def restart_mlx_server(timeout: float = 240.0) -> bool:
    """Riavvia il server MLX (rilascia la memoria) e attende che torni su."""
    global _last_request_end
    with _restart_lock:
        _server_ready.clear()
        try:
            uid = os.getuid()
            ok = _launchctl("kickstart", "-k", f"gui/{uid}/{MLX_LABEL}")
            if not ok:
                plist = os.path.expanduser(f"~/Library/LaunchAgents/{MLX_LABEL}.plist")
                if os.path.exists(plist):
                    _launchctl("bootstrap", f"gui/{uid}", plist)
                    ok = _launchctl("kickstart", "-k", f"gui/{uid}/{MLX_LABEL}")
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(1.0)
                if _server_health_ok():
                    return True
            return False
        finally:
            _server_ready.set()
            _last_request_end = time.time()


def _memory_guard() -> None:
    """Riavvia il server MLX quando e' inattivo e occupa troppa memoria."""
    while True:
        time.sleep(30.0)
        try:
            config = load_config()
            if not bool(config.get("mlx_memory_guard", True)):
                continue
            threshold = int(config.get("mlx_memory_threshold_bytes", 3_500_000_000))
            idle_limit = float(config.get("mlx_memory_guard_idle_seconds", 90))
            with _inflight_lock:
                busy = _inflight > 0
            if busy or (time.time() - _last_request_end) < idle_limit:
                continue
            used = max(mlx_memory_bytes(), process_memory_bytes())
            if used and used > threshold:
                restart_mlx_server()
        except Exception:  # noqa: BLE001
            continue


_inflight_lock = threading.Lock()


def release_upstream_cache() -> bool:
    """Chiede al server MLX di rilasciare la cache di allocazione (mx.clear_cache)."""
    try:
        with urllib.request.urlopen(f"{UPSTREAM}/admin/clear-cache", timeout=15) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            config.update(stored)
    except (OSError, json.JSONDecodeError):
        pass
    return config


def write_state(payload: dict[str, Any]) -> None:
    if not STATE_FILE:
        return
    try:
        with _lock:
            with open(STATE_FILE, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def is_checkpoint(payload: dict[str, Any]) -> bool:
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    return any(marker in blob for marker in CHECKPOINT_MARKERS)


def apply_brevity(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggiunge l'istruzione di brevita' all'ultimo messaggio utente."""
    if not messages:
        return messages
    updated = [dict(message) for message in messages]
    for message in reversed(updated):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = content + BREVITY
        elif isinstance(content, list):
            blocks = [dict(block) if isinstance(block, dict) else block for block in content]
            for block in reversed(blocks):
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = block.get("text", "") + BREVITY
                    break
            message["content"] = blocks
        break
    return updated


def truncate_tool_results(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Tronca i risultati dei tool: sono la parte piu' pesante del contesto."""
    if limit <= 0:
        return messages
    updated: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "tool":
            updated.append(message)
            continue
        text = message_text(message)
        if len(text) <= limit:
            updated.append(message)
            continue
        copy = dict(message)
        copy["content"] = (
            f"{text[:limit]}\n[… output troncato a {limit} caratteri; riesegui il tool se serve]"
        )
        updated.append(copy)
    return updated


def drop_tool_arguments(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Accorcia gli argomenti delle tool call enormi (es. cat di file grossi)."""
    if limit <= 0:
        return messages
    updated: list[dict[str, Any]] = []
    for message in messages:
        calls = tool_calls_of(message)
        if not calls:
            updated.append(message)
            continue
        copy = dict(message)
        rebuilt = []
        for call in calls:
            arguments = json.dumps(call["input"], ensure_ascii=False, default=str)
            if len(arguments) > limit:
                arguments = arguments[:limit] + "…"
            rebuilt.append(
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["tool"], "arguments": arguments},
                }
            )
        copy["tool_calls"] = rebuilt
        updated.append(copy)
    return updated


def accelerate(
    payload: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Ritorna (payload modificato, statistiche, checkpoint già pronto se senza LLM)."""
    global _rizzo_last_use
    stats: dict[str, Any] = {"mode": config.get("mode", "rizzo"), "accelerated": False}
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return payload, stats, None

    mode = str(config.get("mode", "rizzo"))
    if mode == "native":
        stats["rizzo"] = "skipped (modalita' nativa)"
        return payload, stats, None

    tool_chars = int(config.get("tool_chars", 400))
    max_tokens = int(config.get("max_tokens", 1200))
    level = str(config.get("level", "fast"))

    # 1) compattazione con le decisioni di Rizzo
    client = RizzoClient(RIZZO_URL)
    started = time.time()
    result = None
    prune = None
    try:
        if not ensure_rizzo(bool(config.get("rizzo_autostart", True))):
            raise RuntimeError("Rizzo Flow non raggiungibile")
        client.health()
        options = {
            "keepThreshold": config.get("keepThreshold", DEFAULTS["keepThreshold"]),
            "preserveRecentMessages": config.get(
                "preserveRecentMessages", DEFAULTS["preserveRecentMessages"]
            ),
            "maxStateTokens": config.get("maxStateTokens", DEFAULTS["maxStateTokens"]),
            "maxRequestTokens": config.get("maxRequestTokens", DEFAULTS["maxRequestTokens"]),
            "truncateHeadChars": config.get("truncateHeadChars", DEFAULTS["truncateHeadChars"]),
            "digestTextChars": config.get("digestTextChars", 150),
        }
        if level == "deep":
            result = compact(messages, client, options)
            stats["rizzo"] = "ok (livello profondo)"
        else:
            prune = prune_messages(messages, client, options)
            messages = prune["messages"]
            stats["rizzo"] = "ok"
            stats.update(prune["stats"])
    except Exception as error:  # noqa: BLE001
        stats["rizzo"] = f"non disponibile ({type(error).__name__})"
        result = None

    if result is not None or prune is not None:
        _rizzo_last_use = time.time()

    if result is not None:
        stats.update(
            {
                "calls": len(result.decisions),
                "kept": result.kept,
                "dropped": result.dropped,
                "truncated": result.truncated,
                "chars_before": result.chars_before,
                "chars_after": result.chars_after,
                "reduction": round(result.reduction, 4),
                "state_tokens": result.state_tokens,
                "state_stage": result.state_stage,
                "rizzo_seconds": round(result.rizzo_seconds, 2),
            }
        )
        messages = result.messages

    # 2) troncamento aggressivo dei risultati dei tool
    messages = truncate_tool_results(messages, tool_chars)
    messages = drop_tool_arguments(messages, tool_chars * 2)

    payload = dict(payload)
    payload["messages"] = messages

    checkpoint_model = str(config.get("model") or "").strip()
    if checkpoint_model:
        payload["model"] = checkpoint_model
        stats["checkpoint_model"] = checkpoint_model

    original_max = payload.get("max_tokens")
    if not isinstance(original_max, int) or original_max <= 0 or original_max > max_tokens:
        payload["max_tokens"] = max_tokens
    stats["max_tokens"] = payload.get("max_tokens")

    if mode == "rizzo-only":
        # Nessuna generazione: il checkpoint è composto dal materiale compattato.
        checkpoint = build_checkpoint(messages)
        stats["accelerated"] = True
        stats["generated"] = False
        stats["checkpoint_chars"] = len(checkpoint)
        stats["elapsed"] = round(time.time() - started, 2)
        stats["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return payload, stats, checkpoint

    payload["messages"] = apply_brevity(messages)
    stats["accelerated"] = True
    stats["generated"] = True
    stats["elapsed"] = round(time.time() - started, 2)
    stats["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return payload, stats, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FastCompact/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/memory", "/fastcompact/memory"):
            # Memoria del server MLX: quanto occupa davvero (Metal incluso).
            body = json.dumps(
                {
                    "mlx_process_bytes": process_memory_bytes(),
                    "mlx_active_bytes": mlx_memory_bytes(),
                    "rizzo_running": _rizzo_healthy(timeout=2.0),
                    "threshold_bytes": int(
                        load_config().get("mlx_memory_threshold_bytes", 3_500_000_000)
                    ),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.rstrip("/") in ("/health", "/fastcompact/health"):
            config = load_config()
            body = json.dumps(
                {
                    "status": "ok",
                    "upstream": UPSTREAM,
                    "rizzo": RIZZO_URL,
                    "mode": config.get("mode"),
                    "level": config.get("level"),
                    "max_tokens": config.get("max_tokens"),
                    "tool_chars": config.get("tool_chars"),
                    "checkpoint_model": config.get("model"),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._forward()

    # -- POST --------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        global _inflight, _last_request_end
        with _inflight_lock:
            _inflight += 1
        try:
            self._handle_post()
        finally:
            with _inflight_lock:
                _inflight -= 1
                _last_request_end = time.time()

    def _handle_post(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None

        stats: dict[str, Any] = {}
        if isinstance(payload, dict) and is_checkpoint(payload):
            payload, stats, checkpoint = accelerate(payload, load_config())
            write_state(stats)
            if checkpoint is not None:
                self._send_synthetic(checkpoint, payload)
                return

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else raw
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length", "connection")
        }
        headers["Content-Length"] = str(len(body))
        self._send_upstream(body, headers)
        # A compattazione finita il server MLX tiene ancora in memoria i buffer
        # del contesto elaborato: glieli si restituisce subito.
        release_upstream_cache()

    # -- risposta sintetica (checkpoint senza generazione) ------------------
    def _send_synthetic(self, content: str, payload: dict[str, Any]) -> None:
        model = payload.get("model") if isinstance(payload, dict) else "local"
        created = int(time.time())
        if isinstance(payload, dict) and payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(delta: dict[str, Any], finish: str | None = None) -> None:
                frame = {
                    "id": "chatcmpl-fastcompact",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                data = ("data: " + json.dumps(frame) + "\n\n").encode("utf-8")
                self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
                self.wfile.flush()

            chunk({"role": "assistant", "content": ""})
            chunk({"content": content})
            chunk({}, "stop")
            # [DONE] deve viaggiare dentro un chunk chunked come gli altri.
            done = b"data: [DONE]\n\n"
            self.wfile.write(b"%X\r\n" % len(done) + done + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return

        body = json.dumps(
            {
                "id": "chatcmpl-fastcompact",
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- trasporto ---------------------------------------------------------
    def _forward(self) -> None:
        body = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length", "connection")
        }
        headers["Content-Length"] = str(len(body))
        self._send_upstream(body, headers)

    def _send_upstream(self, body: bytes, headers: dict[str, str]) -> None:
        url = f"{UPSTREAM}{self.path}"
        request = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        # Se il memory guard ha avviato un riavvio del server, si aspetta.
        _server_ready.wait(240)
        attempt = 0
        while True:
            attempt += 1
            try:
                with urllib.request.urlopen(request, timeout=1800) as response:
                    content_type = response.headers.get("Content-Type", "application/json")
                    self.send_response(response.status)
                    self.send_header("Content-Type", content_type)
                    if "text/event-stream" in content_type:
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        while True:
                            chunk = response.read(4096)
                            if not chunk:
                                break
                            self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                            self.wfile.flush()
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                    else:
                        data = response.read()
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                return
            except urllib.error.HTTPError as error:
                data = error.read()
                self.send_response(error.code)
                self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as error:  # noqa: BLE001
                # Il server era in riavvio: si aspetta che torni e si ritenta.
                if attempt < 3 and not _server_health_ok():
                    for _ in range(120):
                        if _server_health_ok():
                            break
                        time.sleep(1.0)
                    if _server_health_ok():
                        continue
                data = json.dumps({"error": {"message": f"fast-compact upstream error: {error}"}}).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN), Handler)
    server.daemon_threads = True
    threading.Thread(target=_rizzo_idle_watcher, daemon=True).start()
    threading.Thread(target=_memory_guard, daemon=True).start()
    atexit.register(_release_rizzo_if_ours)
    sys.stderr.write(f"fast-compact: ascolto su http://127.0.0.1:{LISTEN} -> {UPSTREAM}\n")
    sys.stderr.flush()
    server.serve_forever()


def _release_rizzo_if_ours() -> None:
    """Alla chiusura del proxy libera Rizzo solo se lo aveamo avviato noi."""
    try:
        if _rizzo_state.get("started_by_us"):
            release_rizzo()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
