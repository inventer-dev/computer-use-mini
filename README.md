# computer-use-mini

Puente MCP ligero para controlar ventanas de escritorio mediante CuaDriver. Publica ocho herramientas sencillas en vez de exponer directamente todas las herramientas del driver.

```text
Cliente MCP ──stdio──> cu_mini.py ──> CuaDriver ──> escritorio
                         8 tools       daemon
```

## Requisitos

- macOS o Linux de escritorio y Python 3.10 o posterior.
- [Cua Driver](https://cua.ai/docs/cua-driver/quickstart), instalado por separado.
- En macOS, permisos de **Accesibilidad** y **Grabación de pantalla** para CuaDriver en Ajustes del Sistema.

El repositorio no incluye Cua Driver. El script de arranque busca `cua-driver` en `PATH`; en macOS también busca la aplicación en `/Applications` y `~/Applications`. Si está instalada en otra ubicación, configura `CU_DRIVER` con la ruta al ejecutable.

## Instalar

```bash
git clone https://github.com/inventer-dev/computer-use-mini.git
cd computer-use-mini
```

Inicia el daemon:

```bash
./start-daemon.sh
```

El socket predeterminado es `~/Library/Caches/cu-mini/cu.sock` en macOS y `${XDG_CACHE_HOME:-~/.cache}/cu-mini/cu.sock` en Linux. Para usar otra ubicación:

```bash
CU_SOCKET="$HOME/.cache/cu-mini/cu.sock" ./start-daemon.sh
```

Si Cua Driver está instalado fuera de `PATH` y de las carpetas habituales de macOS:

```bash
CU_DRIVER="/ruta/al/ejecutable/cua-driver" ./start-daemon.sh
```

Mantén el daemon activo mientras uses el servidor MCP.

## Conectar un cliente MCP

Agrega el servidor a la configuración de cualquier cliente compatible con MCP sobre stdio. La forma exacta de la configuración depende del cliente; este es un ejemplo común:

```json
{
  "mcpServers": {
    "computer-use-mini": {
      "command": "python3",
      "args": ["/ruta/absoluta/computer-use-mini/cu_mini.py"]
    }
  }
}
```

El puente busca `cua-driver` en `PATH` y en las ubicaciones habituales de CuaDriver.app. Si no lo encuentra automáticamente, configura `CU_DRIVER` en el entorno del cliente MCP con la ruta al ejecutable. Si cambiaste el socket predeterminado, define también `CU_SOCKET` con la misma ruta que usaste al arrancar el daemon. El script de arranque incluido está preparado para macOS y Linux; Windows requiere adaptar el arranque y el endpoint.

## Herramientas

| Herramienta | Parámetros | Función |
|---|---|---|
| `windows` | `{app?, all_spaces?}` | Lista ventanas con `window_id`, proceso, app y título. |
| `screen` | `{window_id, since?, max_elements?, image?}` | Devuelve árbol de accesibilidad y, por defecto, una captura. |
| `click` | `{window_id, element? \| x?, y?}` | Hace clic en un elemento del árbol o en coordenadas. |
| `type` | `{window_id, text, element?}` | Escribe texto en el campo enfocado o en un elemento. |
| `key` | `{window_id, key, modifiers?}` | Pulsa una tecla o combinación. |
| `scroll` | `{window_id, direction, amount?}` | Desplaza la ventana. |
| `open_app` | `{name}` | Abre o activa una app por nombre. |
| `guide` | `{topic?}` | Solicita documentación del driver bajo demanda. |

`screen` devuelve un `snapshot_id`; usa el identificador de una fila como `element: "s00000003:7"` en acciones posteriores. El puente resuelve el PID requerido por CuaDriver a partir de `window_id`.

## Permisos y modo de ejecución

El daemon arranca en modo `standard`. Para sesiones desechables y bajo tu responsabilidad, puedes elegir el modo irrestricto explícitamente:

```bash
CUA_UNSAFE_MODE=1 ./start-daemon.sh
```

Ese modo usa `--dangerously-bypass-approvals`, que activa el modo unrestricted de Cua Driver y omite las aprobaciones del agente. macOS aún requiere que Cua Driver tenga sus permisos de Accesibilidad y Grabación de pantalla. Usa una cuenta y sesión de escritorio adecuadas para ese nivel de acceso.

`open_app` solo acepta nombres de aplicaciones, no rutas ni argumentos. El puente no expone las herramientas `kill_app`, `run_script` ni `clipboard_*` del driver.

## Pruebas manuales

Con CuaDriver y el daemon activos:

```bash
python3 tests/smoke.py
```

Para probar una ventana concreta, obtén su `window_id` con la herramienta `windows` y pásalo como argumento:

```bash
python3 tests/screen_test.py <window_id>
```

## Limitaciones

- En algunas apps Electron o virtualizadas, el árbol de accesibilidad puede ser incompleto; usa coordenadas sobre la captura.
- Las capturas se envían como imagen base64 y pueden ocupar más de un megabyte. Usa `image: false` para omitirlas.
