"""El pipeline no puede seguir adelante con un árbol que quedó en otro commit.

Entre el 2026-08-18 23:44 y el 2026-08-20, `/srv/onnix/work` tenía un
`tailwind.css` modificado. `git checkout --detach <sha>` abortaba —un archivo
trackeado y modificado bloquea el cambio de rama, y el `clean -fd` de la línea
siguiente solo borra lo NO trackeado— y el pipeline ignoraba el error: corría la
suite sobre el commit viejo y construía la imagen desde ese mismo árbol, con el
SHA nuevo en el label y en `/health`.

El resultado fue un verde que no hablaba del commit y un deploy que no lo
contenía: producción reportaba `b803db7` y adentro no estaban ni `money.py` ni
`crm_followup.html`.

Estos tests corren la función real del pipeline contra repos git de verdad en
tmp_path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "pipeline.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repo_con_dos_commits(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """Devuelve (origen, clon, sha_viejo, sha_nuevo) con el clon en el viejo."""
    origen = tmp_path / "origen"
    origen.mkdir()
    _git(origen, "init", "--quiet", "-b", "main")
    _git(origen, "config", "user.email", "pytest@onnixtest.com")
    _git(origen, "config", "user.name", "pytest")

    (origen / "app.txt").write_text("uno\n")
    _git(origen, "add", "app.txt")
    _git(origen, "commit", "--quiet", "-m", "uno")
    viejo = _git(origen, "rev-parse", "HEAD")

    (origen / "app.txt").write_text("dos\n")
    _git(origen, "commit", "--quiet", "-am", "dos")
    nuevo = _git(origen, "rev-parse", "HEAD")

    clon = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "--quiet", str(origen), str(clon)], check=True,
    )
    _git(clon, "checkout", "--quiet", "--detach", viejo)
    return origen, clon, viejo, nuevo


def _checkout_o_morir(dir_: Path, sha: str, tmp_path: Path) -> int:
    """Sourcea el pipeline real y llama a la función, sin disparar la cola."""
    guion = (
        f'export ONNIX_QUEUE={tmp_path}/queue ONNIX_LOGDIR={tmp_path}/logs '
        f'ONNIX_STATUS={tmp_path}/status.json; '
        f'mkdir -p "$ONNIX_QUEUE"; '
        f'source {PIPELINE}; '
        f'checkout_o_morir {dir_} {sha} "el árbol de prueba"'
    )
    return subprocess.run(["bash", "-c", guion], capture_output=True, text=True).returncode


def test_un_archivo_modificado_no_congela_el_arbol(tmp_path):
    """El caso real: `tailwind.css` modificado bloqueaba todos los checkouts."""
    _, clon, viejo, nuevo = _repo_con_dos_commits(tmp_path)
    (clon / "app.txt").write_text("modificado a mano\n")
    assert _git(clon, "rev-parse", "HEAD") == viejo

    rc = _checkout_o_morir(clon, nuevo, tmp_path)

    assert rc == 0
    assert _git(clon, "rev-parse", "HEAD") == nuevo
    assert (clon / "app.txt").read_text() == "dos\n"


def test_un_sha_que_no_existe_falla_en_vez_de_dejar_el_arbol_viejo(tmp_path):
    """La prueba negativa: no poder aplicar el SHA tiene que devolver != 0."""
    _, clon, viejo, _ = _repo_con_dos_commits(tmp_path)
    inexistente = "0" * 40

    rc = _checkout_o_morir(clon, inexistente, tmp_path)

    assert rc != 0
    assert _git(clon, "rev-parse", "HEAD") == viejo


def test_el_archivo_no_trackeado_se_limpia_pero_env_y_logs_sobreviven(tmp_path):
    """`clean` no puede llevarse el .env con las credenciales ni los logs."""
    _, clon, _, nuevo = _repo_con_dos_commits(tmp_path)
    (clon / ".env").write_text("POSTGRES_USER=dummy\n")
    (clon / "logs").mkdir()
    (clon / "logs" / "suite.log").write_text("corrida anterior\n")
    (clon / "basura.tmp").write_text("de una corrida vieja\n")

    rc = _checkout_o_morir(clon, nuevo, tmp_path)

    assert rc == 0
    assert (clon / ".env").exists()
    assert (clon / "logs" / "suite.log").exists()
    assert not (clon / "basura.tmp").exists()
