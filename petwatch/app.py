"""Montagem e ciclo de vida do aplicativo."""

from __future__ import annotations

import logging
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Slot
from PySide6.QtWidgets import QApplication

from .config import DEFAULT_THEME, PID_PATH, SCREEN_GAP_X, SCREEN_GAP_Y
from .daemon import (
    SingleInstance,
    parse_args,
    report_startup,
    spawn,
    status_line,
    stop_instance,
)
from .discovery import get_opencode_password
from .idle import IdleWatchdog
from .monitor import OpenCodeMonitor
from .prefs import load_prefs, save_prefs
from .sessions import SessionBoard, StatusPoller
from .sizes import get_size
from .states import STATE_CONNECTING, STATE_WAITING
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
QUIT_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class PetApplication(QApplication):
    """Aplica o tema, liga o monitor e cuida do encerramento."""

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

            self.monitor.reconnected.connect(
                self.on_reconnected,
                Qt.ConnectionType.QueuedConnection,
            )

            self.poller.start()

        self.monitor.state_changed.connect(
            self.on_stream_state,
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

        O estado visível de cada instância é o "aguardando" quando há
        pedido **dela** — é o único estado que o stream não produz, e é o
        único que pinta cor.
        """

        cards: list[tuple[str, str, bool]] = []

        for instance in self.board.visible():
            state = STATE_WAITING if instance.needs_action else instance.state

            cards.append((state, instance.label, instance.wants_attention))

        return cards

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

    @Slot(str, object)
    def on_session_event(self, state: str, session_id: object) -> None:
        """Um evento que virou estado, na sessão que o produziu."""

        self.board.note_event(session_id if isinstance(session_id, str) else None,
                              state)

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

    @Slot()
    def on_ask_seen(self) -> None:
        """O stream viu um pedido (ou a resposta a um).

        Acorda a consulta na hora, para o pulso não esperar o tique do
        poll. A trava por stream fica posta como rede de segurança: em
        um servidor sem as rotas do v2 é ela que ainda mostra
        "aguardando".
        """

        self.board.note_ask()

        self._sync()

        if self.poller is not None:
            self.poller.poke()

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
    instance: SingleInstance,
    ready: int | None = None,
) -> int:
    """Sobe o aplicativo e espera ele fechar.

    Este é o corpo do processo: tanto o que roda colado no terminal
    (``--foreground``) quanto o processo solto chegam aqui, e por isso o
    ``logging`` é configurado aqui — no modo solto os descritores já foram
    apontados para o log, então o mesmo ``basicConfig`` serve para os dois.
    """

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        # O pid gravado tem que ser o do processo que fica de pé, então
        # ``record_pid`` só pode vir depois do fork.
        instance.record_pid()

        app = PetApplication()

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
        # Solta o lock, para o próximo ``python pet.py`` conseguir rodar.
        instance.release()


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada.

    Sem opção nenhuma o processo vai para segundo plano e o terminal volta
    na hora; ``--foreground`` roda colado, que é o modo de depurar.
    """

    options = parse_args(argv)

    instance = SingleInstance(PID_PATH)

    if options.status:
        print(status_line(instance, options.log))

        return 0 if instance.running() else 1

    if options.stop:
        return stop_instance(instance)

    if not instance.claim():
        print(
            f"[pet] já existe um pet rodando (pid {instance.owner}). "
            "Para encerrá-lo: pet.py --stop"
        )

        return 0

    if options.foreground:
        return run_app(options, instance)

    # O lock fica com o processo solto: ele herdou o descritor, e o pai sai
    # sem soltá-lo. Quem garante a instância única é o lock, não o prompt.
    return spawn(options.log, lambda ready: run_app(options, instance, ready))