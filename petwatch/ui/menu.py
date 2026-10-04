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


@dataclass(frozen=True, slots=True)
class MenuHandlers:
    """O que cada item do menu faz."""

    on_size: Callable[[str], None]
    on_open_picker: Callable[[], None]
    on_toggle_on_top: Callable[[bool], None]
    on_quit: Callable[[], None]

    #: Estado atual, para marcar os itens já ativos.
    current_size: str = "medium"
    always_on_top: bool = True

    #: ``False`` quando o backend da tela não sabe manter a janela no
    #: topo. O item continua visível, só desativado e com o motivo na
    #: dica: escondê-lo faria o usuário concluir que o pet nunca teve
    #: essa opção.
    on_top_supported: bool = True


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