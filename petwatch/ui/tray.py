"""Ícone na bandeja (tray) — o pet não tem entrada na barra de tarefas.

A janela do pet já usa ``Qt.Tool``, que diz ao gerenciador de janelas
para pular a barra de tarefas / Alt-Tab. O que faltava era o outro
lado: um ``QSystemTrayIcon`` para o processo continuar acessível sem
ocupar a barra. Sem ele, esconder da barra seria perder o único jeito
de achar e fechar o pet além do botão direito no sprite.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

log = logging.getLogger(__name__)


def build_tray_icon(pet: QWidget, edge: int = 32) -> QIcon:
    """Monta o ícone do tray a partir do sprite atual.

    Usa o quadro corrente do pet para o ícone ser o próprio bicho.
    Se não houver sprite decodificado, volta um ícone vazio para o
    ``QSystemTrayIcon`` não ficar sem nada — sem levantar exceção.
    """

    image: QImage | None = None

    current = getattr(pet, "current_image", None)
    if callable(current):
        try:
            image = current()
        except Exception:
            image = None

    if image is not None and not image.isNull():
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                edge,
                edge,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            return QIcon(scaled)

    # Fallback: pixmap transparente do tamanho pedido.
    empty = QPixmap(edge, edge)
    empty.fill(Qt.transparent)
    return QIcon(empty)


class PetTray(QSystemTrayIcon):
    """Ícone do tray com o mesmo menu do botão direito.

    O ``QMenu`` é compartilhado com o pet: o clique direito no sprite
    e o clique direito no tray mostram os mesmos itens, então só há um
    lugar para sincronizar tamanho / topo. O clique esquerdo alterna a
    visibilidade do pet.
    """

    def __init__(self, pet: QWidget, menu: QMenu) -> None:
        super().__init__(build_tray_icon(pet), pet)

        self.pet = pet

        self.setToolTip("petwatch")
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_pet_visible()

    def toggle_pet_visible(self) -> None:
        """Mostra ou esconde o pet sem encerrar o processo."""

        if self.pet.isVisible():
            self.pet.hide()
        else:
            self.pet.show()

    def refresh_icon(self) -> None:
        """Recarrega o ícone depois de troca de tema/estado."""

        self.setIcon(build_tray_icon(self.pet))

    @Slot(str)
    def on_state(self, state: str) -> None:
        """Tooltip acompanha o estado; o ícone acompanha o sprite."""

        self.setToolTip(f"petwatch — {state}")
        self.refresh_icon()


def create_tray(pet: QWidget, menu: QMenu) -> PetTray | None:
    """Cria e mostra o tray, ou ``None`` sem bandeja disponível.

    Em ``offscreen`` (suíte) e em sessões sem bandeja não há onde
    mostrar — nesse caso o app segue só com a janela, sem quebrar.
    """

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("[pet] bandeja indisponível; seguindo sem ícone no tray")
        return None

    tray = PetTray(pet, menu)
    tray.show()

    if not tray.isVisible():
        log.warning("[pet] tray não ficou visível")

    return tray
