"""Montagem e ciclo de vida do aplicativo."""

from __future__ import annotations

import logging
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from .config import (
    DEFAULT_THEME,
    IS_WINDOWS,
    PID_PATH,
    SCREEN_GAP_X,
    SCREEN_GAP_Y,
)
from .console import configure_logging, say
from .daemon import (
    _program_name,
    parse_args,
    ready_file,
    report_startup,
    spawn,
    spawn_detached,
    status_line,
    stop_instance,
)
from .discovery import get_opencode_password
from .idle import IdleWatchdog
from .instance import open_instance, serve_quit
from .monitor import OpenCodeMonitor
from .prefs import load_prefs, save_prefs
from .sessions import SessionBoard, StatusPoller
from .sizes import get_size
from .states import STATE_CONNECTING
from .theme import ThemeNotFoundError, load_theme
from .ui import PetRenderer, always_on_top_supported
from .ui.menu import MenuHandlers, PetMenu
from .ui.pet_picker import PetPicker
from .ui.tray import create_tray

log = logging.getLogger(__name__)

#: Tempo máximo de espera da thread do monitor no encerramento.
SHUTDOWN_GRACE_MS = 5000
SHUTDOWN_FORCE_MS = 1000

#: Sinais tratados para encerrar em vez de levantar traceback.
#:
#: No POSIX são ``SIGINT`` (``Ctrl+C``) e ``SIGTERM``, que é o que o
#: ``--stop`` manda. No Windows o ``SIGTERM`` **não entra**: lá ele é
#: ``TerminateProcess``, e um handler para ele seria uma promessa que o
#: sistema não cumpre — o processo morre sem rodar uma linha do handler.
#: O que substitui o ``--stop`` é o named pipe (ver
#: :mod:`petwatch.instance`); o que substitui o ``Ctrl+C`` é o
#: ``SIGBREAK``, que existe só nesta plataforma.
QUIT_SIGNALS = (
    (signal.SIGINT, getattr(signal, "SIGBREAK", signal.SIGINT))
    if IS_WINDOWS
    else (signal.SIGINT, signal.SIGTERM)
)


