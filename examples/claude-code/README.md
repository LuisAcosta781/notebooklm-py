# NotebookLM + Claude Code

Convierte un cuaderno de NotebookLM en la memoria y la documentación de un
proyecto que Claude Code consulta y mantiene al día.

| Archivo | Para qué |
|---|---|
| `CLAUDE.notebooklm.md` | Plantilla de `CLAUDE.md` con las reglas para que Claude use el cuaderno |
| `notebooklm_sync.py` | Automatiza la configuración y la sincronización de la documentación |
| `notebooklm_autorefresh.py` | Tarea programada de Windows que mantiene viva la sesión de Google |

## Requisitos (una vez)

```bash
uv tool install "notebooklm-py[browser]"
notebooklm login                 # o: notebooklm login --master-token --account <email>
notebooklm skill install
```

## Configurar un proyecto (un comando)

Descarga los dos archivos en la misma carpeta y ejecuta:

```bash
python notebooklm_sync.py setup --project /ruta/a/tu/proyecto
```

Opciones útiles: `--name "Mi Proyecto"`, `--profile trabajo`,
`--notebook-id <id>` (reutilizar un cuaderno), `--include "specs/**"` (repetible),
`--force` (reemplazar la sección NotebookLM de un `CLAUDE.md` existente) y
`--no-hook`.

`setup`:

1. Comprueba la autenticación.
2. Crea el cuaderno, o reutiliza el que ya tenga el proyecto.
3. Crea `CLAUDE.md` o añade la sección NotebookLM al existente, con el ID ya rellenado.
4. Copia el script a `<proyecto>/.notebooklm-sync/`, que queda fuera de git.
5. Registra un hook `SessionStart` en `.claude/settings.local.json`.
6. Sube la documentación: `README*`, `docs/**`, `adr/**`… en formatos
   `.md .txt .pdf .docx .pptx .csv .epub`.

## Mantenerlo al día

Con el hook, cada vez que abres Claude Code en el proyecto se suben los
documentos nuevos, se reemplazan los modificados y se retiran los borrados. Si
no hay cambios, no hace ninguna llamada de red. También puedes hacerlo a mano:

```bash
python .notebooklm-sync/notebooklm_sync.py status   # qué cambiaría
python .notebooklm-sync/notebooklm_sync.py sync     # aplicarlo
```

El script solo toca las fuentes que él mismo subió; lo que añadas al cuaderno
a mano (investigaciones, URLs, notas) no se modifica.

## Que la sesión no caduque (Windows)

Google renueva la cookie de sesión cada poco tiempo; si pasas horas sin usar
NotebookLM, tendrías que volver a hacer `notebooklm login`. Para evitarlo,
instala una tarea programada que ejecuta `notebooklm auth refresh` cada 15
minutos en segundo plano (sin ventanas ni permisos de administrador):

```powershell
python notebooklm_autorefresh.py install                      # perfil por defecto
python notebooklm_autorefresh.py install --profile trabajo    # o varios --profile
python notebooklm_autorefresh.py status                       # últimas ejecuciones
python notebooklm_autorefresh.py uninstall                    # quitarla
```

Antes de crear la tarea, `install` prueba un refresco real y se detiene si la
sesión no es válida. El registro queda en
`%LOCALAPPDATA%\notebooklm-autorefresh\refresh.log`. La tarea corre solo con
tu sesión de Windows iniciada y se pone al día sola tras apagar o suspender el
equipo. Si iniciaste sesión con `login --master-token`, el refresco también
regenera las cookies cuando han caducado del todo.

En macOS/Linux usa cron, launchd o systemd con
`notebooklm auth refresh --quiet` (ver `docs/troubleshooting.md`).
