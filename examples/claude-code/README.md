# NotebookLM + Claude Code

Convierte un cuaderno de NotebookLM en la memoria y la documentación de un
proyecto que Claude Code consulta y mantiene al día.

| Archivo | Para qué |
|---|---|
| `CLAUDE.notebooklm.md` | Plantilla de `CLAUDE.md` con las reglas para que Claude use el cuaderno |
| `notebooklm_sync.py` | Automatiza la configuración y la sincronización de la documentación |

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