class PetApplication(QApplication):
    """Aplica o tema, liga o monitor e cuida do encerramento."""

    #: Alguém pediu encerramento de fora — hoje, só o named pipe do Windows.
    #: É um sinal, e não uma chamada a :meth:`quit`, porque o pedido chega
    #: numa thread que não é a da interface e o ``quit()`` toca no event
    #: loop. É o mesmo encanamento do monitor e do poll.
    stop_requested = Signal()

    def __init__(
        self,
        theme_name: str | None = None,
        prefs_path: Path | None = None,
        *,
        pending: bool = True,
    ) -> None:
        super().__init__([])

        # Só o tray aparece na área de notificação; fechar/esconder a
        # janela do pet não pode encerrar o processo.
        self.setQuitOnLastWindowClosed(False)

        # --------------------------------------------------------
        # Preferências
        # --------------------------------------------------------

        #: Onde as preferências são lidas e gravadas. ``None`` é o
        #: arquivo do usuário (``~/.config/petwatch/prefs.json``). A
        #: suíte aponta para um temporário: sem isso ela leria o tema
        #: escolhido na máquina de quem roda os testes — e gravaria por
        #: cima ao chamar :meth:`save`.
        self.prefs_path = prefs_path

        self.prefs = load_prefs(prefs_path)

        self.theme_name = theme_name or self.prefs["theme"] or DEFAULT_THEME

        self.size = get_size(self.prefs["size"])

        self.always_on_top = self.prefs["always_on_top"]

        #: Se o backend da tela sabe manter o pet na frente das janelas.
        #: No Wayland (WSLg) não sabe, e o aviso abaixo evita que o
        #: usuário culpe o flag por algo que ele nunca pediu a ninguém.
        self.on_top_supported = always_on_top_supported()

        # --------------------------------------------------------
        # Tema
        # --------------------------------------------------------

        self.theme = self._load_theme_or_default(self.theme_name)

        # O nome salvo e o tema carregado podem divergir: se a pasta
        # sumiu, o tema real é o padrão.
        self.theme_name = self.theme.directory.name

        # --------------------------------------------------------
        # Pet
        # --------------------------------------------------------

        self.pet = PetRenderer(self.theme, self.size)

        self.pet.set_always_on_top(self.always_on_top)

        if self.always_on_top and not self.on_top_supported:
            self.warn_on_top_is_inert()

        # --------------------------------------------------------
        # Menu
        # --------------------------------------------------------

        self.picker: PetPicker | None = None

        self.menu = PetMenu(
            self.pet,
            MenuHandlers(
                on_size=self.change_size,
                on_open_picker=self.open_picker,
                on_toggle_on_top=self.set_always_on_top,
                on_quit=self.quit,
                current_size=self.size.key,
                always_on_top=self.always_on_top,
                on_top_supported=self.on_top_supported,
            ),
        )

        self.pet.menu = self.menu

        # --------------------------------------------------------
        # Tray (sem barra de tarefas)
        # --------------------------------------------------------
        # A janela do pet é ``Qt.Tool`` (ver pet_widget), então o WM
        # pula a barra de tarefas / Alt-Tab. O acesso fica pelo tray,
        # que reaproveita o mesmo QMenu do botão direito.
        self.tray = create_tray(self.pet, self.menu)

        # --------------------------------------------------------
        # Monitor
        # --------------------------------------------------------

        self.monitor = OpenCodeMonitor()

        self.thread = QThread()

        self.monitor.moveToThread(self.thread)

        self.thread.started.connect(self.monitor.run)

        # --------------------------------------------------------
        # Quadro: um balão por instância em ação
        # --------------------------------------------------------

        #: Quem decide o que aparece: as instâncias em ação, o estado de
        #: cada uma e o estado visível do pet. Ver
        #: :mod:`petwatch.sessions` — o porquê de não decidir "aguardando"
        #: pelo stream está em :mod:`petwatch.pending`.
        self.board = SessionBoard()

        #: Consulta de status. Só existe para o pet não *inventar* um
        #: "aguardando"; a suíte desliga (``pending=False``) para não
        #: tocar na rede.
        self.poller: StatusPoller | None = None

        #: Último quadro publicado, para não repintar à toa.
        self._shown_state: str | None = None

        self._shown_cards: list[tuple[str, str, bool]] = []

        if pending:
            self.poller = StatusPoller(
                lambda: self.monitor.port,
                get_opencode_password,
                parent=self,
            )

            # ``QueuedConnection`` e ``@Slot`` pelos mesmos motivos do
            # monitor: quem fala está em outra thread, e os slots de
            # estado mexem no ``QTimer`` do pulso — que só pode ser
            # tocado na thread que o criou.
            self.poller.answers.connect(
                self.on_answers,
                Qt.ConnectionType.QueuedConnection,
            )

            self.poller.active.connect(
                self.on_active,
                Qt.ConnectionType.QueuedConnection,
            )

            self.poller.named.connect(
                self.on_named,
                Qt.ConnectionType.QueuedConnection,
            )

            # O evento de pedido só acorda a consulta: é ela que decide.
            self.monitor.ask_seen.connect(
                self.on_ask_seen,
                Qt.ConnectionType.QueuedConnection,
            )

            self.monitor.released.connect(
                self.on_released,
                Qt.ConnectionType.QueuedConnection,
            )

            self.monitor.reconnected.connect(
                self.on_reconnected,
                Qt.ConnectionType.QueuedConnection,
            )

            self.poller.start()

        self.monitor.state_changed.connect(
            self.on_stream_state,
            Qt.ConnectionType.QueuedConnection,
        )

        # A prova de vida do stream entra sempre, e não só com a consulta de
        # status ligada: é ela que impede `demote_stale` de trocar trabalho
        # real por "Ready" (bug 22), e `demote_stale` também roda pelo
        # watchdog, que existe nos dois casos.
        self.monitor.session_alive.connect(
            self.on_session_alive,
            Qt.ConnectionType.QueuedConnection,
        )

        if pending:
            self.monitor.session_event.connect(
                self.on_session_event,
                Qt.ConnectionType.QueuedConnection,
            )

        # --------------------------------------------------------
        # Watchdog
        # --------------------------------------------------------

        # Rede de segurança: se o opencode parar de mandar eventos, o
        # pet volta para "pronto" em vez de ficar preso em "Thinking".
        self.watchdog = IdleWatchdog()

        self.monitor.activity.connect(self.watchdog.note_activity)

        # O watchdog acompanha o estado **visível**, não o do stream: com
        # uma espera de verdade ele não age (esperar o usuário pode
        # levar minutos), e o que ele devolve passa pela arbitragem.
        self.watchdog.idle_reached.connect(
            self.on_watchdog_idle,
            Qt.ConnectionType.QueuedConnection,
        )

        # A bandeja **não** se liga ao `state_changed`: ela acompanha o
        # estado publicado por :meth:`_publish`. Ligada ao stream, ela
        # mostraria "trabalhando" no ícone enquanto o balão diz
        # "aguardando" — dois estados diferentes para a mesma coisa.
        self._publish(STATE_CONNECTING)

        # --------------------------------------------------------
        # Thread
        # --------------------------------------------------------

        self.thread.start()

        # --------------------------------------------------------
        # Tela
        # --------------------------------------------------------

        self.place_on_screen()

        self.pet.show()

        # --------------------------------------------------------
        # Encerramento
        # --------------------------------------------------------

        self.aboutToQuit.connect(self.shutdown)

        # ``QueuedConnection`` porque o emissor é o thread do pipe: o
        # ``quit()`` precisa rodar na thread do event loop, e é o que faz
        # o ``shutdown()`` (que para o monitor) rodar dentro dele também.
        self.stop_requested.connect(
            self.quit,
            Qt.ConnectionType.QueuedConnection,
        )

        install_quit_signals(self)

    @staticmethod
    def _load_theme_or_default(name: str):
        """Tema pedido, ou o padrão se o nome salvo não existir mais.

        O usuário pode apagar uma pasta de ``pets/`` depois de tê-la
        escolhido; nesse caso o app ainda precisa abrir.
        """

        try:
            return load_theme(name)

        except (ThemeNotFoundError, OSError) as exc:
            log.warning("[pet] tema %s indisponível (%s); usando %s",
                        name, exc, DEFAULT_THEME)

            return load_theme(DEFAULT_THEME)

    # ------------------------------------------------------------
    # Estados: quem entra, o que sai
    # ------------------------------------------------------------

    def _cards(self) -> list[tuple[str, str, bool]]:
        """Balões a desenhar: ``(estado, nome, espera_resposta)``.

        Quem decide é o quadro — ver :meth:`SessionBoard.cards`. A
        interface não repete a regra aqui: quando repetia, o sprite e o
        balão discordavam, e as duas metades do "aguardando" (a do servidor
        e a da degradação) viviam em lugares diferentes.
        """

        return self.board.cards()

    def _sync(self) -> None:
        """Publica o quadro, se mudou."""

        cards = self._cards()

        if self.board.state == self._shown_state and cards == self._shown_cards:
            return

        self._shown_state = self.board.state
        self._shown_cards = cards

        self._publish(self.board.state)

        self.pet.set_cards(cards)

    def _publish(self, state: str) -> None:
        """Aplica ``state`` no pet, na bandeja e no watchdog."""

        self.pet.set_state(state)

        if self.tray is not None:
            self.tray.on_state(state)

        # O watchdog precisa do estado **visível**: é ele que impede o
        # timeout de cortar um "aguardando" legítimo.
        self.watchdog.note_state(state)

    @Slot(str, object, object)
    def on_session_event(self, state: str, session_id: object,
                         directory: object = None) -> None:
        """Um evento que virou estado, na sessão que o produziu."""

        self.board.note_event(session_id if isinstance(session_id, str) else None,
                              state,
                              directory if isinstance(directory, str) else None)

        self._sync()

    @Slot(object, object)
    def on_session_alive(self, session_id: object, directory: object) -> None:
        """Prova de vida do stream, sem estado.

        Não é o estado — é a prova de que a instância **está viva**, que é
        o que impede `demote_stale` de chamá-la de parada no meio de um
        raciocínio (bug 22). Por isso entra em toda evento, inclusive nos
        que não viram estado.
        """

        self.board.note_alive(session_id if isinstance(session_id, str) else None,
                              directory if isinstance(directory, str) else None)

        self._sync()

    @Slot(str)
    def on_stream_state(self, state: str) -> None:
        """Estado que veio do stream, sem dono de balão."""

        self.board.note_global_state(state)

        self._sync()

    @Slot(str)
    def on_watchdog_idle(self, state: str) -> None:
        """O watchdog devolveu o pet para "pronto".

        Antes disso, os balões que calaram também: com o estado por
        sessão, o watchdog mudava o sprite para "Ready" e deixava o
        balão dizendo "Thinking" — duas verdades na tela. A mesma
        regra vale para os dois: silêncio por mais que ``IDLE_TIMEOUT``
        é turno encerrado.
        """

        self.board.demote_stale(self.watchdog.timeout)

        self.board.note_global_state(state)

        self._sync()

    @Slot(object)
    def on_answers(self, asks: object) -> None:
        """Resposta do servidor sobre o que está esperando o usuário.

        ``None`` é "não deu para saber" (a consulta falhou, ou o servidor
        é antigo e não tem a rota) e **não** muda o estado: trocar
        "aguardando" por "trabalhando" sem saber seria inventar resposta
        do usuário.
        """

        if asks is not None and not isinstance(asks, Sequence):
            return

        self.board.note_pending(asks)

        self._sync()

    @Slot(object)
    def on_active(self, sessions: object) -> None:
        """Sessões em primeiro plano, de ``GET /api/session/active``."""

        if sessions is None:
            # Não deu para saber: o quadro continua como está.
            ids = None

        elif isinstance(sessions, Sequence) and not isinstance(
            sessions, (str, bytes)
        ):
            ids = [s for s in sessions if isinstance(s, str)]

        else:
            return

        self.board.note_active(ids)

        # Envelhecer o balão aqui, e não só em `on_watchdog_idle`.
        #
        # O watchdog global só dispara depois de `IDLE_TIMEOUT` de silêncio
        # **do servidor inteiro**, e com outra aba trabalhando ele nunca
        # dispara — que é o uso normal, várias abas do opencode abertas.
        # Aí a sessão que travou ficava em "Thinking" para sempre, porque
        # a única chamada de `demote_stale` estava atrás desse watchdog.
        # Aqui não: a consulta de ativas é o que descobre que a instância
        # continua listada, então é também o lugar natural para conferir
        # que ela de fato calou. Ver o bug 21.
        if ids is not None:
            self.board.demote_stale(self.watchdog.timeout)

        self._sync()

    @Slot(object)
    def on_named(self, payload: object) -> None:
        """``(sessionID, info)``: uma instância ganhou nome."""

        if not isinstance(payload, tuple) or len(payload) != 2:
            return

        session_id, info = payload

        if not isinstance(session_id, str) or not isinstance(info, Mapping):
            return

        self.board.note_session_info(session_id, info)

        self._sync()

    @Slot(object)
    def on_ask_seen(self, session_id: object = None) -> None:
        """O stream viu um pedido.

        Acorda a consulta na hora, para o pulso não esperar o tique do
        poll. A trava por stream fica posta como rede de segurança: em
        um servidor sem as rotas do v2 é ela que ainda mostra
        "aguardando" — e ela guarda **de quem** foi o pedido, senão o
        ``session.idle`` de outra aba a derruba (bug 20).
        """

        self.board.note_ask(session_id if isinstance(session_id, str) else None)

        self._sync()

        if self.poller is not None:
            self.poller.poke()

    @Slot(object)
    def on_released(self, session_id: object = None) -> None:
        """O stream viu a resposta a um pedido.

        Solta a trava na hora, em vez de esperar o tique do poll. Com o
        servidor respondendo, a espera real é a consulta que decide — isto
        só antecipa o que ela ia dizer.
        """

        self.board.note_release(session_id if isinstance(session_id, str) else None)

        self._sync()

    @Slot()
    def on_reconnected(self) -> None:
        """O stream reconectou: o que houve na quebra é desconhecido."""

        if self.poller is not None:
            self.poller.poke()

    # ------------------------------------------------------------
    # Preferências
    # ------------------------------------------------------------

    def save(self) -> None:
        save_prefs({
            "theme": self.theme_name,
            "size": self.size.key,
            "always_on_top": self.always_on_top,
        }, self.prefs_path)

    # ------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------

    def change_size(self, key: str) -> None:
        self.size = get_size(key)

        self.pet.set_size(self.size)
        self.menu.sync_size(self.size.key)
        self.save()

    def set_always_on_top(self, enabled: bool) -> None:
        self.always_on_top = bool(enabled)

        self.pet.set_always_on_top(self.always_on_top)
        self.menu.sync_on_top(self.always_on_top)
        self.save()

    def warn_on_top_is_inert(self) -> None:
        """Deixa claro que o item de topo não vai mudar nada aqui.

        Sem isso o sintoma é silencioso: o flag está ligado, o menu
        mostra marcado, e o pet continua passando atrás das outras
        janelas sem nenhuma pista de onde está o problema.
        """

        log.warning(
            "[pet] 'sempre no topo' não tem efeito em %s: esse backend "
            "não tem z-order, então o flag é aceito e descartado (é o "
            "caso do WSLg). Em X11 ele funciona, desde que o "
            "gerenciador de janelas honre _NET_WM_STATE_ABOVE.",
            self.platformName(),
        )

    def open_picker(self) -> None:
        """Abre o seletor de pet, um só de cada vez.

        Reabrir o diálogo de dentro de si mesmo deixaria dois menus de
        seleção disputando o mesmo estado, então o anterior é fechado.
        """

        if self.picker is not None:
            self.picker.close()
            self.picker.deleteLater()
            self.picker = None

        picker = PetPicker(self.pet, current=self.theme_name)

        picker.theme_chosen.connect(self.change_theme)

        # Qt apaga o wrapper quando a janela fecha; soltar a referência
        # aqui evita recriar o mesmo diálogo sem parar o anterior.
        picker.finished.connect(lambda _code: self._forget_picker(picker))

        self.picker = picker

        picker.show()

    def _forget_picker(self, picker: PetPicker) -> None:
        if self.picker is picker:
            self.picker = None

    def change_theme(self, name: str) -> None:
        """Troca o pet em tempo real."""

        if name == self.theme_name:
            return

        try:
            theme = load_theme(name)

        except (ThemeNotFoundError, OSError) as exc:
            log.warning("[pet] não consegui abrir o tema %s: %s", name, exc)
            return

        self.theme_name = name
        self.theme = theme

        self.pet.apply_theme(theme)

        if self.tray is not None:
            self.tray.refresh_icon()

        self.save()

    # ------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------

    def place_on_screen(self) -> None:
        """Ancora o pet no canto inferior direito da tela principal."""

        screen = self.primaryScreen()

        if screen is None:
            return

        geometry = screen.availableGeometry()

        self.pet.move(
            geometry.right() - self.pet.width() - SCREEN_GAP_X,
            geometry.bottom() - self.pet.height() - SCREEN_GAP_Y,
        )

    def shutdown(self) -> None:
        """Para o monitor e espera a thread terminar.

        ``stop()`` desliga o socket do stream, então a leitura bloqueada
        volta na hora e ``wait()`` completa dentro do primeiro prazo.
        """

        self.save()

        self.watchdog.timer.stop()

        if self.poller is not None:
            self.poller.stop()

        if getattr(self, "tray", None) is not None:
            self.tray.hide()

        if self.picker is not None:
            self.picker.close()

        self.monitor.stop()

        if not self.thread.isRunning():
            return

        self.thread.quit()

        if self.thread.wait(SHUTDOWN_GRACE_MS):
            return

        log.warning("[pet] thread do monitor não parou a tempo; forçando")

        self.thread.requestInterruption()

        if not self.thread.wait(SHUTDOWN_FORCE_MS):
            log.error("[pet] thread do monitor segue ativa")

    def run(self) -> int:
        return self.exec()

    # ------------------------------------------------------------
    # Robustez
    # ------------------------------------------------------------

    def notify(self, receiver, event) -> bool:
        """Erro em um handler de evento não pode derrubar o programa.

        Cobre o código chamado pelo Qt como *evento* — ``paintEvent``,
        ``mousePressEvent``, ``resizeEvent`` — que é onde roda quase tudo
        aqui. Sem esta volta, o PySide6 imprime o traceback e segue com o
        event loop; com ela, o erro vai para o log com o evento e o
        receptor no contexto.

        Exceções em *slots de sinal* (o ``timeout`` do timer, por
        exemplo) não passam por aqui: o PySide6 as imprime e continua, o
        que também preserva a janela.
        """

        try:
            return super().notify(receiver, event)

        except Exception:
            log.exception(
                "[pet] erro tratando %s em %s",
                type(event).__name__,
                type(receiver).__name__,
            )

            return False


