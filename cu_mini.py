#!/usr/bin/env python3
"""computer-use-mini — puente MCP flaco sobre CuaDriver.

Expone 8 herramientas en vez de las 61 de `cua-driver mcp`, con esquemas
simples, y reenvía al daemon por `cua-driver call --socket`.

Protocolo: MCP sobre stdio, JSON-RPC 2.0, un mensaje JSON por línea.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys


def find_driver() -> str:
    """Busca Cua Driver sin depender de una ruta de instalación personal."""
    configured = os.environ.get("CU_DRIVER")
    if configured:
        return os.path.expanduser(configured)

    on_path = shutil.which("cua-driver")
    if on_path:
        return on_path

    for app in ("/Applications/CuaDriver.app", "~/Applications/CuaDriver.app"):
        bundled = os.path.expanduser(
            os.path.join(app, "Contents", "MacOS", "cua-driver")
        )
        if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
            return bundled

    # Keep the eventual error actionable if the dependency is not installed.
    return "cua-driver"


CUA = find_driver()
if sys.platform == "darwin":
    DEFAULT_CACHE = os.path.expanduser("~/Library/Caches")
else:
    DEFAULT_CACHE = os.environ.get(
        "XDG_CACHE_HOME", os.path.expanduser("~/.cache")
    )
SOCKET = os.environ.get("CU_SOCKET", os.path.join(DEFAULT_CACHE, "cu-mini", "cu.sock"))
TIMEOUT = int(os.environ.get("CU_TIMEOUT", "120"))
SERVER_INFO = {"name": "computer-use-mini", "version": "0.1.0"}
PROTOCOL_FALLBACK = "2025-06-18"

TOKEN_RE = re.compile(r"^s[0-9a-f]{8}:\d+$")


def tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        },
    }


WINDOW_ID = {"type": "integer", "description": "window_id de `windows` o `screen`"}

TOOLS = [
    tool(
        "windows",
        "Lista las ventanas visibles con su app, titulo, pid y window_id. "
        "Empieza por aqui para saber donde actuar.",
        {
            "app": {"type": "string", "description": "Filtra por nombre de app (opcional)"},
            "all_spaces": {
                "type": "boolean",
                "description": "Incluye ventanas de otros Spaces/minimizadas (default false)",
            },
        },
        [],
    ),
    tool(
        "screen",
        "Snapshot de una ventana: arbol de accesibilidad numerado + screenshot. "
        "Cada fila [N] se usa como `element` = '<snapshot_id>:N'. "
        "Usa since='latest' tras actuar para recibir solo los cambios.",
        {
            "window_id": WINDOW_ID,
            "since": {
                "type": "string",
                "description": "'latest' o un snapshot_id anterior (solo cambios)",
            },
            "max_elements": {
                "type": "integer",
                "description": "Nodos maximos del arbol (default 120)",
            },
            "image": {
                "type": "boolean",
                "description": "Incluir screenshot (default true; false ahorra tokens)",
            },
        },
        ["window_id"],
    ),
    tool(
        "click",
        "Click en un elemento del snapshot (recomendado) o en coordenadas de la ventana.",
        {
            "window_id": WINDOW_ID,
            "element": {"type": "string", "description": "Token '<snapshot_id>:N'"},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "count": {"type": "integer", "description": "1 (default) o 2 para doble click"},
        },
        ["window_id"],
    ),
    tool(
        "type",
        "Escribe texto en el elemento enfocado o en uno indicado por token.",
        {
            "window_id": WINDOW_ID,
            "text": {"type": "string"},
            "element": {"type": "string", "description": "Token opcional"},
        },
        ["window_id", "text"],
    ),
    tool(
        "key",
        "Pulsa una tecla o combinacion: 'return', 'escape', 'tab', 'a' + ['cmd'].",
        {
            "window_id": WINDOW_ID,
            "key": {"type": "string"},
            "modifiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "cmd, shift, option, ctrl, fn",
            },
        },
        ["window_id", "key"],
    ),
    tool(
        "scroll",
        "Desplaza la ventana arriba/abajo.",
        {
            "window_id": WINDOW_ID,
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "Ticks (default 5)"},
        },
        ["window_id", "direction"],
    ),
    tool(
        "open_app",
        "Abre (o trae al frente) una app por nombre, p.ej. 'TextEdit'.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    tool(
        "guide",
        "Guia del driver bajo demanda (topic: overview, drag, browser, permissions, batching, menus).",
        {"topic": {"type": "string"}},
        [],
    ),
]

# nombre publico -> (tool del driver, campos permitidos)
ROUTES = {
    "windows": ("list_windows", {"app", "all_spaces"}),
    "screen": ("get_window_state", {"window_id", "since", "max_elements", "image"}),
    "click": ("click", {"window_id", "element", "x", "y", "count"}),
    "type": ("type_text", {"window_id", "text", "element"}),
    "key": ("press_key", {"window_id", "key", "modifiers"}),
    "scroll": ("scroll", {"window_id", "direction", "amount"}),
    "open_app": ("launch_app", {"name"}),
    "guide": ("get_guide", {"topic"}),
}


def cua_call(driver_tool: str, args: dict) -> dict:
    cmd = [CUA, "call", "--socket", SOCKET, driver_tool, json.dumps(args)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(
            f"{driver_tool}: sin salida (rc={proc.returncode}) {proc.stderr.strip()[:400]}"
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"text": out}


_PID_CACHE: dict[int, int] = {}


def resolve_pid(window_id: int) -> int | None:
    """El driver pide pid ademas de window_id; lo resolvemos por el modelo."""
    if window_id in _PID_CACHE:
        return _PID_CACHE[window_id]
    try:
        res = cua_call("list_windows", {})
    except Exception:  # noqa: BLE001
        return None
    for w in res.get("windows") or []:
        if w.get("window_id") == window_id and w.get("pid"):
            _PID_CACHE[window_id] = int(w["pid"])
            return int(w["pid"])
    return None


def as_content(public: str, result: dict) -> dict:
    """Convierte la respuesta del driver en bloques MCP."""
    if isinstance(result.get("refusal"), dict):
        msg = result["refusal"].get("message") or "refused"
        return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "isError": True}

    blocks: list[dict] = []
    tree = result.get("tree_markdown")
    if tree:
        header = f"snapshot_id: {result.get('snapshot_id')} | window_id: {result.get('window_id')}"
        blocks.append({"type": "text", "text": header + "\n" + tree})
        if result.get("truncated"):
            blocks.append({"type": "text", "text": result.get("truncation_hint", "")})
    elif public == "windows":
        rows = [
            {
                "window_id": w.get("window_id"),
                "pid": w.get("pid"),
                "app": w.get("app_name"),
                "title": w.get("title"),
            }
            for w in (result.get("windows") or [])
            if w.get("is_on_screen") and w.get("layer") == 0
        ]
        blocks.append({"type": "text", "text": json.dumps(rows, ensure_ascii=False)})
    else:
        slim = {
            k: v
            for k, v in result.items()
            if k != "screenshot_png_b64" and (not isinstance(v, str) or len(v) < 400)
        }
        blocks.append({"type": "text", "text": json.dumps(slim, ensure_ascii=False)[:2000]})

    png = result.get("screenshot_png_b64")
    if png:
        blocks.append(
            {
                "type": "image",
                "data": png,
                "mimeType": result.get("screenshot_mime_type", "image/png"),
            }
        )
    return {"content": blocks, "structuredContent": {"snapshot_id": result.get("snapshot_id"),
                                                    "window_id": result.get("window_id")}}


def call_tool(name: str, args: dict) -> dict:
    route = ROUTES.get(name)
    if route is None:
        return {"content": [{"type": "text", "text": f"ERROR: tool desconocido {name}"}],
                "isError": True}
    driver_tool, allowed = route
    payload = {k: v for k, v in (args or {}).items() if k in allowed}

    if name == "screen":
        payload.setdefault("max_elements", 120)
        include = payload.pop("image", True)
        payload["include_screenshot"] = bool(include)
        payload["tree_format"] = "markdown"
        if "since" in payload and payload["since"]:
            pass  # el driver lo entiende tal cual
        else:
            payload.pop("since", None)
    if name == "windows":
        if not payload.pop("all_spaces", False):
            payload["on_screen_only"] = True
    if name in ("click", "type") and payload.get("element"):
        if not TOKEN_RE.match(str(payload["element"])):
            return {
                "content": [{"type": "text", "text":
                             "ERROR: `element` debe ser '<snapshot_id>:N', p.ej. s00000001:7"}],
                "isError": True,
            }
    if name == "click" and not payload.get("element"):
        if "x" not in payload or "y" not in payload:
            return {"content": [{"type": "text", "text":
                                 "ERROR: pasa `element` o `x`+`y`"}], "isError": True}
    if name == "open_app":
        name_arg = str(payload.get("name") or "").strip()
        if not name_arg or "/" in name_arg or name_arg.startswith("-"):
            return {"content": [{"type": "text", "text":
                                 "ERROR: nombre de app invalido"}], "isError": True}

    if "window_id" in payload and driver_tool in (
        "get_window_state", "click", "type_text", "press_key", "scroll"
    ) and not payload.get("pid"):
        pid = resolve_pid(int(payload["window_id"]))
        if pid is None:
            return {"content": [{"type": "text", "text":
                                 f"ERROR: no encuentro pid para window_id {payload['window_id']}"}],
                    "isError": True}
        payload["pid"] = pid

    try:
        result = cua_call(driver_tool, payload)
    except Exception as exc:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"ERROR: {exc}"}], "isError": True}
    return as_content(name, result)


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method is None:
        return None  # notificacion
    if method == "initialize":
        params = msg.get("params") or {}
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_FALLBACK,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": call_tool(params.get("name", ""), params.get("arguments") or {}),
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Metodo no soportado: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is not None and msg.get("id") is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
