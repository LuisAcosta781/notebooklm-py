#!/usr/bin/env python3
"""Automatiza NotebookLM como "cerebro del proyecto" para Claude Code.

Un solo comando prepara un proyecto:

    python notebooklm_sync.py setup --project RUTA/A/TU/PROYECTO

y desde entonces la documentación se mantiene sincronizada sola (hook de
Claude Code al iniciar sesión) o a mano con:

    python .notebooklm-sync/notebooklm_sync.py sync

Subcomandos:
    setup   Crea (o reutiliza) el cuaderno, genera/actualiza CLAUDE.md,
            sube la documentación y registra el hook SessionStart.
    sync    Sube documentos nuevos, reemplaza los modificados y retira los
            borrados. Sin cambios locales no hace ninguna llamada de red.
    status  Muestra qué se subiría o borraría, sin tocar nada.

Requisitos: Python 3.10+, solo biblioteca estándar, y la CLI `notebooklm`
instalada y autenticada (`uv tool install "notebooklm-py[browser]"` y
`notebooklm login`). El script usa la CLI con `--json`, nunca lee ni copia
credenciales.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATE_DIR = ".notebooklm-sync"
STATE_FILE = "sync.json"
TEMPLATE_NAME = "CLAUDE.notebooklm.md"
SECTION_HEADING = "## NotebookLM"

# Extensiones que el endpoint de subida de NotebookLM acepta
# (``_UPLOAD_FILE_EXTENSIONS`` en src/notebooklm/_types/sources.py).
UPLOADABLE = {".csv", ".docx", ".epub", ".md", ".markdown", ".pdf", ".pptx", ".txt"}

DEFAULT_INCLUDE = [
    "README*",
    "ARCHITECTURE*",
    "CONTRIBUTING*",
    "CHANGELOG*",
    "docs/**",
    "doc/**",
    "adr/**",
    "design/**",
]
DEFAULT_EXCLUDE = [
    "CLAUDE.md",
    "**/CLAUDE.md",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/bin/**",
    "**/obj/**",
    "**/dist/**",
    "**/build/**",
]
SKIP_DIRS = {".git", STATE_DIR, "node_modules", ".venv", "venv", "__pycache__"}

MAX_FILE_MB = 50
SOURCE_WARN_LIMIT = 45  # Las cuentas gratuitas admiten 50 fuentes por cuaderno.
DELAY_SECONDS = 1.5  # Pausa entre llamadas para no chocar con el rate limit.
WAIT_TIMEOUT = 300


class SyncError(RuntimeError):
    """Fallo que se muestra al usuario sin traceback."""


# --------------------------------------------------------------------------- CLI


@dataclass
class Cli:
    exe: str
    profile: str | None
    quiet: bool = False

    def run(self, *args: str, allow_codes: tuple[int, ...] = (0,)) -> tuple[int, Any]:
        cmd = [self.exe]
        if self.profile:
            cmd += ["--profile", self.profile]
        cmd += list(args)
        env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        data: Any = None
        out = proc.stdout.strip()
        if out:
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                data = None
        if proc.returncode not in allow_codes:
            if isinstance(data, dict) and data.get("message"):
                detail = str(data["message"])
            else:
                lines = (proc.stderr or proc.stdout).strip().splitlines()
                detail = lines[-1] if lines else ""
            raise SyncError(
                f"`notebooklm {' '.join(args[:2])}` falló (código {proc.returncode}): {detail}"
            )
        return proc.returncode, data

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)


def find_cli() -> str:
    exe = shutil.which("notebooklm")
    if not exe:
        raise SyncError(
            "No encuentro la CLI `notebooklm`. Instálala con:\n"
            '    uv tool install "notebooklm-py[browser]"\n'
            "y después ejecuta `notebooklm login`."
        )
    return exe


def check_auth(cli: Cli) -> None:
    _, data = cli.run("auth", "check", "--test", "--json", allow_codes=(0, 1, 2))
    checks = (data or {}).get("checks") or {}
    if (data or {}).get("status") != "ok" or checks.get("token_fetch") is not True:
        profile = f" --profile {cli.profile}" if cli.profile else ""
        raise SyncError(
            "La autenticación de NotebookLM no es válida. Prueba:\n"
            f"    notebooklm{profile} auth refresh\n"
            f"y si sigue fallando:  notebooklm{profile} login"
        )


# ------------------------------------------------------------------------ estado


def state_path(project: Path) -> Path:
    return project / STATE_DIR / STATE_FILE


def load_state(project: Path) -> dict[str, Any]:
    path = state_path(project)
    if not path.exists():
        raise SyncError(f"{project} no está configurado. Ejecuta primero `setup`.")
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(project: Path, state: dict[str, Any]) -> None:
    path = state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)  # Escritura atómica: un corte no deja el estado a medias.


# --------------------------------------------------------------------- archivos


def _matches(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
        # "docs/**" debe incluir también archivos directamente en docs/.
        if pat.endswith("/**") and rel.startswith(pat[:-2]):
            return True
    return False


def collect_files(project: Path, include: list[str], exclude: list[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root, dirs, files in os.walk(project):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            path = Path(root) / name
            rel = path.relative_to(project).as_posix()
            if path.suffix.lower() not in UPLOADABLE:
                continue
            if not _matches(rel, include) or _matches(rel, exclude):
                continue
            if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
                print(f"  ! Omito {rel}: supera {MAX_FILE_MB} MB", file=sys.stderr)
                continue
            found[rel] = path
    return found


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Plan:
    add: list[str]
    replace: list[str]
    remove: list[str]

    @property
    def empty(self) -> bool:
        return not (self.add or self.replace or self.remove)


def build_plan(
    project: Path, state: dict[str, Any]
) -> tuple[Plan, dict[str, Path], dict[str, str]]:
    files = collect_files(project, state["include"], state["exclude"])
    hashes = {rel: sha256(path) for rel, path in files.items()}
    tracked: dict[str, Any] = state["sources"]
    plan = Plan(
        add=[rel for rel in files if rel not in tracked],
        replace=[rel for rel in files if rel in tracked and tracked[rel]["sha256"] != hashes[rel]],
        remove=[rel for rel in tracked if rel not in files],
    )
    return plan, files, hashes


# ------------------------------------------------------------------ sincronizar


def upload(cli: Cli, notebook_id: str, rel: str, path: Path) -> str:
    _, data = cli.run(
        "source", "add", str(path), "--type", "file", "--title", rel,
        "-n", notebook_id, "--json",
    )  # fmt: skip
    try:
        return str(data["source"]["id"])
    except (TypeError, KeyError) as exc:
        raise SyncError(f"Respuesta inesperada al subir {rel}: {data!r}") from exc


def wait_ready(cli: Cli, notebook_id: str, source_id: str, rel: str) -> bool:
    code, _ = cli.run(
        "source", "wait", source_id, "-n", notebook_id,
        "--timeout", str(WAIT_TIMEOUT), "--json",
        allow_codes=(0, 1, 2),
    )  # fmt: skip
    if code == 0:
        return True
    reason = "tiempo de espera agotado" if code == 2 else "procesamiento fallido"
    print(f"  ! {rel}: {reason} (id {source_id})", file=sys.stderr)
    return False


def delete_source(cli: Cli, notebook_id: str, source_id: str) -> None:
    # Código 1 = ya no existe (borrada a mano en la web): no es un error aquí.
    cli.run("source", "delete", source_id, "-n", notebook_id, "-y", "--json", allow_codes=(0, 1))


def sync(cli: Cli, project: Path, state: dict[str, Any], *, dry_run: bool = False) -> Plan:
    plan, files, hashes = build_plan(project, state)
    if plan.empty or dry_run:
        return plan

    check_auth(cli)
    notebook_id = state["notebook_id"]
    tracked: dict[str, Any] = state["sources"]

    total = len(tracked) + len(plan.add) - len(plan.remove)
    if total > SOURCE_WARN_LIMIT:
        print(
            f"  ! El cuaderno tendrá {total} fuentes; las cuentas gratuitas admiten 50. "
            "Acota --include si falla.",
            file=sys.stderr,
        )

    pending: list[tuple[str, str]] = []

    # Primero se sube la versión nueva y solo después se borra la vieja: si la
    # subida falla, el cuaderno conserva el documento anterior.
    for rel in plan.replace:
        cli.log(f"  ~ {rel}")
        new_id = upload(cli, notebook_id, rel, files[rel])
        delete_source(cli, notebook_id, tracked[rel]["id"])
        tracked[rel] = {"id": new_id, "sha256": hashes[rel]}
        save_state(project, state)
        pending.append((new_id, rel))
        time.sleep(DELAY_SECONDS)

    for rel in plan.add:
        cli.log(f"  + {rel}")
        new_id = upload(cli, notebook_id, rel, files[rel])
        tracked[rel] = {"id": new_id, "sha256": hashes[rel]}
        save_state(project, state)
        pending.append((new_id, rel))
        time.sleep(DELAY_SECONDS)

    for rel in plan.remove:
        cli.log(f"  - {rel}")
        delete_source(cli, notebook_id, tracked[rel]["id"])
        del tracked[rel]
        save_state(project, state)
        time.sleep(DELAY_SECONDS)

    for source_id, rel in pending:
        wait_ready(cli, notebook_id, source_id, rel)

    return plan


def describe(plan: Plan) -> str:
    if plan.empty:
        return "NotebookLM: documentación al día."
    return (
        f"NotebookLM: {len(plan.add)} nuevos, {len(plan.replace)} actualizados, "
        f"{len(plan.remove)} retirados."
    )


# ------------------------------------------------------------------- CLAUDE.md


def find_template(explicit: str | None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    here = Path(__file__).resolve().parent
    candidates += [here / TEMPLATE_NAME, here.parent / TEMPLATE_NAME]
    for path in candidates:
        if path.is_file():
            return path
    raise SyncError(
        f"No encuentro la plantilla {TEMPLATE_NAME}. Ponla junto a este script o usa --template."
    )


def render_template(template: Path, *, notebook_id: str, name: str, profile: str) -> str:
    text = template.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL)  # instrucciones de uso
    return (
        text.replace("<NOTEBOOK_ID>", notebook_id)
        .replace("<NOMBRE_PROYECTO>", name)
        .replace("<PERFIL>", profile)
    )


def write_claude_md(project: Path, rendered: str, *, force: bool) -> str:
    target = project / "CLAUDE.md"
    section = rendered[rendered.index(SECTION_HEADING) :]
    if not target.exists():
        target.write_text(rendered, encoding="utf-8")
        return "creado"

    current = target.read_text(encoding="utf-8")
    if SECTION_HEADING in current:
        if not force:
            return "ya tenía la sección NotebookLM (sin cambios; usa --force para reemplazarla)"
        start = current.index(SECTION_HEADING)
        # La sección termina en el siguiente encabezado de nivel 2 ajeno o al final.
        nxt = re.search(r"^## (?!NotebookLM)", current[start + 1 :], flags=re.MULTILINE)
        end = start + 1 + nxt.start() if nxt else len(current)
        target.write_text(
            current[:start] + section.rstrip() + "\n\n" + current[end:].lstrip(), encoding="utf-8"
        )
        return "sección NotebookLM reemplazada"

    target.write_text(current.rstrip() + "\n\n" + section, encoding="utf-8")
    return "sección NotebookLM añadida al final"


# ------------------------------------------------------------------------ hook


def install_hook(project: Path, script: Path) -> str:
    """Registra un hook SessionStart en .claude/settings.local.json.

    Se usa settings.local.json (no versionado) porque el comando lleva la ruta
    absoluta del intérprete de Python de esta máquina.
    """
    settings_path = project / ".claude" / "settings.local.json"
    settings: dict[str, Any] = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")

    python = Path(sys.executable).as_posix()
    command = f'"{python}" "$CLAUDE_PROJECT_DIR/{STATE_DIR}/{script.name}" sync --quiet'
    groups = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    existing = [
        hook
        for group in groups
        for hook in group.get("hooks", [])
        if script.name in hook.get("command", "")
    ]
    if existing:
        existing[0]["command"] = command  # Re-ejecutar setup no duplica el hook.
    else:
        groups.append(
            {
                "matcher": "startup",
                "hooks": [{"type": "command", "command": command, "timeout": 900}],
            }
        )

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings_path.relative_to(project).as_posix()


# ---------------------------------------------------------------- subcomandos


def cmd_setup(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise SyncError(f"{project} no es un directorio.")
    template = find_template(args.template)
    cli = Cli(find_cli(), args.profile)
    name = args.name or project.name

    cli.log(f"Proyecto: {project}")
    check_auth(cli)
    cli.log("✓ Autenticación correcta")

    previous = state_path(project)
    state: dict[str, Any] = (
        json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else {}
    )

    notebook_id = args.notebook_id or state.get("notebook_id")
    if notebook_id:
        cli.log(f"✓ Reutilizo el cuaderno {notebook_id}")
    else:
        _, data = cli.run("create", name, "--json")
        notebook_id = str(data["notebook"]["id"])
        cli.log(f"✓ Cuaderno creado: {name} ({notebook_id})")

    state.update(
        {
            "notebook_id": notebook_id,
            "profile": args.profile,
            "include": args.include or state.get("include") or DEFAULT_INCLUDE,
            "exclude": state.get("exclude") or DEFAULT_EXCLUDE,
        }
    )
    state.setdefault("sources", {})
    save_state(project, state)

    # Copia el script dentro del proyecto para que el hook no dependa de dónde
    # se descargó, y deja un .gitignore que mantiene el estado fuera de git.
    local_script = project / STATE_DIR / Path(__file__).name
    if Path(__file__).resolve() != local_script.resolve():
        shutil.copy2(__file__, local_script)
    (project / STATE_DIR / ".gitignore").write_text("*\n", encoding="utf-8")

    rendered = render_template(
        template, notebook_id=notebook_id, name=name, profile=args.profile or "default"
    )
    cli.log(f"✓ CLAUDE.md: {write_claude_md(project, rendered, force=args.force)}")

    if not args.no_hook:
        cli.log(f"✓ Hook SessionStart registrado en {install_hook(project, local_script)}")

    cli.log("Subiendo documentación…")
    plan = sync(cli, project, state)
    cli.log(f"✓ {describe(plan)}")
    if not state["sources"]:
        cli.log("  (No encontré documentos. Ajusta --include, por ejemplo: --include 'specs/**')")
    cli.log("\nListo. Abre Claude Code en el proyecto y escribe /memory para ver el CLAUDE.md.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    state = load_state(project)
    cli = Cli(find_cli(), state.get("profile"), quiet=args.quiet)
    plan = sync(cli, project, state, dry_run=args.dry_run)
    # En modo hook (--quiet) esta línea llega al contexto de Claude.
    print(describe(plan))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    state = load_state(project)
    plan, _, _ = build_plan(project, state)
    print(f"Cuaderno: {state['notebook_id']}  ·  fuentes sincronizadas: {len(state['sources'])}")
    for label, items in (
        ("+ nuevo", plan.add),
        ("~ cambiado", plan.replace),
        ("- borrado", plan.remove),
    ):
        for rel in items:
            print(f"  {label}: {rel}")
    print(describe(plan))
    return 0


def default_project() -> str:
    # Si el script vive en <proyecto>/.notebooklm-sync/, el proyecto es su padre.
    here = Path(__file__).resolve().parent
    if here.name == STATE_DIR:
        return str(here.parent)
    return os.environ.get("CLAUDE_PROJECT_DIR", ".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Configura un proyecto de principio a fin")
    p_setup.add_argument(
        "--project", default=".", help="Raíz del proyecto (por defecto: directorio actual)"
    )
    p_setup.add_argument(
        "--name", help="Nombre del cuaderno y del proyecto (por defecto: nombre de la carpeta)"
    )
    p_setup.add_argument("--profile", help="Perfil de notebooklm (cuenta de Google) a usar")
    p_setup.add_argument(
        "--notebook-id", help="Reutiliza un cuaderno existente en vez de crear uno"
    )
    p_setup.add_argument(
        "--include", action="append", metavar="GLOB",
        help="Patrón de archivos a subir (repetible). Por defecto: README*, docs/**, adr/**…",
    )  # fmt: skip
    p_setup.add_argument("--template", help=f"Ruta a {TEMPLATE_NAME}")
    p_setup.add_argument(
        "--force", action="store_true", help="Reemplaza la sección NotebookLM de CLAUDE.md"
    )
    p_setup.add_argument(
        "--no-hook", action="store_true", help="No registrar el hook de Claude Code"
    )
    p_setup.set_defaults(func=cmd_setup)

    p_sync = sub.add_parser("sync", help="Sincroniza la documentación con el cuaderno")
    p_sync.add_argument("--project", default=default_project())
    p_sync.add_argument("--quiet", action="store_true", help="Solo imprime el resumen (modo hook)")
    p_sync.add_argument("--dry-run", action="store_true", help="Calcula cambios sin aplicarlos")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status", help="Muestra cambios pendientes sin tocar nada")
    p_status.add_argument("--project", default=default_project())
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SyncError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