def install_quit_signals(app: QApplication) -> None:
    """Faz ``Ctrl+C`` e ``kill`` encerrarem o aplicativo.

    Sem isso o ``SIGINT`` levanta ``KeyboardInterrupt`` dentro do slot que
    o Qt está executando; o PySide6 imprime o traceback e o event loop
    continua, então o processo não sai. O handler chama ``quit()``, que
    dispara ``aboutToQuit`` e o mesmo caminho limpo do item de menu.

    O handler roda na thread principal entre um tique do timer e o
    seguinte, então a resposta leva no máximo ``ANIMATION_INTERVAL_MS``.
    """

    def request_quit(signum, frame) -> None:
        log.info("[pet] %s recebido; encerrando", signal.Signals(signum).name)

        app.quit()

    for number in QUIT_SIGNALS:
        try:
            signal.signal(number, request_quit)
        except (ValueError, OSError) as exc:
            # Só é possível instalar na thread principal.
            log.debug("[pet] sinal %s não registrado: %s", number, exc)


def run_app(
    options,
    instance,
    ready: int | Path | None = None,
) -> int:
    """Sobe o aplicativo e espera ele fechar.

    Este é o corpo do processo: tanto o que roda colado no terminal
    (``--foreground``) quanto o processo solto chegam aqui, e por isso o
    ``logging`` é configurado aqui — no modo solto os descritores já foram
    apontados para o log, então o mesmo destino serve para os dois; e sem
    console (``petwatch.exe``) o destino é o arquivo.
    """

    configure_logging()

    try:
        # O pid gravado tem que ser o do processo que fica de pé, então
        # ``record_pid`` só pode vir depois do desvio.
        instance.record_pid()

        app = PetApplication()

        # Quem pode pedir encerramento vem pelo mesmo caminho do menu.
        # No POSIX existe o sinal; no Windows, o pipe — e o pipe entrega
        # por sinal, porque o pedido sai de outra thread.
        serve_quit(instance, app.stop_requested.emit)

        # O processo original está esperando isto para devolver o terminal.
        report_startup(ready)

        return app.run()

    except BaseException as exc:
        # Sem esta confirmação o processo original ficaria esperando até o
        # prazo estourar sem saber por quê — e o traceback iria para um
        # arquivo que ninguém foi avisado de que existe.
        report_startup(ready, f"{type(exc).__name__}: {exc}")

        raise

    finally:
        # Solta a instância, para o próximo ``python pet.py`` conseguir rodar.
        instance.release()


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada.

    Sem opção nenhuma o processo vai para segundo plano e o terminal volta
    na hora; ``--foreground`` roda colado, que é o modo de depurar.

    A diferença entre as plataformas está em **quem** assume a instância
    única, e só isso. No POSIX o processo original toma o ``flock`` e o
    filho o herança pelo ``fork``; no Windows não há herança de handle
    confiável entre processos, então o original apenas pergunta se há pet
    rodando e quem assume é o filho, que já sobe com ``--child``. O
   filho responde "já tem" pelo mesmo ``claim()`` que falha.
    """

    options = parse_args(argv)

    instance = open_instance(PID_PATH)

    if options.status:
        line = status_line(instance, options.log)

        say(line, popup=True)

        return 0 if instance.running() else 1

    if options.stop:
        return stop_instance(instance, options.log)

    if IS_WINDOWS:
        return _windows(options, instance)

    return _posix(options, instance)


def _already_running(instance) -> int:
    """A frase de "já tem pet", e o código de quem só avisa."""

    say(
        f"[pet] já existe um pet rodando (pid {instance.owner}). "
        f"Para encerrá-lo: {_program_name()} --stop",
        popup=True,
    )

    return 0


def _posix(options, instance) -> int:
    """Fluxo original: o lock é tomado aqui e atravessa o ``fork``."""

    if not instance.claim():
        return _already_running(instance)

    if options.foreground:
        return run_app(options, instance)

    # O lock fica com o processo solto: ele herdou o descritor, e o pai sai
    # sem soltá-lo. Quem garante a instância única é o lock, não o prompt.
    return spawn(options.log, lambda ready: run_app(options, instance, ready))


def _windows(options, instance) -> int:
    """Fluxo do Windows: o original pergunta, o filho assume."""

    if options.child:
        # Este é o processo solto. Quem garante a instância única é o pipe,
        # e ele é assumido aqui — no pai não haveria handle confiável para
        # chegar até este processo.
        if not instance.claim():
            return _already_running(instance)

        return run_app(options, instance, ready_file())

    if instance.running():
        return _already_running(instance)

    if options.foreground:
        # Colado no terminal assume a instância aqui e segura até o fim.
        if not instance.claim():
            return _already_running(instance)

        return run_app(options, instance)

    return spawn_detached(options.log)