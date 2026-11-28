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
import select
import shutil
import subprocess
import sys
import tempfile
import time


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
            "capture_id": {"type": "string",
                           "description": "ID de captura que devuelve `screen` (structuredContent.capture_id). "
                                          "Úsalo con x,y para clics por píxel."},
            "delivery_mode": {"type": "string", "enum": ["background", "foreground"]},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
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
        "Desplaza la ventana. Para gestos tipo swipe (feed vertical) usa `drag`.",
        {
            "window_id": WINDOW_ID,
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "Unidades de scroll (default 3)"},
        },
        ["window_id", "direction"],
    ),
    tool(
        "scroll_smooth",
        "Scroll con fases de trackpad (el gesto de dos dedos). Es el que sí funciona en "
        "iPhone Mirroring y feeds tipo TikTok; `scroll` manda rueda discreta y casi nunca avanza.",
        {
            "window_id": WINDOW_ID,
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"],
                          "description": "down = avanzar al siguiente elemento del feed"},
            "amount": {"type": "integer", "description": "Píxeles totales (default 400)"},
            "steps": {"type": "integer", "description": "Eventos intermedios (default 14)"},
        },
        ["window_id", "direction"],
    ),
    tool(
        "drag",
        "Arrastre con presionar-mover-soltar entre dos puntos de la captura de la ventana. "
        "Para swipe (feed tipo TikTok) usa `swipe`: es más simple y rápido.",
        {
            "window_id": WINDOW_ID,
            "from_x": {"type": "number"},
            "from_y": {"type": "number"},
            "to_x": {"type": "number"},
            "to_y": {"type": "number"},
            "duration_ms": {"type": "integer", "description": "Duración del gesto (default 500)"},
            "steps": {"type": "integer", "description": "Eventos intermedios (default 20)"},
            "delivery_mode": {"type": "string", "enum": ["background", "foreground"]},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
        },
        ["window_id", "from_x", "from_y", "to_x", "to_y"],
    ),
    tool(
        "swipe",
        "Swipe vertical u horizontal en el centro de la ventana. Pensado para feeds (TikTok, "
        "Reels) y apps que reenvían el gesto al teléfono (iPhone Mirroring): usa foreground y un "
        "gesto rápido por defecto.",
        {
            "window_id": WINDOW_ID,
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "duration_ms": {"type": "integer", "description": "Duración del gesto (default 120)"},
            "distance": {"type": "number", "description": "Fracción de la ventana a recorrer (0-1, default 0.5)"},
            "delivery_mode": {"type": "string", "enum": ["background", "foreground"]},
        },
        ["window_id", "direction"],
    ),
    tool(
        "open_app",
        "Abre (o activa) una app por nombre, p.ej. 'TextEdit'. No garantiza que su ventana quede al frente.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    tool(
        "front",
        "Trae al frente una ventana concreta y la deja enfocada. Úsalo cuando el juego o la app "
        "esté detrás de otra ventana o en otro monitor.",
        {"window_id": WINDOW_ID},
        ["window_id"],
    ),
    tool(
        "displays",
        "Lista los monitores activos con sus límites, escala y cuál es el principal.",
        {},
        [],
    ),
    tool(
        "screenshot",
        "Captura una pantalla completa a un PNG (por defecto la principal). Devuelve ruta y tamaño. "
        "Usa `display` para elegir monitor.",
        {
            "display": {"type": "integer", "description": "Índice de pantalla (1 = principal)."},
            "path": {"type": "string", "description": "Ruta de salida del PNG."},
            "window_id": {"type": "integer",
                          "description": "Captura solo esa ventana recortando la pantalla. "
                                         "Úsalo para iPhone Mirroring, que no se deja capturar directo."},
        },
        [],
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
    "click": ("click", {"window_id", "element", "x", "y", "count", "capture_id", "delivery_mode", "button"}),
    "type": ("type_text", {"window_id", "text", "element"}),
    "key": ("press_key", {"window_id", "key", "modifiers"}),
    "scroll": ("scroll", {"window_id", "direction", "amount"}),
    "drag": ("drag", {"window_id", "from_x", "from_y", "to_x", "to_y", "duration_ms",
                      "steps", "delivery_mode", "button", "modifier"}),
    "open_app": ("launch_app", {"name"}),
    "front": ("bring_to_front", {"window_id", "session"}),
    "guide": ("get_guide", {"topic"}),
}

