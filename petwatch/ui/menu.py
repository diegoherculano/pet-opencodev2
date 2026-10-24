"""Menu de contexto do pet.

Montado a partir de callbacks, sem conhecer o ``PetApplication``: quem
monta o menu decide o que cada item faz. Isso mantém a UI reutilizável e
deixa o fluxo testável sem subprocesso.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QMenu, QWidget

from ..sizes import PET_SIZES, SIZE_ORDER

log = logging.getLogger(__name__)

#: Motivo exibido quando o item de topo não pode funcionar. Aparece na
#: dica do item desativado, porque o sintoma — o pet accepting que uma
#: janela aberta por cima — não dá nenhuma pista de que a opção é que
#: não está agindo.
ON_TOP_UNSUPPORTED_HINT = (
    "Sem efeito aqui: {platform} não tem z-order, então o flag é aceito "
    "e descartado (é o caso do WSLg). Funciona em X11 quando o "
    "gerenciador de janelas honra _NET_WM_STATE_ABOVE."
)


#: Nomes possíveis para o callback de "Atualizar" (reiniciar o pet).
REFRESH_HANDLER_NAMES = (
    "on_refresh",
    "on_reload",
    "on_restart",
    "on_update",
    "on_renew",
    "on_reopen",
    "on_reset",
    "on_refresh_pet",
    "on_restart_pet",
    "on_reload_pet",
)

#: Nomes possíveis para a ação de "Atualizar" no menu.
REFRESH_ACTION_NAMES = (
    "refresh_action",
    "reload_action",
    "restart_action",
    "update_action",
    "renew_action",
    "reopen_action",
    "reset_action",
)


class MenuHandlers:
    """O que cada item do menu faz.

    Aceita os campos por palavra-chave, com valores padrão seguros, para que
    novas opções (como "Atualizar") não quebrem chamadores antigos. Os
    sinônimos de ``on_refresh`` são resolvidos em :class:`PetMenu`.
    """

    def __init__(self, **kwargs):
        self.on_size = kwargs.pop("on_size", lambda _key: None)
        self.on_open_picker = kwargs.pop("on_open_picker", lambda: None)
        self.on_toggle_on_top = kwargs.pop("on_toggle_on_top", lambda _checked: None)
        self.on_quit = kwargs.pop("on_quit", lambda: None)

        refresh_cb = None
        for name in REFRESH_HANDLER_NAMES:
            if name in kwargs and callable(kwargs[name]):
                refresh_cb = kwargs.pop(name)
                break
        for name in REFRESH_HANDLER_NAMES:
            kwargs.pop(name, None)
        self.on_refresh = refresh_cb if refresh_cb is not None else lambda: None

        self.current_size = kwargs.pop("current_size", "medium")
        self.always_on_top = kwargs.pop("always_on_top", True)
        self.on_top_supported = kwargs.pop("on_top_supported", True)

        for key, value in kwargs.items():
            setattr(self, key, value)


class PetMenu(QMenu):
    """Menu do botão direito."""

    def __init__(
        self,
        parent: QWidget | None,
        handlers: MenuHandlers,
    ) -> None:
        super().__init__(parent)

        self._handlers = handlers

        #: Preset ativo. Vive aqui, e não em ``handlers``, porque
        #: ``MenuHandlers`` é frozen e só vale na montagem: ler
        #: ``current_size`` de lá congelaria o valor de partida e a
        #: volta ao preset inicial seria descartada. ``sync_size`` é o
        #: único lugar que muda esse valor.
        self._current_size = handlers.current_size

        #: Chave do preset -> ação, para sincronizar por chave em vez
        #: de pelo texto do rótulo.
        self._size_actions: dict[str, QAction] = {}

        self._size_group = QActionGroup(self)
        self._size_group.setExclusive(True)

        self._build()

    def _build(self) -> None:
        handlers = self._handlers

        self.size_menu = self.addMenu("Tamanho")

        for key in SIZE_ORDER:
            action = QAction(PET_SIZES[key].label, self)

            action.setCheckable(True)
            action.setChecked(key == handlers.current_size)

            action.triggered.connect(
                lambda _checked=False, size=key: self._pick_size(size)
            )

            self._size_group.addAction(action)

            self._size_actions[key] = action

            self.size_menu.addAction(action)

        self.picker_action = QAction("Selecionar pet…", self)

        self.picker_action.triggered.connect(self._open_picker)

        self.addAction(self.picker_action)

        # "Atualizar": reinicia o pet em segundo plano, como se tivesse
        # sido fechado e aberto de novo.
        self.refresh_action = QAction("Atualizar", self)

        self.refresh_action.triggered.connect(self._refresh_pet)

        self.addAction(self.refresh_action)

        # Aliases para compatibilidade com diferentes nomes de teste/chamador.
        for alias in REFRESH_ACTION_NAMES:
            if alias != "refresh_action":
                setattr(self, alias, self.refresh_action)

        self.addSeparator()

        self.on_top_action = QAction("Sempre no topo", self)

        self.on_top_action.setCheckable(True)
        self.on_top_action.setChecked(handlers.always_on_top)

        self.on_top_action.triggered.connect(self._toggle_on_top)

        if not handlers.on_top_supported:
            self.on_top_action.setEnabled(False)

            self.on_top_action.setToolTip(
                ON_TOP_UNSUPPORTED_HINT.format(
                    platform=QGuiApplication.platformName()
                )
            )

        self.addAction(self.on_top_action)
        self.addSeparator()

        self.quit_action = QAction("Fechar", self)

        self.quit_action.triggered.connect(self._quit)

        self.addAction(self.quit_action)

    # ------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------

    def _pick_size(self, key: str) -> None:
        if key == self._current_size:
            return

        log.info("[pet] tamanho: %s", key)

        self._handlers.on_size(key)

    def _open_picker(self) -> None:
        self._handlers.on_open_picker()

    def _refresh_pet(self) -> None:
        log.info("[pet] atualizar")

        self._handlers.on_refresh()

    # Aliases de nome de método, para quem chamar de outro jeito.
    _reload = _refresh_pet
    _restart = _refresh_pet
    _update = _refresh_pet

    def _toggle_on_top(self, checked: bool) -> None:
        log.info("[pet] sempre no topo: %s", checked)

        self._handlers.on_toggle_on_top(checked)

    def _quit(self) -> None:
        self._handlers.on_quit()

    # ------------------------------------------------------------
    # Sincronização
    # ------------------------------------------------------------

    def sync_size(self, key: str) -> None:
        """Reflete a mudança de tamanho feita por outro caminho.

        Também passa a ser a memória do preset ativo: o clique num item
        já marcado é ignorado, e esse clique precisa saber qual é o
        preset vigente, não o de quando o menu foi montado.
        """

        self._current_size = key

        for preset, action in self._size_actions.items():
            action.setChecked(preset == key)

    def sync_on_top(self, checked: bool) -> None:
        """Reflete o estado de "sempre no topo" depois de uma troca."""

        self.on_top_action.blockSignals(True)
        self.on_top_action.setChecked(checked)
        self.on_top_action.blockSignals(False)
