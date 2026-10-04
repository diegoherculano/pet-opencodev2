"""Segundo plano: instância única, log e o fork de verdade.

O desvio em si é testado num processo separado
(:class:`DetachTests`), porque ``fork`` no processo da suíte trocaria os
descritores do próprio runner. Aqui o que dá para testar no lugar é tudo
que vem antes e depois do fork: o lock, a rotação do log, a leitura das
opções e o ``--stop``.
"""

from __future__ import annotations

import contextlib
import errno
import io
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from petwatch.config import LOG_PATH, PID_PATH
from petwatch.daemon import (
    LOCK_BUSY_ERRNOS,
    SingleInstance,
    parse_args,
    report_startup,
    rotate_log,
    status_line,
    stop_instance,
)

BASE_DIR = Path(__file__).resolve().parent.parent


class OptionsTests(unittest.TestCase):
    """A linha de comando."""

    def test_second_plane_is_the_default(self):
        options = parse_args([])

        self.assertFalse(options.foreground)
        self.assertFalse(options.background)

    def test_foreground_has_a_short_flag(self):
        for argv in (["--foreground"], ["-f"]):
            with self.subTest(argv=argv):
                self.assertTrue(parse_args(argv).foreground)

    def test_background_can_be_asked_for_explicitly(self):
        for argv in (["--background"], ["-b"]):
            with self.subTest(argv=argv):
                self.assertFalse(parse_args(argv).foreground)

    def test_both_modes_at_once_is_a_usage_error(self):
        # O argparse escreve o uso em ``stderr``; aqui é só ruído.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["-f", "-b"])

    def test_stop_and_status_are_flags(self):
        self.assertTrue(parse_args(["--stop"]).stop)
        self.assertTrue(parse_args(["--status"]).status)

    def test_the_log_defaults_to_the_state_directory(self):
        self.assertEqual(parse_args([]).log, LOG_PATH)

    def test_the_log_can_be_pointed_elsewhere(self):
        self.assertEqual(parse_args(["--log", "/tmp/x.log"]).log, Path("/tmp/x.log"))