_JXA_SCREENS = """
ObjC.import("AppKit");
var arr = $.NSScreen.screens, n = arr.count, out = [];
var m = $.NSScreen.mainScreen.frame;
for (var i = 0; i < n; i++) {
  var s = arr.objectAtIndex(i), f = s.frame;
  var isMain = (f.origin.x == m.origin.x && f.origin.y == m.origin.y &&
                f.size.width == m.size.width && f.size.height == m.size.height);
  out.push({index:i, x:f.origin.x, y:f.origin.y, w:f.size.width, h:f.size.height,
            scale:s.backingScaleFactor, main:isMain});
}
JSON.stringify({main_w:m.size.width, main_h:m.size.height, screens:out});
"""

_SCREENS_CACHE = {"t": 0.0, "v": None}


def screens():
    """Pantallas activas vía NSScreen (JXA). Cache de 5 s."""
    now = time.time()
    if _SCREENS_CACHE["v"] and now - _SCREENS_CACHE["t"] < 5:
        return _SCREENS_CACHE["v"]
    out = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA_SCREENS],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    data = json.loads(out) if out else {"screens": [], "main_h": 0}
    _SCREENS_CACHE.update({"t": now, "v": data})
    return data


def display_of(bounds):
    """Índice de pantalla (1-based) que contiene el centro de la ventana, o 0."""
    try:
        d = screens()
        main_h = float(d.get("main_h") or 0)
        cx = float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2
        cy = float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2
        for s in d.get("screens", []):
            # NSScreen: origen abajo-izquierda; CGWindow: arriba-izquierda.
            top = main_h - (float(s["y"]) + float(s["h"]))
            if float(s["x"]) <= cx <= float(s["x"]) + float(s["w"]) and top <= cy <= top + float(s["h"]):
                return int(s["index"]) + 1
    except Exception:
        pass
    return 0


class _DriverSession:
    """Sesión MCP persistente contra `cua-driver mcp`."""

    def __init__(self):
        cmd = [CUA, "mcp"]
        if SOCKET:
            cmd += ["--socket", SOCKET]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._id = 0
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "computer-use-mini", "version": "0.2.0"},
        })
        self.notify("notifications/initialized")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._id += 1
        mid = self._id
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + (timeout if timeout is not None else TIMEOUT)
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise RuntimeError(f"{method}: timeout")
            ready, _, _ = select.select([self.proc.stdout], [], [], left)
            if not ready:
                raise RuntimeError(f"{method}: timeout")
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"{method}: el driver cerró la sesión")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == mid:
                return resp


_DRIVER: "_DriverSession | None" = None


def _session() -> "_DriverSession":
    global _DRIVER
    if _DRIVER is None or not _DRIVER.alive():
        _DRIVER = _DriverSession()
    return _DRIVER


