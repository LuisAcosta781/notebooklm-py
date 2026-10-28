#!/usr/bin/env python3
"""Mantiene viva la sesión de NotebookLM en Windows con una tarea programada.

Google rota la cookie de frescura de la sesión (``__Secure-1PSIDTS``) cada
poco tiempo; si nadie usa NotebookLM durante horas, la sesión caduca y toca
volver a hacer ``notebooklm login``. Esta tarea ejecuta
``notebooklm auth refresh`` cada 15 minutos (la cadencia recomendada en
docs/cli-reference.md) en segundo plano, sin abrir ventanas.

Uso (en PowerShell o CMD, una sola vez):

    python notebooklm_autorefresh.py install
    python notebooklm_autorefresh.py install --profile trabajo --profile personal

Otros subcomandos:

    status      Muestra si la tarea existe y las últimas ejecuciones.
    run         Ejecuta un refresco ahora (es lo que lanza la tarea).
    uninstall   Elimina la tarea y sus archivos.

La tarea se registra para tu usuario, sin permisos de administrador, y solo se
ejecuta mientras tienes la sesión de Windows iniciada. Si el equipo estuvo
apagado o suspendido, se ejecuta en cuanto vuelve a estar disponible.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "NotebookLM Auth Refresh"
DEFAULT_INTERVAL = 15
LOG_MAX_LINES = 1000
RUN_TIMEOUT = 240  # segundos por perfil; la tarea entera se corta a los 10 min.


class RefreshError(RuntimeError):
    """Fallo que se muestra al usuario sin traceback."""


def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "notebooklm-autorefresh"


def config_path() -> Path:
    return app_dir() / "config.json"


def log_path() -> Path:
    return app_dir() / "refresh.log"


# ------------------------------------------------------------------------- run


def append_log(lines: list[str]) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = (old + lines)[-LOG_MAX_LINES:]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def refresh_profile(exe: str, profile: str | None) -> tuple[int, str]:
    cmd = [exe]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["auth", "refresh", "--quiet"]
    # Sin ventana de consola en Windows aunque el proceso hijo sea un .exe de consola.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=RUN_TIMEOUT,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"sin respuesta tras {RUN_TIMEOUT} s"
    except OSError as exc:
        return 127, f"no se pudo ejecutar {exe}: {exc}"
    output = " | ".join(
        line.strip() for line in (proc.stdout + proc.stderr).splitlines() if line.strip()
    )
    return proc.returncode, output


def cmd_run(_: argparse.Namespace) -> int:
    config: dict = {}
    if config_path().exists():
        config = json.loads(config_path().read_text(encoding="utf-8"))
    exe = config.get("exe")
    if not exe or not Path(exe).exists():
        exe = shutil.which("notebooklm")  # ruta guardada obsoleta (p. ej. reinstalación)
    profiles = config.get("profiles") or [None]
    stamp = dt.datetime.now().isoformat(timespec="seconds")

    if not exe:
        append_log([f"{stamp}  ERROR  no encuentro la CLI `notebooklm` en el PATH"])
        return 127

    worst = 0
    lines = []
    for profile in profiles:
        code, output = refresh_profile(exe, profile)
        name = profile or "default"
        status = "OK" if code == 0 else f"ERROR {code}"
        lines.append(f"{stamp}  {status:<9} perfil={name}" + (f"  {output}" if output else ""))
        worst = max(worst, code)
    append_log(lines)
    if sys.stdout is not None:  # con pythonw no hay consola
        print("\n".join(lines))
    return worst


# --------------------------------------------------------------------- install


def build_task_xml(
    *, command: str, arguments: str, user: str, interval: int, start: dt.datetime
) -> str:
    """Definición de la tarea en el esquema de Task Scheduler 1.2.

    TimeTrigger con Repetition sin Duration = se repite indefinidamente.
    InteractiveToken = corre como tú, solo con la sesión iniciada, sin guardar
    contraseña ni pedir permisos de administrador.
    """
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Mantiene viva la sesión de notebooklm-py (notebooklm auth refresh cada {interval} min).</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT{interval}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def windowless_python() -> str:
    """pythonw.exe junto al intérprete actual: ejecuta sin abrir consola."""
    current = Path(sys.executable)
    pythonw = current.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    print(
        "  ! No encuentro pythonw.exe; la tarea usará python.exe y verás una ventana "
        "parpadear cada vez que se ejecute.",
        file=sys.stderr,
    )
    return str(current)


def schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *args], capture_output=True, text=True, errors="replace", check=False
    )


def require_windows() -> None:
    if os.name != "nt":
        raise RefreshError(
            "Este instalador es para Windows. En macOS/Linux usa cron, launchd o systemd "
            "(ver docs/troubleshooting.md)."
        )


def cmd_install(args: argparse.Namespace) -> int:
    require_windows()
    exe = shutil.which("notebooklm")
    if not exe:
        raise RefreshError(
            'No encuentro `notebooklm`. Instálalo con: uv tool install "notebooklm-py[browser]"'
        )

    profiles = args.profile or []
    print("Probando el refresco antes de programarlo…")
    for profile in profiles or [None]:
        code, output = refresh_profile(exe, profile)
        name = profile or "default"
        if code != 0:
            hint = f"notebooklm{' --profile ' + profile if profile else ''} login"
            raise RefreshError(
                f"El refresco del perfil '{name}' falló ({output or f'código {code}'}).\n"
                f"Inicia sesión primero con:  {hint}"
            )
        print(f"  ✓ perfil {name}")

    target = app_dir()
    target.mkdir(parents=True, exist_ok=True)
    script = target / Path(__file__).name
    if Path(__file__).resolve() != script.resolve():
        shutil.copy2(__file__, script)
    config_path().write_text(
        json.dumps({"exe": exe, "profiles": profiles, "interval": args.interval}, indent=2) + "\n",
        encoding="utf-8",
    )

    domain = os.environ.get("USERDOMAIN", "")
    user = (
        f"{domain}\\{os.environ.get('USERNAME', '')}" if domain else os.environ.get("USERNAME", "")
    )
    xml = build_task_xml(
        command=windowless_python(),
        arguments=f'"{script}" run',
        user=user,
        interval=args.interval,
        start=dt.datetime.now().replace(second=0, microsecond=0) + dt.timedelta(minutes=1),
    )
    # schtasks exige el XML en UTF-16 cuando la cabecera lo declara así.
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as fh:
        fh.write(xml)
        xml_file = fh.name
    try:
        result = schtasks("/Create", "/TN", args.task_name, "/XML", xml_file, "/F")
    finally:
        os.unlink(xml_file)
    if result.returncode != 0:
        raise RefreshError(
            f"schtasks no pudo crear la tarea: {(result.stderr or result.stdout).strip()}"
        )

    print(f"✓ Tarea '{args.task_name}' creada: cada {args.interval} min, sin ventanas.")
    print(f"  Registro: {log_path()}")
    print("  Compruébala con:  python notebooklm_autorefresh.py status")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    require_windows()
    result = schtasks("/Query", "/TN", args.task_name)
    if result.returncode != 0:
        print(f"✗ La tarea '{args.task_name}' no existe. Instálala con `install`.")
        return 1
    print(f"✓ La tarea '{args.task_name}' está registrada.")
    if config_path().exists():
        config = json.loads(config_path().read_text(encoding="utf-8"))
        profiles = ", ".join(config.get("profiles") or ["default"])
        print(f"  Perfiles: {profiles}  ·  cada {config.get('interval', DEFAULT_INTERVAL)} min")
    if log_path().exists():
        tail = log_path().read_text(encoding="utf-8").splitlines()[-10:]
        print(f"  Últimas ejecuciones ({log_path()}):")
        for line in tail:
            print(f"    {line}")
        if tail and "ERROR" in tail[-1]:
            print(
                "  ! El último refresco falló: ejecuta `notebooklm login` (o `auth refresh`) a mano."
            )
    else:
        print("  Aún no se ha ejecutado ninguna vez.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    require_windows()
    result = schtasks("/Delete", "/TN", args.task_name, "/F")
    if result.returncode == 0:
        print(f"✓ Tarea '{args.task_name}' eliminada.")
    else:
        print(f"  (La tarea '{args.task_name}' no existía.)")
    if app_dir().exists():
        shutil.rmtree(app_dir(), ignore_errors=True)
        print(f"✓ Eliminado {app_dir()}")
    return 0


def interval_minutes(value: str) -> int:
    minutes = int(value)
    if not 5 <= minutes <= 60:
        raise argparse.ArgumentTypeError("debe estar entre 5 y 60 minutos")
    return minutes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--task-name", default=TASK_NAME, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Crea (o actualiza) la tarea programada")
    p_install.add_argument(
        "--profile",
        action="append",
        help="Perfil de notebooklm a refrescar (repetible; por defecto: default)",
    )
    p_install.add_argument(
        "--interval",
        type=interval_minutes,
        default=DEFAULT_INTERVAL,
        metavar="MIN",
        help=f"Minutos entre refrescos (5-60, por defecto {DEFAULT_INTERVAL})",
    )
    p_install.set_defaults(func=cmd_install)

    sub.add_parser("status", help="Estado de la tarea y últimas ejecuciones").set_defaults(
        func=cmd_status
    )
    sub.add_parser("run", help="Refresca ahora (lo que ejecuta la tarea)").set_defaults(
        func=cmd_run
    )
    sub.add_parser("uninstall", help="Elimina la tarea y sus archivos").set_defaults(
        func=cmd_uninstall
    )

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RefreshError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
