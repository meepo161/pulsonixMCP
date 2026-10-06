# pulsonix-mcp

MCP-сервер для **Pulsonix 10.x** (проверено на 10.5 build 7883) через встроенный ActiveX Scripting API.
Позволяет ИИ-агентам (Claude, Codex, Cursor, Copilot, Gemini…) читать и менять схемы и платы Pulsonix:
компоненты, цепи, netlist, BOM, атрибуты, ERC/DRC, CAM-выводы, библиотеки — и выполнять произвольные скрипты.

*MCP server for Pulsonix 10.x PCB/schematic CAD via its legacy ActiveX scripting API (Windows, stdio).*

## Требования

- Windows с установленным **Pulsonix 10.x** и лицензией, в которой есть Scripting (меню *Tools → Run Script*).
- Python **3.10+**.
- Сервер работает по **stdio на той же машине**, где стоит Pulsonix (облачные агенты без локального доступа его не запустят).

## Установка

```bash
pip install git+https://github.com/meepo161/pulsonixMCP.git
```

или из клона в режиме разработки:

```bash
git clone https://github.com/meepo161/pulsonixMCP.git
cd pulsonixMCP
pip install -e .
```

После установки появляется команда `pulsonix-mcp` (лежит в папке `Scripts` вашего Python).
Узнать полный путь:

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

Если в `PATH` эта папка не добавлена — в конфигах ниже указывайте полный путь,
например `C:\\Users\\<you>\\AppData\\Roaming\\Python\\Python314\\Scripts\\pulsonix-mcp.exe`.
Вместо `pulsonix-mcp` везде можно использовать `python -m pulsonix_mcp`.