def cua_call(driver_tool: str, args: dict) -> dict:
    """Llama una tool del driver dentro de una sesión MCP persistente.

    Importante: `cua-driver call` crea una sesión nueva por invocación, así que los
    `capture_id` y `element_token` mueren entre llamadas. Mantener un `cua-driver mcp`
    vivo permite snapshot -> click sobre la misma sesión.
    """
    res = _session().request("tools/call", {"name": driver_tool, "arguments": args})
    if "error" in res:
        raise RuntimeError((res["error"] or {}).get("message", "driver error"))
    result = res.get("result") or {}
    out = dict(result.get("structuredContent") or {})
    for block in result.get("content") or []:
        if block.get("type") == "image" and block.get("data"):
            out["screenshot_png_b64"] = block["data"]
            out.setdefault("screenshot_mime_type", block.get("mimeType", "image/png"))
    if not out:
        out = {"text": "".join(b.get("text", "") for b in result.get("content") or [])}
    if result.get("isError"):
        out["isError"] = True
    return out


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
    rows: list[dict] = []
    tree = result.get("tree_markdown")
    if tree:
        header = f"snapshot_id: {result.get('snapshot_id')} | window_id: {result.get('window_id')}"
        blocks.append({"type": "text", "text": header + "\n" + tree})
        if result.get("truncated"):
            blocks.append({"type": "text", "text": result.get("truncation_hint", "")})
    elif public == "windows":
        rows = []
        for w in (result.get("windows") or []):
            if w.get("layer") != 0:
                continue
            b = w.get("bounds") or {}
            if not b.get("width") or not b.get("height"):
                continue
            rows.append({
                "window_id": w.get("window_id"),
                "pid": w.get("pid"),
                "app": w.get("app_name"),
                "title": w.get("title"),
                "on_screen": bool(w.get("is_on_screen")),
                "display": display_of(b),
                "z": w.get("z_index"),
            })
        rows.sort(key=lambda r: (r.get("z") is None, r.get("z") or 0))
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
    structured = {"snapshot_id": result.get("snapshot_id"),
                  "window_id": result.get("window_id"),
                  "capture_id": result.get("capture_id")}
    # Algunos clientes (opencode) solo exponen structuredContent al modelo.
    # Incluir el payload real evita que la llamada se vea vacía.
    if public == "windows":
        structured["windows"] = rows
    elif tree:
        structured["tree"] = tree[:20000]
        structured["truncated"] = bool(result.get("truncated"))
    else:
        structured["result"] = {k: v for k, v in result.items()
                                if k != "screenshot_png_b64" and
                                (not isinstance(v, str) or len(v) < 4000)}
    return {"content": blocks, "structuredContent": structured}


def png_size(path: str):
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    except Exception:
        return (0, 0)


_CG = None


