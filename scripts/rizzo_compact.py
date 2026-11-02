#!/usr/bin/env python3
"""Compattazione "fast-jev" locale: le decisioni tipizzate di Rizzo Flow al posto
del riassunto LLM.

Porting in Python dell'algoritmo di `fast-jev-compaction`
(https://github.com/tamaratran/fast-jev-compaction, licenza MIT, fork
aleksvega) adattato al formato messaggi OpenAI Chat Completions e con
l'endpoint Jev-compatibile di Rizzo Flow (POST /v1/systemone) al posto
dell'API cloud TypeSafe.

Il punto: il riassunto di contesto di MiniMax Code costa una generazione
lunghissima (fino a `maxTokens` token dal modello di sessione). Qui invece si
chiede a Rizzo, che non genera un solo token, quali tool call e quali output
sono ancora necessari: si buttano quelli obsoleti e si lascia il resto
verbatim. Il risultato è una cronologia compattata, non un riassunto.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Opzioni (stesse semantica e nomi del pacchetto originale)
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "keepThreshold": 0.5,
    "preserveRecentMessages": 6,
    "maxStateTokens": 25_000,
    "maxRequestTokens": 30_000,
    "truncateHeadChars": 300,
}

STATE_CONTEXT = (
    "A coding assistant conversation is being compacted to free context. `history` is the whole "
    "conversation so far, oldest first; tool outputs are replaced by a short `result` note and long "
    "texts may be abridged. Each question asks whether one tool call, or the full output of that "
    "call, still needs to stay in the history verbatim. Whatever is not kept is deleted permanently, "
    "but the assistant can always re-run a tool or re-read a file."
)

INPUT_CHARS = (1000, 200, 60)
TEXT_HEAD = 400
TEXT_TAIL = 150
REQUEST_OVERHEAD_TOKENS = 20

_TOKEN_PIECES = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


def estimate_tokens(text: str) -> int:
    """Stima dei token senza tokenizer (stessa euristica dell'originale)."""
    tokens = 0.0
    for piece in _TOKEN_PIECES.findall(text):
        first = ord(piece[0])
        if 48 <= first <= 57:
            tokens += len(piece) / 2
        elif 65 <= first <= 90 or 97 <= first <= 122:
            tokens += 1 + (len(piece) - 1) // 6
        else:
            tokens += 0.9
    return int(-(-tokens // 1))


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def abridge(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail + 40:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n[… {omitted} chars omitted …]\n{text[-tail:]}"


# ---------------------------------------------------------------------------
# Normalizzazione dei messaggi OpenAI
# ---------------------------------------------------------------------------


CONTROL_MARKERS = ("Checkpoint control:", "<checkpoint-protocol>", "Return only the eight checkpoint sections")


def is_control_message(text: str) -> bool:
    """Il messaggio di controllo del checkpoint non e' una richiesta dell'utente."""
    return any(marker in text for marker in CONTROL_MARKERS)


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def tool_calls_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw_args = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError, ValueError):
            arguments = {"_raw": str(raw_args)[:400]}
        calls.append(
            {
                "id": call.get("id") or "",
                "tool": function.get("name") or "tool",
                "input": arguments,
            }
        )
    return calls


@dataclass
class ToolCall:
    id: str
    tool_use_id: str
    tool: str
    input: dict[str, Any]
    call_index: int
    result_index: int
    result_chars: int
    is_error: bool
    pinned: bool = False


def is_pinned(index: int, total: int, preserve_recent_messages: int) -> bool:
    return index == 0 or index >= total - preserve_recent_messages


def collect_tool_calls(messages: list[dict[str, Any]], preserve_recent_messages: int) -> list[ToolCall]:
    """Abbina ogni tool_call al suo risultato tramite tool_call_id."""
    results: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, message in enumerate(messages):
        if message.get("role") == "tool" and message.get("tool_call_id"):
            results[message["tool_call_id"]] = (index, message)

    calls: list[ToolCall] = []
    for call_index, message in enumerate(messages):
        for call in tool_calls_of(message):
            found = results.get(call["id"])
            if not found:
                continue
            result_index, result = found
            text = message_text(result)
            calls.append(
                ToolCall(
                    id=f"t{len(calls) + 1}",
                    tool_use_id=call["id"],
                    tool=call["tool"],
                    input=call["input"],
                    call_index=call_index,
                    result_index=result_index,
                    result_chars=len(text),
                    is_error="error" in (result.get("name") or "").lower()
                    or text.lstrip().lower().startswith("error"),
                    pinned=is_pinned(call_index, len(messages), preserve_recent_messages)
                    or is_pinned(result_index, len(messages), preserve_recent_messages),
                )
            )
    return calls


def goal_from_messages(messages: list[dict[str, Any]]) -> str:
    """Le ultime tre richieste dell'utente, come goal di default."""
    users = [
        message
        for message in messages
        if message.get("role") == "user"
        and message_text(message).strip()
        and not is_control_message(message_text(message))
        and not message.get("tool_calls")
    ]
    return "\n".join(truncate(message_text(message), 500) for message in users[-3:])


# ---------------------------------------------------------------------------
# Stato per Rizzo
# ---------------------------------------------------------------------------


def _input_text(value: Any, limit: int) -> str:
    try:
        as_json = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        as_json = "[unserializable input]"
    return truncate(as_json, limit)


def _result_note(call: ToolCall) -> str:
    return f"{'error' if call.is_error else 'ok'}, {call.result_chars} chars (omitted)"


def _compact_call(call: ToolCall) -> str:
    parts = []
    for key, value in call.input.items():
        text = value if isinstance(value, str) else _input_text({key: value}, 200)
        flat = _WHITESPACE.sub(" ", text)
        parts.append(f"{key}={flat}")
    joined = " ".join(parts)
    return f"{call.id} {call.tool} {truncate(joined, INPUT_CHARS[2])} → {'error' if call.is_error else 'ok'} {call.result_chars}ch"


def _calls_by_message(calls: Iterable[ToolCall]) -> dict[int, list[ToolCall]]:
    by_message: dict[int, list[ToolCall]] = {}
    for call in calls:
        by_message.setdefault(call.call_index, []).append(call)
    return by_message


def _history_entries(
    messages: list[dict[str, Any]], calls: list[ToolCall], input_chars: int
) -> list[dict[str, Any]]:
    by_message = _calls_by_message(calls)
    entries: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        message_calls = by_message.get(index, [])
        entry_calls = [
            {
                "id": call.id,
                "tool": call.tool,
                "input": _input_text(call.input, input_chars),
                "result": _result_note(call),
            }
            for call in message_calls
        ]
        text = message_text(message)
        if not text.strip() and not entry_calls:
            continue
        entry: dict[str, Any] = {"i": index, "role": message.get("role", "user"), "text": text}
        if entry_calls:
            entry["tool_calls"] = entry_calls
        entries.append(entry)
    return entries


def fit_state(
    messages: list[dict[str, Any]],
    calls: list[ToolCall],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Riduce lo stato finché entra nel budget di token."""
    goal = options.get("goal") or goal_from_messages(messages)
    max_state_tokens = options["maxStateTokens"]
    preserve_recent_messages = options["preserveRecentMessages"]

    def state_of(history: list[dict[str, Any]]) -> dict[str, Any]:
        return {"context": STATE_CONTEXT, "goal": goal, "history": history}

    def entry_tokens(entry: dict[str, Any]) -> int:
        return estimate_tokens(json.dumps(entry, ensure_ascii=False, default=str)) + 1

    base_tokens = estimate_tokens(json.dumps(state_of([]), ensure_ascii=False, default=str))
    history: list[dict[str, Any]] = []
    per_entry: list[int] = []
    tokens = 0

    def rebuild(input_chars: int) -> None:
        nonlocal history, per_entry, tokens
        history = _history_entries(messages, calls, input_chars)
        per_entry = [entry_tokens(entry) for entry in history]
        tokens = base_tokens + sum(per_entry)

    def fits() -> bool:
        return tokens <= max_state_tokens

    def shrink(index: int, change) -> None:
        nonlocal tokens
        if index >= len(history):
            return
        change(history[index])
        now = entry_tokens(history[index])
        tokens += now - per_entry[index]
        per_entry[index] = now

    rebuild(INPUT_CHARS[0])
    if fits():
        return {"state": state_of(history), "tokens": tokens, "stage": "full"}

    for limit in INPUT_CHARS[1:]:
        rebuild(limit)
        if fits():
            return {"state": state_of(history), "tokens": tokens, "stage": f"inputs<={limit}"}

    def pinned(entry: dict[str, Any]) -> bool:
        return is_pinned(entry["i"], len(messages), preserve_recent_messages)

    indices = list(range(len(history)))
    order = [i for i in indices if not pinned(history[i])] + [i for i in indices if pinned(history[i])]

    for index in order:
        entry = history[index]
        if len(entry["text"]) <= TEXT_HEAD + TEXT_TAIL + 40:
            continue
        shrink(index, lambda e: e.__setitem__("text", abridge(e["text"], TEXT_HEAD, TEXT_TAIL)))
        if fits():
            return {"state": state_of(history), "tokens": tokens, "stage": "texts abridged"}

    for index in order:
        entry = history[index]
        if pinned(entry) or not entry["text"]:
            continue
        original = len(message_text(messages[entry["i"]]))
        shrink(index, lambda e: e.__setitem__("text", f"[… {original} chars omitted …]"))
        if fits():
            return {"state": state_of(history), "tokens": tokens, "stage": "old messages collapsed"}

    by_message = _calls_by_message(calls)
    for index in order:
        entry = history[index]
        own = by_message.get(entry["i"])
        if pinned(entry) or not own:
            continue
        shrink(index, lambda e: e.__setitem__("tool_calls", [_compact_call(call) for call in own]))
        if fits():
            return {"state": state_of(history), "tokens": tokens, "stage": "old calls compacted"}

    left: set[int] = set()
    for index in order:
        entry = history[index]
        if pinned(entry) or entry.get("tool_calls"):
            continue
        left.add(index)
        tokens -= per_entry[index]
        if fits():
            kept = [e for i, e in enumerate(history) if i not in left]
            return {"state": state_of(kept), "tokens": tokens, "stage": "old messages left out"}

    kept = [e for i, e in enumerate(history) if i not in left]
    merged = _merge_call_runs(kept, pinned)
    per_entry = [entry_tokens(entry) for entry in merged]
    tokens = base_tokens + sum(per_entry)
    if fits():
        return {"state": state_of(merged), "tokens": tokens, "stage": "old calls merged"}
    raise ValueError(f"storia troppo grande per Rizzo (~{tokens} token, limite {max_state_tokens})")


def _merge_call_runs(history: list[dict[str, Any]], pinned) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for entry in history:
        previous = merged[-1] if merged else None
        foldable = (
            lambda e: not pinned(e)
            and not e.get("text")
            and isinstance(e.get("tool_calls"), list)
            and e["tool_calls"]
            and isinstance(e["tool_calls"][0], str)
        )
        if previous is not None and foldable(previous) and foldable(entry) and previous["role"] == entry["role"]:
            previous["tool_calls"] = previous["tool_calls"] + entry["tool_calls"]
            continue
        merged.append(dict(entry))
    return merged


# ---------------------------------------------------------------------------
# Client Rizzo Flow (endpoint Jev-compatibile)
# ---------------------------------------------------------------------------


class RizzoClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8017", model: str = "rizzo-latest", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"model": self.model, "state": state, "questions": questions}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/systemone",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("risposta Rizzo senza 'answers'")
        return answers

    def noul(self, answers: dict[str, Any], name: str) -> float:
        answer = answers.get(name)
        if not isinstance(answer, dict) or not isinstance(answer.get("noul"), (int, float)):
            raise ValueError(f"risposta Rizzo non valida per {name}")
        return float(answer["noul"])


# ---------------------------------------------------------------------------
# Compattazione
# ---------------------------------------------------------------------------


def _questions_for(call: ToolCall) -> dict[str, Any]:
    return {
        f"call_{call.id}": {
            "type": "noul",
            "instructions": (
                f"Tool call {call.id} ({call.tool}) should stay in the history: knowing this call was "
                "made, with its input, still matters for what the assistant does next"
            ),
        },
        f"result_{call.id}": {
            "type": "noul",
            "instructions": (
                f"The full output of tool call {call.id} ({call.tool}, {call.result_chars} chars) should "
                "stay in the history verbatim: the assistant still needs its contents and re-running the "
                "tool would not do"
            ),
        },
    }


def _batch_calls(calls: list[ToolCall], state_tokens: int, options: dict[str, Any]) -> list[list[ToolCall]]:
    budget = options["maxRequestTokens"] - state_tokens - REQUEST_OVERHEAD_TOKENS
    batches: list[list[ToolCall]] = []
    current: list[ToolCall] = []
    current_tokens = 0
    for call in calls:
        tokens = estimate_tokens(json.dumps(_questions_for(call), ensure_ascii=False))
        if current and current_tokens + tokens > budget:
            batches.append(current)
            current = []
            current_tokens = 0
        if not current and tokens > budget:
            raise ValueError(
                f"lo stato non lascia spazio per le domande (~{state_tokens} di "
                f"{options['maxRequestTokens']} token)"
            )
        current.append(call)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def _decide(call: ToolCall, keep_call: float, keep_result: float, threshold: float) -> dict[str, Any]:
    if keep_result >= threshold:
        return {"id": call.id, "action": "keep", "reason": "kept"}
    if keep_call >= threshold:
        return {"id": call.id, "action": "drop_result", "reason": "result_dropped"}
    return {"id": call.id, "action": "drop_call", "reason": "call_dropped"}


def _truncated_result_text(text: str, is_error: bool, head_chars: int) -> str:
    if len(text) <= head_chars + 120:
        return text
    head = f"{text[:head_chars]}\n" if head_chars > 0 else ""
    return (
        f"{head}[compattazione veloce: troncati {len(text) - head_chars} caratteri di questo output"
        f"{' (errore)' if is_error else ''}; riesegui il tool se serve]"
    )


def apply_decisions(
    messages: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    calls: list[ToolCall],
    head_chars: int,
) -> list[dict[str, Any]]:
    """Ricostruisce la cronologia applicando le decisioni di Rizzo."""
    by_id = {call.id: call for call in calls}
    actions: dict[str, str] = {}
    for decision in decisions:
        call = by_id.get(decision["id"])
        if call and decision["action"] != "keep":
            actions[call.tool_use_id] = decision["action"]

    kept: list[dict[str, Any]] = []
    for message in messages:
        calls_here = tool_calls_of(message)
        is_tool_result = message.get("role") == "tool"
        touched = False
        if is_tool_result and message.get("tool_call_id") in actions:
            touched = True
        if any(call["id"] in actions for call in calls_here):
            touched = True
        if not touched:
            kept.append(message)
            continue

        if is_tool_result:
            action = actions.get(message.get("tool_call_id", ""))
            if action == "drop_call":
                continue
            text = message_text(message)
            if action == "drop_result":
                new_text = _truncated_result_text(text, False, head_chars)
                if new_text != text:
                    copy = dict(message)
                    copy["content"] = new_text
                    kept.append(copy)
                    continue
            kept.append(message)
            continue

        new_calls = []
        for call in calls_here:
            action = actions.get(call["id"])
            if action == "drop_call":
                continue
            new_calls.append(call)
        copy = dict(message)
        if new_calls:
            copy["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["tool"],
                        "arguments": json.dumps(call["input"], ensure_ascii=False, default=str),
                    },
                }
                for call in new_calls
            ]
        else:
            copy.pop("tool_calls", None)
        if not message_text(copy).strip() and not new_calls:
            continue
        kept.append(copy)
    return kept


@dataclass
class CompactionResult:
    messages: list[dict[str, Any]]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    chars_before: int = 0
    chars_after: int = 0
    state_tokens: int = 0
    state_stage: str = ""
    rizzo_seconds: float = 0.0
    kept: int = 0
    dropped: int = 0
    truncated: int = 0

    @property
    def reduction(self) -> float:
        if not self.chars_before:
            return 0.0
        return (self.chars_before - self.chars_after) / self.chars_before


# ---------------------------------------------------------------------------
# Checkpoint deterministico (modalità "solo Rizzo": zero generazione)
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")

DECISION_HINTS = (
    "decido", "scelgo", "deciso", "invece", "scegliamo", "optiamo", "adottiamo",
    "decide", "decision", "we will", "use ", "usa ", "prefer",
)
CONSTRAINT_HINTS = (
    "deve", "devesi", "non ", "mai ", "sempre", "obblig", "vincol", "preferis",
    "requisito", "regola", "constraint", "must ", "never ", "always ",
)
BLOCKER_HINTS = ("error", "failed", "failure", "exception", "traceback", "non è stato possibile")


def _collect_files(messages: list[dict[str, Any]]) -> list[str]:
    files: list[str] = []
    pattern = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|json|md|sh|yaml|yml|toml|swift|go|rs)")
    for message in messages:
        for call in tool_calls_of(message):
            text = json.dumps(call["input"], ensure_ascii=False, default=str)
            for match in pattern.findall(text):
                if match not in files:
                    files.append(match)
    return files


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


# ---------------------------------------------------------------------------
# Pre-igestione: decisioni a livello di messaggio su uno stato compatto
# ---------------------------------------------------------------------------

DIGEST_CONTEXT = (
    "A coding-assistant conversation is being compacted. `history` lists the messages of the "
    "conversation, oldest first, with tool outputs replaced by a short note. For each message a "
    "question asks whether that message still matters for what the assistant does next. Messages "
    "that are dropped are deleted permanently; the assistant can always re-run a tool or re-read a "
    "file."
)


def digest_entries(messages: list[dict[str, Any]], text_chars: int = 400) -> list[dict[str, Any]]:
    """Rappresentazione compatta della cronologia (pochi token, decisioni veloci)."""
    entries: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = message.get("role", "user")
        text = message_text(message).strip()
        calls = tool_calls_of(message)
        tools = ", ".join(call["tool"] for call in calls)
        if role == "tool":
            note = f"tool output ({len(text)} chars)"
            summary = ""
        else:
            note = ""
            summary = text[:text_chars].replace("\n", " ")
        if not summary and not tools and not note:
            continue
        entries.append({"i": index, "role": role, "summary": summary, "tools": tools, "note": note})
    return entries


def build_units(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raggruppa i messaggi in unita' indivisibili.

    Un messaggio dell'assistente con tool_calls e i suoi messaggi 'tool' formano
    un'unita': buttare via uno dei due lascerebbe una sequenza non valida per
    l'API, quindi si decide sempre sull'unita' intera.
    """
    tool_index: dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.get("role") == "tool" and message.get("tool_call_id"):
            tool_index[message["tool_call_id"]] = index

    units: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for index, message in enumerate(messages):
        if index in consumed:
            continue
        calls = tool_calls_of(message)
        if message.get("role") == "assistant" and calls:
            indices = [index]
            for call in calls:
                result_index = tool_index.get(call["id"])
                if result_index is not None and result_index not in consumed:
                    indices.append(result_index)
                    consumed.add(result_index)
            consumed.add(index)
            units.append({"indices": indices, "role": "assistant", "message": message, "calls": calls})
        else:
            consumed.add(index)
            units.append({"indices": [index], "role": message.get("role", "user"), "message": message, "calls": []})
    return units


def unit_digest(unit: dict[str, Any], text_chars: int) -> dict[str, Any]:
    message = unit["message"]
    text = message_text(message).strip()
    tools = ", ".join(call["tool"] for call in unit["calls"])
    if unit["role"] == "tool":
        summary = ""
        note = f"tool output ({len(text)} chars)"
    else:
        note = ""
        summary = text[:text_chars].replace("\n", " ")
    return {
        "i": unit["indices"][0],
        "role": unit["role"],
        "summary": summary,
        "tools": tools,
        "note": note,
        "size": len(text),
    }


def prune_messages(
    messages: list[dict[str, Any]],
    client: RizzoClient,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide (con Rizzo) quali unita' di messaggi tenere, su uno stato compatto.

    Restituisce un dizionario con i messaggi tenuti e le statistiche.
    """
    resolved = dict(DEFAULTS)
    resolved.update(options or {})
    threshold = resolved["keepThreshold"]
    preserve = resolved["preserveRecentMessages"]
    text_chars = int(resolved.get("digestTextChars", 150))

    units = build_units(messages)
    entries = [unit_digest(unit, text_chars) for unit in units]
    candidates = [
        (unit, entry)
        for unit, entry in zip(units, entries)
        if not is_pinned(unit["indices"][0], len(messages), preserve)
        and entry["role"] not in ("system", "tool")
        and (entry["summary"] or entry["tools"])
    ]
    stats: dict[str, Any] = {
        "messages_before": len(messages),
        "messages_after": len(messages),
        "candidates": len(candidates),
        "kept": 0,
        "dropped": 0,
        "state_tokens": 0,
        "seconds": 0.0,
    }
    if not candidates:
        return {"messages": messages, "stats": stats}

    state = {
        "context": DIGEST_CONTEXT,
        "goal": goal_from_messages(messages),
        "history": entries,
    }
    state_tokens = estimate_tokens(json.dumps(state, ensure_ascii=False, default=str))
    stats["state_tokens"] = state_tokens

    import time

    started = time.time()
    decisions: dict[int, float] = {}
    questions: dict[str, Any] = {}
    for unit, entry in candidates:
        questions[f"m{entry['i']}"] = {
            "type": "noul",
            "instructions": (
                f"Message {entry['i']} ({entry['role']}"
                f"{', tool: ' + entry['tools'] if entry['tools'] else ''}) still matters for what the "
                f"assistant does next: {entry['summary'][:200]}"
            ),
        }

    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    budget = resolved["maxRequestTokens"] - state_tokens - REQUEST_OVERHEAD_TOKENS
    for unit, entry in candidates:
        tokens = estimate_tokens(json.dumps(questions[f"m{entry['i']}"], ensure_ascii=False))
        if current and current_tokens + tokens > budget:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((unit, entry))
        current_tokens += tokens
    if current:
        batches.append(current)

    for batch in batches:
        batch_questions = {}
        for _unit, entry in batch:
            batch_questions.update({f"m{entry['i']}": questions[f"m{entry['i']}"]})
        answers = client.ask(state, batch_questions)
        for _unit, entry in batch:
            decisions[entry["i"]] = client.noul(answers, f"m{entry['i']}")
    stats["seconds"] = round(time.time() - started, 2)

    dropped_indices: set[int] = set()
    for unit, entry in candidates:
        if decisions.get(entry["i"], 1.0) < threshold:
            dropped_indices.update(unit["indices"])
    kept = [
        message for index, message in enumerate(messages) if index not in dropped_indices
    ]
    stats["messages_after"] = len(kept)
    stats["kept"] = len(kept)
    stats["dropped"] = len(dropped_indices)
    return {"messages": kept, "stats": stats}




def build_checkpoint(messages: list[dict[str, Any]]) -> str:
    """Costruisce il checkpoint a otto sezioni senza chiamare alcun LLM."""
    users: list[str] = []
    assistants: list[str] = []
    tool_lines: list[str] = []
    errors: list[str] = []
    last_assistant_index = -1

    for index, message in enumerate(messages):
        role = message.get("role")
        text = message_text(message).strip()
        if role == "user" and text and not is_control_message(text):
            users.append(text)
        elif role == "assistant":
            if text:
                assistants.append(text)
                last_assistant_index = index
            for call in tool_calls_of(message):
                arguments = json.dumps(call["input"], ensure_ascii=False, default=str)
                tool_lines.append(f"`{call['tool']}` {truncate(arguments, 160)}")
        elif role == "tool" and text:
            lowered = text.lower()
            if any(hint in lowered for hint in BLOCKER_HINTS):
                errors.append(truncate(text.replace("\n", " "), 200))

    pending = [
        message_text(message).strip()
        for message in messages[last_assistant_index + 1 :]
        if message.get("role") == "user"
        and message_text(message).strip()
        and not is_control_message(message_text(message))
    ]

    constraints = _unique(
        line
        for message in messages
        if message.get("role") == "user"
        for line in message_text(message).splitlines()
        if not is_control_message(line)
        and any(hint in line.lower() for hint in CONSTRAINT_HINTS)
    )[:6]
    decisions = _unique(
        line
        for text in assistants
        for line in text.splitlines()
        if any(hint in line.lower() for hint in DECISION_HINTS)
    )[:6]
    files = _collect_files(messages)[:20]

    sections = [
        ("Goal", pending[-3:] if pending else users[-3:]),
        ("Constraints & Preferences", constraints),
        ("Completed Work", tool_lines[-12:]),
        ("Current State", [assistants[-1]] if assistants else []),
        ("Blockers", _unique(errors)[:5]),
        ("Key Decisions", decisions),
        ("Pending User Asks", pending[-3:]),
        ("Critical Context & Relevant Files", [", ".join(f"`{name}`" for name in files)] if files else []),
    ]

    parts: list[str] = []
    for title, items in sections:
        parts.append(f"## {title}")
        if items:
            for item in items:
                parts.append(f"- {item}" if not item.startswith("- ") else item)
        else:
            parts.append("- (none)")
    return "\n".join(parts)



def _message_chars(message: dict[str, Any]) -> int:
    total = len(message_text(message))
    for call in tool_calls_of(message):
        try:
            total += len(json.dumps(call["input"], ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            total += 20
    return total


def compact(
    messages: list[dict[str, Any]],
    client: RizzoClient,
    options: dict[str, Any] | None = None,
) -> CompactionResult:
    """Compatta la cronologia usando le decisioni locali di Rizzo Flow."""
    import time

    resolved = dict(DEFAULTS)
    resolved.update(options or {})
    calls = collect_tool_calls(messages, resolved["preserveRecentMessages"])
    candidates = [call for call in calls if not call.pinned]
    chars_before = sum(_message_chars(message) for message in messages)

    result = CompactionResult(messages=messages, chars_before=chars_before, chars_after=chars_before)
    if not candidates:
        result.kept = len(calls)
        return result

    fitted = fit_state(messages, calls, resolved)
    result.state_tokens = fitted["tokens"]
    result.state_stage = fitted["stage"]

    started = time.time()
    decisions: list[dict[str, Any]] = []
    for batch in _batch_calls(candidates, fitted["tokens"], resolved):
        questions: dict[str, Any] = {}
        for call in batch:
            questions.update(_questions_for(call))
        answers = client.ask(fitted["state"], questions)
        for call in batch:
            decisions.append(
                _decide(
                    call,
                    client.noul(answers, f"call_{call.id}"),
                    client.noul(answers, f"result_{call.id}"),
                    resolved["keepThreshold"],
                )
            )
    result.rizzo_seconds = time.time() - started

    result.messages = apply_decisions(messages, decisions, calls, resolved["truncateHeadChars"])
    result.decisions = decisions
    result.chars_after = sum(_message_chars(message) for message in result.messages)
    result.kept = sum(1 for decision in decisions if decision["action"] == "keep")
    result.dropped = sum(1 for decision in decisions if decision["action"] == "drop_call")
    result.truncated = sum(1 for decision in decisions if decision["action"] == "drop_result")
    return result
