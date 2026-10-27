# CLAUDE.md — NotebookLM como cerebro del proyecto

<!--
Plantilla para usar notebooklm-py desde Claude Code en CUALQUIER proyecto.

Cómo usarla:
  Forma automática: python notebooklm_sync.py setup --project <ruta> (ver README.md).

  Forma manual:
  1. Copia este archivo a la raíz de tu proyecto renombrándolo a CLAUDE.md
     (o pega la sección "NotebookLM" en tu CLAUDE.md existente).
     Para que aplique a todos tus proyectos, pégala en ~/.claude/CLAUDE.md.
  2. Sustituye <NOTEBOOK_ID>, <PERFIL> y <NOMBRE_PROYECTO>.
     Obtén el ID con: notebooklm list --json
  3. Requisitos (una vez, en tu máquina):
       uv tool install "notebooklm-py[browser,headless]"
       notebooklm --profile <PERFIL> login --master-token --account <cuenta-dedicada>@gmail.com
       notebooklm skill install
  Puedes borrar este comentario después.
-->

## NotebookLM (memoria y documentación del proyecto)

NotebookLM es la fuente de verdad sobre la documentación, las decisiones y la
investigación de **<NOMBRE_PROYECTO>**. Gemini lee y analiza las fuentes en el
servidor; tú (Claude) coordinas y haces el trabajo final. Úsalo para ahorrar
tokens y para responder con citas en vez de suponer.

- Cuaderno del proyecto: `<NOTEBOOK_ID>`
- Perfil: `<PERFIL>` → antepón siempre `notebooklm --profile <PERFIL>` a cada comando
  (abajo se omite por brevedad).

### Reglas obligatorias

1. Usa siempre `--json` y `-n <NOTEBOOK_ID>` explícito. No uses `notebooklm use`.
2. Antes de empezar una sesión de trabajo, si un comando falla por autenticación, ejecuta
   `notebooklm auth check --test --json` (requiere `status == "ok"` y
   `checks.token_fetch == true`). Si falla, prueba `notebooklm auth refresh`; si sigue
   fallando, **para y pídeme** que haga login. Nunca imprimas ni hagas commit de
   `storage_state.json` ni de `master_token.json`.
3. Tras añadir una fuente, guarda su `.source.id` y ejecuta `source wait <id>` antes de
   preguntar o generar nada con ella.
4. Las acciones destructivas (`delete`, `source delete`, `ask --new`) requieren mi
   confirmación explícita.
5. En operaciones masivas, haz pausas entre llamadas (Google limita el número de
   peticiones).

### Cuándo consultar el cuaderno

- **Antes de diseñar o cambiar algo importante** (arquitectura, API pública, modelo de
  datos), pregunta qué hay decidido:
  ```bash
  notebooklm ask -n <NOTEBOOK_ID> "¿Qué decisiones existen sobre <tema>?" --json
  ```
  Usa las citas de la respuesta y menciónalas cuando justifiques una decisión.
- **Para buscar pasajes concretos** sin gastar una respuesta de chat:
  ```bash
  notebooklm source search -n <NOTEBOOK_ID> "<términos>" --limit 5 --json
  ```

### Investigar un tema nuevo

No leas decenas de páginas web tú mismo. Deja que NotebookLM investigue e importe las
fuentes, y después pregunta:
```bash
notebooklm source add-research -n <NOTEBOOK_ID> "<tema>" --mode deep --import-all
notebooklm ask -n <NOTEBOOK_ID> "Resume lo esencial sobre <tema> para este proyecto" --json
```

### Añadir documentación al cuaderno

```bash
notebooklm source add -n <NOTEBOOK_ID> ./docs/arquitectura.md --json   # archivo
notebooklm source add -n <NOTEBOOK_ID> "https://..." --json            # URL
notebooklm source wait -n <NOTEBOOK_ID> <source_id> --timeout 300
```
Cuando cambie un documento importante del repo que ya está en el cuaderno, avísame para
reemplazar la fuente (no la borres sin confirmación).

### Memoria entre sesiones

- **Al empezar** una tarea no trivial, recupera el contexto reciente:
  ```bash
  notebooklm ask -n <NOTEBOOK_ID> "¿Qué se decidió y qué quedó pendiente en las últimas sesiones?" --json
  ```
- **Al terminar** una tarea relevante (decisión de diseño, bug difícil, convención
  nueva), guarda una nota breve:
  ```bash
  notebooklm note create -n <NOTEBOOK_ID> -t "Decisión: <título>" "<qué, por qué, alternativas descartadas>" --json
  ```
- Para guardar una respuesta útil con sus citas:
  `notebooklm ask -n <NOTEBOOK_ID> "<pregunta>" --save-as-note --note-title "<título>"`

### Generar y descargar contenido (solo si te lo pido)

```bash
notebooklm generate report -n <NOTEBOOK_ID> --format briefing-doc --wait
notebooklm download report -n <NOTEBOOK_ID> ./out/briefing.md
notebooklm generate audio -n <NOTEBOOK_ID> --wait          # podcast
notebooklm download audio -n <NOTEBOOK_ID> ./out/podcast.mp3
```
Otros tipos: `slide-deck`, `quiz`, `flashcards`, `mind-map`, `infographic`, `video`.
Para descargar todos los de un tipo: `download <tipo> --all`.

### Si algo falla

- Errores de RPC o respuestas extrañas → puede que Google haya cambiado su API interna:
  sugiere `uv tool upgrade notebooklm-py` y reintenta.
- Referencia completa: `notebooklm --help` o la skill `notebooklm` instalada.