def _coregraphics():
    """Carga CoreGraphics por ctypes (sin dependencias) y fija firmas."""
    global _CG
    if _CG is not None:
        return _CG
    import ctypes
    import ctypes.util
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
    cg.CGEventCreateScrollWheelEvent2.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                  ctypes.c_uint32, ctypes.c_int32,
                                                  ctypes.c_int32, ctypes.c_int32]
    cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
    cg.CGEventPostToPid.argtypes = [ctypes.c_int32, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    _CG = (cg, cf)
    return _CG


def smooth_scroll(pid, dy, dx=0, steps=14, ms=12):
    """Scroll con fases de trackpad (began/changed/ended) + inercia, dirigido al pid.

    Es lo que sí interpreta iPhone Mirroring; la rueda discreta casi nunca.
    """
    import time as _t
    cg, cf = _coregraphics()
    IS_CONTINUOUS, SCROLL_PHASE, MOMENTUM_PHASE = 88, 99, 123
    BEGAN, CHANGED, ENDED, M_END = 1, 2, 4, 4

    def make(y, x, phase=0, momentum=0):
        ev = cg.CGEventCreateScrollWheelEvent2(None, 0, 2, int(y), int(x), 0)
        cg.CGEventSetIntegerValueField(ev, IS_CONTINUOUS, 1)
        if phase:
            cg.CGEventSetIntegerValueField(ev, SCROLL_PHASE, phase)
        if momentum:
            cg.CGEventSetIntegerValueField(ev, MOMENTUM_PHASE, momentum)
        return ev

    def post(ev):
        cg.CGEventPostToPid(pid, ev)
        cf.CFRelease(ev)

    post(make(0, 0, phase=BEGAN))
    _t.sleep(ms / 1000.0)
    sy, sx = dy / float(steps), dx / float(steps)
    for _ in range(steps):
        post(make(sy, sx, phase=CHANGED))
        _t.sleep(ms / 1000.0)
    post(make(0, 0, phase=ENDED))
    _t.sleep(0.02)
    for frac in (0.45, 0.30, 0.15, 0.06):
        post(make(sy * frac, sx * frac, momentum=2))
        _t.sleep(0.03)
    post(make(0, 0, momentum=M_END))


def call_tool(name: str, args: dict) -> dict:
    if name == "displays":
        rows = [{"display": s["index"] + 1, "main": bool(s.get("main")),
                 "x": s["x"], "y": s["y"], "w": s["w"], "h": s["h"], "scale": s["scale"]}
                for s in screens().get("screens", [])]
        return {"content": [{"type": "text", "text": json.dumps(rows)}],
                "structuredContent": {"displays": rows}}

    if name == "screenshot":
        disp = int((args or {}).get("display") or 0)
        wid = (args or {}).get("window_id")
        path = (args or {}).get("path")
        if wid:
            # Captura por región: sirve para ventanas que no se dejan capturar
            # directo (iPhone Mirroring devuelve una composición con otras ventanas).
            pid = resolve_pid(int(wid))
            raw = cua_call("list_windows", {})
            win = next((w for w in (raw.get("windows") or [])
                        if w.get("window_id") == int(wid)), None)
            if not win:
                return {"content": [{"type": "text", "text": "ERROR: ventana no encontrada"}],
                        "isError": True}
            b = win.get("bounds") or {}
            d = screens().get("screens", [])
            cx = float(b.get("x", 0)) + float(b.get("width", 0)) / 2
            cy = float(b.get("y", 0)) + float(b.get("height", 0)) / 2
            target = None
            for s in d:
                top = float(screens().get("main_h") or 0) - (float(s["y"]) + float(s["h"]))
                if float(s["x"]) <= cx <= float(s["x"]) + float(s["w"]) and top <= cy <= top + float(s["h"]):
                    target = s
                    break
            if target is None:
                target = d[0] if d else {"index": 0, "x": 0, "y": 0, "w": 1920, "h": 1080}
            disp = int(target["index"]) + 1
            path = path or os.path.join(tempfile.gettempdir(), "cu-win-%s.png" % wid)
            proc = subprocess.run(["screencapture", "-x", "-D", str(disp), path],
                                  capture_output=True, text=True)
            if proc.returncode != 0 or not os.path.exists(path):
                return {"content": [{"type": "text", "text": "ERROR: screencapture falló"}],
                        "isError": True}
            try:
                from PIL import Image
                im = Image.open(path)
                scale = im.width / float(target["w"]) if target.get("w") else 1.0
                top = float(screens().get("main_h") or 0) - (float(target["y"]) + float(target["h"]))
                left = (float(b.get("x", 0)) - float(target["x"])) * scale
                upper = (float(b.get("y", 0)) - top) * scale
                right = left + float(b.get("width", 0)) * scale
                lower = upper + float(b.get("height", 0)) * scale
                im.crop((int(left), int(upper), int(right), int(lower))).save(path)
            except Exception as exc:  # noqa: BLE001
                return {"content": [{"type": "text", "text": "ERROR recortando: %s" % exc}],
                        "isError": True}
        else:
            disp = disp or 1
            path = path or os.path.join(tempfile.gettempdir(), "cu-screen-%d.png" % disp)
            proc = subprocess.run(["screencapture", "-x", "-D", str(disp), path],
                                  capture_output=True, text=True)
            if proc.returncode != 0 or not os.path.exists(path):
                return {"content": [{"type": "text", "text":
                                     "ERROR: screencapture falló: %s" % (proc.stderr or "")[:200]}],
                        "isError": True}
        w, h = png_size(path)
        info = {"path": path, "display": disp, "width": w, "height": h,
                "window_id": wid or None}
        return {"content": [{"type": "text", "text": json.dumps(info)}],
                "structuredContent": info}

    if name == "swipe":
        wid = int((args or {}).get("window_id"))
        direction = (args or {}).get("direction") or "up"
        dur = int((args or {}).get("duration_ms") or 120)
        dist = float((args or {}).get("distance") or 0.5)
        dm = (args or {}).get("delivery_mode") or "foreground"
        pid = resolve_pid(wid)
        if pid is None:
            return {"content": [{"type": "text", "text":
                                 "ERROR: no encuentro pid para window_id %s" % wid}], "isError": True}
        state = cua_call("get_window_state", {"pid": pid, "window_id": wid,
                                              "include_screenshot": True, "tree_format": "markdown"})
        w = state.get("screenshot_width") or 400
        h = state.get("screenshot_height") or 800
        cx, cy = w / 2.0, h / 2.0
        # El recorrido se mide sobre el eje del gesto: en vertical manda el alto
        # (si no, en ventanas verticales el swipe queda demasiado corto y iOS lo ignora).
        span = max(60.0, dist * (h if direction in ("up", "down") else w))
        # En feeds (TikTok) el centro cae sobre el caption/links y puede abrir un
        # enlace en vez de deslizar: el gesto vertical arranca abajo.
        if direction == "up":
            frm, to = (cx, h * 0.80), (cx, h * 0.80 - span)
        elif direction == "down":
            frm, to = (cx, h * 0.25), (cx, h * 0.25 + span)
        elif direction == "left":
            frm, to = (cx + span / 2, h * 0.62), (cx - span / 2, h * 0.62)
        else:
            frm, to = (cx - span / 2, h * 0.62), (cx + span / 2, h * 0.62)
        res = cua_call("drag", {"pid": pid, "window_id": wid,
                                "from_x": frm[0], "from_y": frm[1],
                                "to_x": to[0], "to_y": to[1],
                                "duration_ms": dur, "steps": max(4, dur // 20),
                                "delivery_mode": dm})
        out = as_content(name, res)
        out.setdefault("structuredContent", {})["gesture"] = {
            "from": [round(frm[0], 1), round(frm[1], 1)],
            "to": [round(to[0], 1), round(to[1], 1)],
            "duration_ms": dur, "delivery_mode": dm}
        return out

    if name == "scroll_smooth":
        wid = int((args or {}).get("window_id"))
        pid = resolve_pid(wid)
        if pid is None:
            return {"content": [{"type": "text", "text":
                                 "ERROR: no encuentro pid para window_id %s" % wid}], "isError": True}
        direction = (args or {}).get("direction") or "down"
        amount = int((args or {}).get("amount") or 400)
        steps = int((args or {}).get("steps") or 14)
        if direction == "down":
            dy, dx = -amount, 0
        elif direction == "up":
            dy, dx = amount, 0
        elif direction == "left":
            dy, dx = 0, amount
        else:
            dy, dx = 0, -amount
        smooth_scroll(pid, dy, dx, steps=steps)
        info = {"window_id": wid, "pid": pid, "direction": direction,
                "dy": dy, "dx": dx, "steps": steps}
        return {"content": [{"type": "text", "text": json.dumps(info)}],
                "structuredContent": info}

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
        # No filtrar en el driver: con pantallas duplicadas/extendidas `is_on_screen`
        # puede venir siempre en false. Devolvemos todas las layer 0 y marcamos el estado.
        payload.pop("all_spaces", None)
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
        "get_window_state", "click", "type_text", "press_key", "scroll", "bring_to_front", "drag"
    ) and not payload.get("pid"):
        pid = resolve_pid(int(payload["window_id"]))
        if pid is None:
            return {"content": [{"type": "text", "text":
                                 f"ERROR: no encuentro pid para window_id {payload['window_id']}"}],
                    "isError": True}
        payload["pid"] = pid

    # El driver 0.52 espera `element_token`; la API pública usa `element`.
    if payload.get("element"):
        payload["element_token"] = payload.pop("element")

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