class SingleInstanceTests(unittest.TestCase):
    """O lock de ``pet.pid``.

    Cada teste usa o seu próprio arquivo: o lock é do arquivo, e um teste
    que segura o lock quebraria todos os outros da classe.

    Toda instância vai por :meth:`instance`, e o ``tearDown`` solta todas.
    O lock vive no descritor que ``os.open`` devolveu — um inteiro, sem
    finalizador — então uma instância abandonada no meio de um teste
    continuaria segurando o lock até o fim da suíte.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-pid-"))

        self.path = self.directory / "pet.pid"

        self.instances: list[SingleInstance] = []

    def tearDown(self):
        for instance in self.instances:
            instance.release()

    def instance(self) -> SingleInstance:
        """Instância que o ``tearDown`` vai soltar."""

        instance = SingleInstance(self.path)

        self.instances.append(instance)

        return instance

    def test_the_first_claim_wins(self):
        self.assertTrue(self.instance().claim())

    def test_a_second_claim_finds_the_first(self):
        first = self.instance()

        self.assertTrue(first.claim())

        first.record_pid()

        second = self.instance()

        self.assertFalse(second.claim())
        self.assertEqual(second.owner, os.getpid())

    def test_the_owner_is_reset_after_a_successful_claim(self):
        first = self.instance()

        first.claim()

        first.record_pid()

        second = self.instance()

        self.assertFalse(second.claim())
        self.assertEqual(second.owner, os.getpid())

        first.release()

        # Quem pega o lock agora não é o dono anterior, e não carrega o pid
        # dele: o arquivo vai ser reescrito por quem estiver rodando.
        self.assertTrue(second.claim())
        self.assertIsNone(second.owner)

    def test_release_lets_the_next_one_in(self):
        first = self.instance()

        first.claim()

        first.release()

        self.assertTrue(self.instance().claim())

    def test_a_stale_pid_file_does_not_block_anyone(self):
        """Um ``pet.pid`` de um processo morto é só um número velho.

        Se a presença do pet fosse decidida pelo conteúdo do arquivo em
        vez do lock, um ``SIGKILL`` deixaria o app fechado para sempre até
        alguém apagar o arquivo na mão.
        """

        self.path.write_text("4242\n", encoding="ascii")

        self.assertFalse(self.instance().running())
        self.assertTrue(self.instance().claim())

    def test_running_is_true_while_someone_holds_the_lock(self):
        holder = self.instance()

        holder.claim()

        holder.record_pid()

        watcher = self.instance()

        self.assertTrue(watcher.running())
        self.assertEqual(watcher.owner, os.getpid())

    def test_running_does_not_keep_the_lock(self):
        """Senão ``--status`` viraria o dono da instância."""

        self.instance().running()

        self.assertTrue(self.instance().claim())

    def test_a_missing_file_means_nobody_is_running(self):
        watcher = self.instance()

        self.assertFalse(watcher.running())

        # E sondar não cria o arquivo.
        self.assertFalse(self.path.exists())

    def test_record_pid_writes_the_current_process(self):
        instance = self.instance()

        instance.claim()

        pid = instance.record_pid()

        self.assertEqual(pid, os.getpid())
        self.assertEqual(self.path.read_text(encoding="ascii"), f"{os.getpid()}\n")

    def test_record_pid_truncates_the_numero_antigo(self):
        self.path.write_text("999999999\n", encoding="ascii")

        instance = self.instance()

        instance.claim()
        instance.record_pid()

        self.assertEqual(self.path.read_text(encoding="ascii"), f"{os.getpid()}\n")

    def test_record_pid_without_a_claim_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.instance().record_pid()

    def test_release_without_a_claim_is_harmless(self):
        instance = self.instance()

        instance.release()
        instance.release()

    def test_the_default_path_lives_in_the_state_directory(self):
        """O caminho real é o do usuário, mas precisa existir só no nome."""

        self.assertEqual(PID_PATH.name, "pet.pid")
        self.assertEqual(PID_PATH.parent, LOG_PATH.parent)


class NoFlockTests(unittest.TestCase):
    """Sistema de arquivos sem ``flock``.

    ``ENOLCK`` acontece em rede e em alguns montagens (DrvFs do WSL,
    fuse). O sintoma de confundir esse erro com "outro pet segurando o
    lock" é ``já existe um pet rodando (pid None)``, para sempre, com
    nenhum pet na tela e sem vestígio do motivo.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-nolock-"))

        self.path = self.directory / "pet.pid"

        self.instances: list[SingleInstance] = []

    def tearDown(self):
        for instance in self.instances:
            instance.release()

    def instance(self) -> SingleInstance:
        instance = SingleInstance(self.path)

        self.instances.append(instance)

        return instance

    def unsupported(self) -> None:
        self.flock = mock.patch(
            "petwatch.daemon.fcntl.flock",
            side_effect=OSError(errno.ENOLCK, "no locks available"),
        )

        self.flock.start()

        self.addCleanup(self.flock.stop)

    def test_the_pet_still_starts(self):
        self.unsupported()

        with self.assertLogs("petwatch.daemon", level="WARNING"):
            self.assertTrue(self.instance().claim())

    def test_the_pid_can_still_be_written(self):
        """O arquivo é o registro do pid, e serve mesmo sem lock."""

        self.unsupported()

        with self.assertLogs("petwatch.daemon", level="WARNING"):
            instance = self.instance()

            instance.claim()

            pid = instance.record_pid()

        self.assertEqual(pid, os.getpid())
        self.assertEqual(self.path.read_text(encoding="ascii"), f"{os.getpid()}\n")

    def test_status_does_not_invent_a_running_pet(self):
        self.unsupported()

        self.assertFalse(self.instance().running())

    def test_the_repeated_busy_errno_is_a_busy_answer(self):
        """``EWOULDBLOCK`` e ``EAGAIN`` são o mesmo número no Linux."""

        self.assertEqual(errno.EWOULDBLOCK, errno.EAGAIN)

        self.assertIn(errno.EWOULDBLOCK, LOCK_BUSY_ERRNOS)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-status-"))

        self.path = self.directory / "pet.pid"

        self.log = self.directory / "pet.log"

        self.instances: list[SingleInstance] = []

    def tearDown(self):
        for instance in self.instances:
            instance.release()

    def instance(self) -> SingleInstance:
        instance = SingleInstance(self.path)

        self.instances.append(instance)

        return instance

    def test_says_when_nothing_is_running(self):
        line = status_line(self.instance(), self.log)

        self.assertIn("não está rodando", line)
        self.assertIn(str(self.log), line)

    def test_says_the_pid_when_it_is_running(self):
        holder = self.instance()

        holder.claim()

        pid = holder.record_pid()

        line = status_line(self.instance(), self.log)

        self.assertIn("rodando", line)
        self.assertIn(str(pid), line)


class StopTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-stop-"))

        self.path = self.directory / "pet.pid"

        self.instance = SingleInstance(self.path)

    def tearDown(self):
        # Garante o lock livre, mesmo se o teste falhou no meio.
        self.instance.release()

    def claimed(self) -> int:
        """Pet "rodando", com o pid gravado."""

        self.assertTrue(self.instance.claim())

        return self.instance.record_pid()

    def stop(self) -> tuple[int, mock.MagicMock]:
        """Roda ``--stop`` sem o barulho na saída da suíte."""

        with mock.patch("petwatch.daemon.os.kill") as kill:
            with contextlib.redirect_stdout(io.StringIO()) as said:
                code = stop_instance(self.instance)

        self.assertIsInstance(said.getvalue(), str)

        return code, kill

    def test_sigterm_is_the_signal_sent(self):
        """``SIGTERM`` e não ``SIGKILL``: o app tem handler para ele."""

        pid = self.claimed()

        _, kill = self.stop()

        kill.assert_called_once_with(pid, signal.SIGTERM)

    def test_it_waits_for_the_pet_to_be_gone(self):
        pid = self.claimed()

        def die(*_):
            """O pet recebeu o sinal e saiu, soltando o lock."""

            self.instance.release()

        with mock.patch("petwatch.daemon.os.kill", side_effect=die):
            with contextlib.redirect_stdout(io.StringIO()):
                code = stop_instance(self.instance)

        self.assertEqual(code, 0)

    def test_nothing_to_stop_is_reported(self):
        code, kill = self.stop()

        self.assertEqual(code, 1)

        kill.assert_not_called()

    def test_a_process_that_died_in_the_meantime_is_not_a_problem(self):
        self.claimed()

        with mock.patch(
            "petwatch.daemon.os.kill",
            side_effect=ProcessLookupError,
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                code = stop_instance(self.instance)

        self.assertEqual(code, 1)

    def test_the_wait_gives_up_instead_of_hanging(self):
        """Um lock que ninguém solta não pode travar o terminal."""

        self.claimed()

        with (
            mock.patch("petwatch.daemon.os.kill"),
            mock.patch("petwatch.daemon.STOP_TIMEOUT", 0.2),
            mock.patch("petwatch.daemon.STOP_POLL", 0.01),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                code = stop_instance(self.instance)

        self.assertEqual(code, 1)


class LogTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-log-"))

        self.path = self.directory / "pet.log"

    def test_a_small_log_is_kept(self):
        self.path.write_text("uma linha só\n", encoding="utf-8")

        rotate_log(self.path)

        self.assertEqual(self.path.read_text(encoding="utf-8"), "uma linha só\n")

    def test_a_big_log_is_moved_aside(self):
        self.path.write_text("x" * 100, encoding="utf-8")

        rotate_log(self.path, max_bytes=10)

        self.assertFalse(self.path.exists())
        self.assertEqual(
            (self.directory / "pet.log.1").read_text(encoding="utf-8"),
            "x" * 100,
        )

    def test_one_generation_is_enough(self):
        self.path.write_text("antigo", encoding="utf-8")

        (self.directory / "pet.log.1").write_text("bem antigo", encoding="utf-8")

        rotate_log(self.path, max_bytes=1)

        self.assertEqual(
            (self.directory / "pet.log.1").read_text(encoding="utf-8"),
            "antigo",
        )

    def test_a_log_that_does_not_exist_is_not_a_problem(self):
        rotate_log(self.path)

        self.assertFalse(self.path.exists())


class ReportStartupTests(unittest.TestCase):
    """O protocolo do pipe entre o processo solto e o original.

    ``report_startup`` fecha o descritor que recebe — é o EOF que libera a
    espera do outro lado — então nenhum destes testes fecha o descritor de
    escrita por conta própria.
    """

    def read(self, read_fd: int) -> str:
        with os.fdopen(read_fd, "rb") as stream:
            return stream.read().decode("utf-8")

    def test_nothing_happens_without_a_pipe(self):
        report_startup(None)
        report_startup(None, "erro qualquer")

    def test_the_ready_line_carries_the_pid(self):
        read_fd, write_fd = os.pipe()

        report_startup(write_fd)

        self.assertEqual(self.read(read_fd), f"ok {os.getpid()}\n")

    def test_the_error_line_is_a_single_line(self):
        """Uma quebra no traceback viraria duas mensagens."""

        read_fd, write_fd = os.pipe()

        report_startup(write_fd, "RuntimeError: qt.qpa.plugin\nnão achou xcb")

        message = self.read(read_fd)

        self.assertEqual(message.count("\n"), 1)

        self.assertTrue(message.startswith("erro RuntimeError:"))
        self.assertIn("xcb", message)

    def test_reporting_twice_does_not_raise(self):
        """O caminho feliz e o do erro podem passar pelos dois."""

        read_fd, write_fd = os.pipe()

        report_startup(write_fd)

        os.close(read_fd)

        # Descritor já fechado: escrever nele dá EBADF, que precisa ser
        # engolido — senão o aviso de erro do app viraria o erro do aviso.
        report_startup(write_fd)
        report_startup(write_fd, "segunda vez")


#: Roteiro do processo separado: solta o ``work`` do terminal e deixa um
#: rastro no log, para o pai ter o que conferir.
SCRIPT = textwrap.dedent(
    """
    import os
    import sys
    import time
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])

    from petwatch.daemon import report_startup, spawn

    def work(ready):
        report_startup(ready)

        print(f"pet vivo pid {os.getpid()}", flush=True)

        time.sleep(30)

        return 0

    raise SystemExit(spawn(Path(sys.argv[2]), work))
    """
)


def read_pid(text: str) -> int:
    """Pid do rastro ``pet vivo pid N`` no log."""

    for line in text.splitlines():
        if line.startswith("pet vivo pid "):
            return int(line.rsplit(" ", 1)[1])

    raise AssertionError(f"log sem rastro do processo:\n{text}")


class DetachTests(unittest.TestCase):
    """O desvio de verdade, num processo separado da suíte.

    É aqui que se confere a promessa inteira: o comando volta com código 0,
    o terminal não recebe a saída do processo, e o processo continua vivo
    depois de o pai ter saído.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-detach-"))

        self.log = self.directory / "pet.log"

        self.pid: int | None = None

    def tearDown(self):
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGKILL)

            except ProcessLookupError:
                pass

    def wait_for_log(self, timeout: float = 15.0) -> str:
        """Espera o rastro aparecer e devolve o log inteiro."""

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.log.exists() and "pet vivo pid" in self.log.read_text(
                encoding="utf-8",
                errors="replace",
            ):
                return self.log.read_text(encoding="utf-8", errors="replace")

            time.sleep(0.05)

        content = (
            self.log.read_text(encoding="utf-8", errors="replace")
            if self.log.exists()
            else "(sem log)"
        )

        self.fail(f"o processo solto não escreveu no log em {timeout}s:\n{content}")

    def run_script(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                SCRIPT,
                str(BASE_DIR),
                str(self.log),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_the_command_returns_immediately_and_frees_the_terminal(self):
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertIn("rodando em segundo plano", result.stdout)

        # O rastro do processo foi para o arquivo, não para a tela: é o que
        # faz o terminal voltar a ser do shell.
        self.assertNotIn("pet vivo pid", result.stdout)

        self.pid = read_pid(self.wait_for_log())

    def test_the_process_outlives_the_shell_that_started_it(self):
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)

        self.pid = read_pid(self.wait_for_log())

        # O pai do processo solto já não é o comando, que saiu assim que a
        # janela confirmou que abriu. Sem os dois forks, o `ppid` seria o do
        # shell — e o `Ctrl+C` do shell mataria o pet junto.
        stat = Path(f"/proc/{self.pid}/stat").read_text(encoding="utf-8")

        ppid = int(stat.rsplit(")", 1)[1].split()[1])

        self.assertNotEqual(ppid, os.getpid())

    def test_the_stderr_of_the_detached_process_goes_to_the_log(self):
        """Including o que nem é Python — os avisos do Qt são de C."""

        self.log.parent.mkdir(parents=True, exist_ok=True)

        self.log.write_text("", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import os
                    import sys
                    from pathlib import Path

                    sys.path.insert(0, sys.argv[1])

                    from petwatch.daemon import report_startup, spawn

                    def work(ready):
                        report_startup(ready)

                        os.write(2, b"aviso do plugin\\n")

                        print("saida", flush=True)

                        return 0

                    raise SystemExit(spawn(Path(sys.argv[2]), work))
                    """
                ),
                str(BASE_DIR),
                str(self.log),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        content = self.log.read_text(encoding="utf-8")

        self.assertIn("aviso do plugin", content)
        self.assertIn("saida", content)

        self.assertNotIn("saida", result.stdout)
        self.assertNotIn("aviso do plugin", result.stderr)

    def test_a_failure_inside_the_work_reaches_the_terminal(self):
        """Sem isto o sintoma de "não abriu nada" seria o silêncio.

        O roteiro reproduz o que :func:`petwatch.app.run_app` faz: avisar o
        processo original antes de deixar a exceção subir, porque o
        traceback sozinho iria para um arquivo que ninguém sabe que
        existe.
        """

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import sys
                    from pathlib import Path

                    sys.path.insert(0, sys.argv[1])

                    from petwatch.daemon import report_startup, spawn

                    def work(ready):
                        try:
                            raise RuntimeError("qt.qpa.plugin: nao achou libxcb")

                        except BaseException as exc:
                            report_startup(ready, f"{type(exc).__name__}: {exc}")

                            raise

                    raise SystemExit(spawn(Path(sys.argv[2]), work))
                    """
                ),
                str(BASE_DIR),
                str(self.log),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 1)

        self.assertIn("não consegui abrir a janela", result.stderr)
        self.assertIn("libxcb", result.stderr)

    def test_the_instance_survives_the_fork(self):
        """O lock continua valendo com o processo que foi devolvido já fora.

        Sem isto, o segundo ``python pet.py`` ganharia um lock só para si e
        subiria um segundo pet na tela.
        """

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import sys
                    from pathlib import Path

                    sys.path.insert(0, sys.argv[1])

                    from petwatch.daemon import SingleInstance, spawn

                    pid_path = Path(sys.argv[3])

                    # Como `petwatch.app.main`: o lock é tomado antes do
                    # desvio, e atravessa o fork por herança.
                    assert SingleInstance(pid_path).claim()

                    def work(ready):
                        from petwatch.daemon import report_startup

                        report_startup(ready)

                        rival = SingleInstance(pid_path)

                        print(
                            "rival conseguiu" if rival.claim() else "rival barrado",
                            flush=True,
                        )

                        return 0

                    raise SystemExit(spawn(Path(sys.argv[2]), work))
                    """
                ),
                str(BASE_DIR),
                str(self.log),
                str(self.directory / "pet.pid"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        content = self.log.read_text(encoding="utf-8")

        self.assertIn("rival barrado", content)


if __name__ == "__main__":
    unittest.main()