Без установки, через [uv](https://docs.astral.sh/uv/): команда `uvx`, аргументы
`--from git+https://github.com/meepo161/pulsonixMCP.git pulsonix-mcp`.

### Первый запуск Pulsonix

Один раз откройте Pulsonix вручную и закройте стартовые диалоги (например, «проверять обновления?»).
В скрытом режиме такой диалог заблокирует скрипт, и вызов упадёт по таймауту.

## Подключение к ИИ-агентам

Во всех примерах `pulsonix-mcp` можно заменить полным путём к exe. Необязательные переменные окружения — в разделе
[Настройки](#настройки). Операция в headless-режиме занимает ~2 с, но на медленных машинах поднимите таймаут
инструмента клиента до 2–3 минут.

### Claude Code

```bash
claude mcp add pulsonix --scope user -- pulsonix-mcp
claude mcp list
```

`--scope user` — во всех проектах; без него — только в текущем.

### Claude Desktop

*Settings → Developer → Edit Config* (файл `%APPDATA%\Claude\claude_desktop_config.json`).
Правьте, когда Claude Desktop **закрыт** — иначе приложение может перезаписать файл.

```json
{
  "mcpServers": {
    "pulsonix": {
      "command": "pulsonix-mcp",
      "args": []
    }
  }
}
```

### OpenAI Codex CLI

```bash
codex mcp add pulsonix -- pulsonix-mcp
```

или в `~/.codex/config.toml`:

```toml
[mcp_servers.pulsonix]
command = "pulsonix-mcp"
args = []
tool_timeout_sec = 180
```

### Cursor

`~/.cursor/mcp.json` (глобально) или `.cursor/mcp.json` в проекте:

```json
{
  "mcpServers": {
    "pulsonix": { "command": "pulsonix-mcp", "args": [] }
  }
}
```

### VS Code (GitHub Copilot, режим Agent)

`.vscode/mcp.json` в проекте (или *MCP: Add Server* из палитры команд):

```json
{
  "servers": {
    "pulsonix": { "type": "stdio", "command": "pulsonix-mcp", "args": [] }
  }
}
```

### Gemini CLI

`~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "pulsonix": { "command": "pulsonix-mcp", "args": [], "timeout": 180000 }
  }
}
```

### Windsurf / Cline / LM Studio / другие

Используют тот же формат `mcpServers`, что и Claude Desktop:
Windsurf — `~/.codeium/windsurf/mcp_config.json`, Cline — *MCP Servers → Configure*,
LM Studio — `mcp.json` в настройках программы.

### Свой агент (Python MCP SDK)

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(command="pulsonix-mcp")
async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
    await s.initialize()
    res = await s.call_tool("pulsonix_bom", {"design": r"C:\path\design.sch"})
```

Проверить сервер вручную: `npx @modelcontextprotocol/inspector pulsonix-mcp`.

## Как это работает

```
Агент ──MCP/stdio──► pulsonix_mcp (Python)
                         │ генерирует job.jse (runtime + ops + параметры)
                         ├─ headless: Pulsonix.exe -hidden -scriptfile job.jse     (~2 с на вызов)
                         └─ live:     DDE "Pulsonix|System" → [psx_script_file("job.jse")]
                                       ▼
                    Pulsonix выполняет скрипт → result.json (UTF-16) → Python → агент
```

* **headless** (по умолчанию): скрытый Pulsonix открывает *временную копию* файла из параметра `design`.
  Инструменты записи без `save`/`save_as` — пробный прогон (dry run), оригинал не меняется.
  `save=true` перезаписывает оригинал, предварительно сохранив `<имя>.bak-ГГГГММДД-ччммсс.<ext>`;
  `save_as` пишет результат в новый файл.
* **live** (`mode="live"`): работа с уже открытым окном Pulsonix — активный документ или тот, что указан в `design`.
  Изменения видны в окне сразу; сохраняете сами (или `save=true`).
* Не сохраняйте в headless-режиме файл, который сейчас открыт в окне Pulsonix: окно останется со старой версией.

## Инструменты

| Чтение | Запись (dry run без save) | Вывод / прочее |
|---|---|---|
| `pulsonix_status` | `pulsonix_set_component_attributes` | `pulsonix_write_plots` |
| `pulsonix_design_summary` | `pulsonix_set_net_attributes` | `pulsonix_run_report` |
| `pulsonix_list_components` | `pulsonix_rename_components` | `pulsonix_generate_output` (STEP/ODB++/IPC-2581, PCB) |
| `pulsonix_get_component` | `pulsonix_move_component` | `pulsonix_run_command` (live) |
| `pulsonix_list_nets` / `pulsonix_get_net` | `pulsonix_set_fitted` | `pulsonix_run_macro` (live) |
| `pulsonix_netlist` | `pulsonix_add_component` | `pulsonix_run_script` (произвольный JScript) |
| `pulsonix_list_parts` / `pulsonix_bom` | `pulsonix_add_net` / `pulsonix_add_node` | `pulsonix_api_reference` |
| `pulsonix_list_texts` / `pulsonix_list_attribute_names` | `pulsonix_set_design_properties` | |
| `pulsonix_design_rule_check` / `pulsonix_list_cam_plots` / `pulsonix_library_parts` | | |

Координаты — в миллиметрах (1 мм = 100000 DSU). Выводы в цепях — `REF:PIN` (например `KM1:9`).

`pulsonix_api_reference` при первом вызове извлекает справочник API из `Scripting.chm` вашей установки
Pulsonix (через `hh.exe`) в рабочую папку. Сама справка © WestDev Ltd и в репозиторий не входит.

Примеры запросов агенту:

- «Сделай BOM по схеме `C:\work\board.sch` и сохрани в Excel»
- «Найди компоненты без атрибута Value и неподключённые выводы»
- «Переименуй R1..R20 в R101..R120 и сохрани в `board_v2.sch`»
- «В открытом Pulsonix передвинь C5 на (120; 45) мм»

## Настройки

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PULSONIX_EXE` | `C:\Program Files (x86)\Pulsonix10.5\Pulsonix.exe` | путь к Pulsonix.exe |
| `PULSONIX_MCP_WORKDIR` | `%TEMP%\pulsonix_mcp` | рабочая папка (скрипты, копии, кэш справки) |
| `PULSONIX_MCP_TIMEOUT` | `120` | таймаут одной операции, с |
| `PULSONIX_MCP_KEEP_JOBS` | — | `1` = не удалять сгенерированные скрипты (отладка) |

Пример с переменными (Claude Desktop / Cursor и т. п.):

```json
"pulsonix": {
  "command": "pulsonix-mcp",
  "env": { "PULSONIX_EXE": "D:\\Pulsonix10.5\\Pulsonix.exe", "PULSONIX_MCP_TIMEOUT": "300" }
}
```

## Особенности Pulsonix 10.x, учтённые в сервере

* COM-объект `Pulsonix.Application` зарегистрирован, но наружу методов не отдаёт — управление только через скрипты,
  которые Pulsonix выполняет сам (`-scriptfile` или DDE-команда `psx_script_file`).
* Движок скрипта выбирается по расширению файла через реестр Windows. `.js` часто перехвачен редакторами
  (WebStorm, VS Code…) — тогда у ассоциации нет ключа `ScriptEngine`, и скрипты молча не запускаются.
  Поэтому сервер пишет скрипты как `.jse` (движок `JScript.Encode` исполняет обычный JScript).
* В JScript-движке Pulsonix нет объекта `JSON` — сериализатор свой (`pulsonix_mcp/jscript/runtime.jse`).
* `Document.Save()` возвращает не `true` даже при успехе — сохранение проверяется по времени изменения файла.
* `Component.Pads` в схеме отдаёт выводы только первого гейта; связь вывод→цепь строится по узлам цепей.
* DDE-клиент из `pywin32` способен уронить Pulsonix (heap corruption) — в сервере собственный клиент DDEML на ctypes.

## Разработка и тесты

```bash
python tests/smoke_test.py   path\to\design.sch   # мост, headless, только чтение
python tests/mcp_client_test.py path\to\design.sch  # MCP по stdio; запись только в %TEMP%
python tests/live_test.py                         # нужен открытый Pulsonix с документом
```

Структура:

```
pulsonix_mcp/
  server.py         MCP-инструменты (FastMCP)
  bridge.py         запуск скриптов: headless / live, копии, бэкапы
  dde.py            DDEML-клиент (ctypes, Unicode)
  apidocs.py        извлечение справки из Scripting.chm
  jscript/runtime.jse   JSON-сериализатор и хелперы для скриптов
  jscript/ops.jse       операции (OPS.summary, OPS.bom, ...)
```

Новая операция = функция `OPS.<имя>(D, A)` в `ops.jse` + обёртка `@mcp.tool()` в `server.py`.
