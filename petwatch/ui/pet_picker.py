"""Diálogo de escolha de pet.

Lista os temas de ``pets/`` pelo **nome da pasta**, com busca e paginação.
Nada é lido dos ``pet.json`` para montar a lista: o usuário decide
quantos pets existem e eles podem mudar, então a pasta é a fonte da
verdade. Ler 1738 JSONs para mostrar nomes seria acoplar a UI a um
formato que o usuário não precisa ter.

A troca é em tempo real: clicar já repinta o pet, sem botão de
confirmar. Por isso o diálogo é modeless.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import list_themes

log = logging.getLogger(__name__)

#: Petões por página.
PAGE_SIZE = 10

#: Uma página é uma fatia da lista filtrada.
Page = list


class _Silence:
    """Silencia um objeto durante uma atualização.

    Guardar e restaurar o estado dos sinais é mais barato que conectar e
    desconectar, e sobrevive a ``clear()`` recriando os itens.
    """

    def __init__(self, target) -> None:
        self._target = target
        self._state = None

    def __enter__(self):
        self._state = self._target.blockSignals(True)
        return self

    def __exit__(self, *_exc) -> None:
        if self._state is not None:
            self._target.blockSignals(self._state)
            self._state = None


class PetPicker(QDialog):
    """Escolhe o pet pelo nome da pasta."""

    #: Emitido com o nome da pasta escolhida.
    theme_chosen = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current: str | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("Escolher pet")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setModal(False)

        # O diálogo é só uma lista de nomes de pasta; a fonte é o disco.
        self._all: list[str] = list_themes()
        self._filtered: list[str] = list(self._all)
        self._page = 0

        #: Nome da pasta selecionada, sem efeito colateral.
        self.selected: str | None = None

        self._build()

        self._refresh()

        if current:
            self.preselect(current)

    # ------------------------------------------------------------
    # Montagem
    # ------------------------------------------------------------

    def _build(self) -> None:
        self.search = QLineEdit(self)

        self.search.setPlaceholderText("Buscar pet…")
        self.search.setClearButtonEnabled(True)

        self.search.textChanged.connect(self._on_search)

        self.list = QListWidget(self)

        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setUniformItemSizes(True)

        # Um clique simples troca na hora: é o comportamento esperado de
        # um seletor que se aplica em tempo real.
        self.list.itemClicked.connect(self._on_clicked)

        # Enter e duplo clique também escolhem.
        self.list.itemActivated.connect(self._on_activated)

        # currentItemChanged também dispara quando os itens são recriados
        # por uma busca ou paginação, então ele só serve para manter a
        # seleção em dia — nunca para trocar o pet.
        self.list.currentItemChanged.connect(self._on_selection_moved)

        self.status = QLabel(self)

        self.previous_button = QPushButton("Anterior", self)
        self.next_button = QPushButton("Próxima", self)

        self.previous_button.clicked.connect(lambda: self._go(-1))
        self.next_button.clicked.connect(lambda: self._go(1))

        self.close_button = QPushButton("Fechar", self)

        self.close_button.clicked.connect(self.close)

        pager = QHBoxLayout()

        pager.addWidget(self.previous_button)
        pager.addWidget(self.list_page_label(), stretch=1)
        pager.addWidget(self.next_button)

        buttons = QHBoxLayout()

        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)

        layout.addWidget(self.search)
        layout.addWidget(self.list, stretch=1)
        layout.addLayout(pager)
        layout.addLayout(buttons)

        self.setMinimumWidth(260)

    def list_page_label(self) -> QLabel:
        """Rótulo de página, guardado em ``self._page_label``."""

        self._page_label = QLabel(self)

        return self._page_label

    # ------------------------------------------------------------
    # Dados
    # ------------------------------------------------------------

    @property
    def page_count(self) -> int:
        if not self._filtered:
            return 1

        return (len(self._filtered) + PAGE_SIZE - 1) // PAGE_SIZE

    def page_slice(self, page: int) -> Page:
        start = page * PAGE_SIZE

        return Page(self._filtered[start:start + PAGE_SIZE])

    # ------------------------------------------------------------
    # Interação
    # ------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        query = text.strip().lower()

        if query:
            self._filtered = [
                name for name in self._all if query in name.lower()
            ]
        else:
            self._filtered = list(self._all)

        self._page = 0

        # Buscar recomeça na primeira página e não troca o pet.
        self._refresh()

    def _go(self, delta: int) -> None:
        page = self._page + delta

        if 0 <= page < self.page_count:
            self._page = page

            self._refresh()

    def _refresh(self) -> None:
        self.list.clear()

        items = self.page_slice(self._page)

        for name in items:
            self.list.addItem(QListWidgetItem(name))

        # Recomeça sem seleção: marcar a primeira linha faria o clique
        # nela parecer não valer nada, já que ela já seria a atual.
        self.selected = None

        total = len(self._filtered)

        # Rótulo do meio: cresce com o número de dígitos, então reserva
        # a largura antes de escrever, senão os botões pulam de lugar a
        # cada mudança de página.
        count = f"({total} pet{'s' if total != 1 else ''})"

        label = f"Página {self._page + 1} de {self.page_count}  {count}"

        metrics = self._page_label.fontMetrics()

        self._page_label.setMinimumWidth(metrics.horizontalAdvance(label) + 8)
        self._page_label.setText(label)

        self.previous_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < self.page_count - 1)

        # Sem resultado, avisa em vez de mostrar uma lista vazia sem
        # explicação.
        if not items:
            self.list.addItem(QListWidgetItem("(nenhum pet encontrado)"))
            self.list.setCurrentRow(-1)

    def _on_selection_moved(
        self,
        current: QListWidgetItem | None,
        _previous,
    ) -> None:
        """A seleção mudou; só marca que a linha é a escolhida.

        Buscar e paginar recriam os itens e disparariam isto sem que o
        usuário tivesse pedido troca alguma, então a troca fica por conta
        de ``itemClicked`` e ``itemActivated``.
        """

        if current is None:
            return

        name = current.text()

        if name and not name.startswith("("):
            self.selected = name

    def _on_clicked(self, item: QListWidgetItem) -> None:
        """Clique simples: troca o pet na hora."""

        if item is None:
            return

        self.choose(item.text())

    def choose(self, name: str) -> None:
        """Aplica a escolha de ``name``."""

        if not name or name.startswith("("):
            return

        log.info("[pet] pet escolhido: %s", name)

        self.theme_chosen.emit(name)

    # ------------------------------------------------------------
    # Digito do usuário
    # ------------------------------------------------------------

    def _on_activated(self, item: QListWidgetItem) -> None:
        """Enter ou duplo clique numa linha."""

        if item is None:
            return

        self.choose(item.text())

    # ------------------------------------------------------------
    # API
    # ------------------------------------------------------------

    def preselect(self, name: str) -> None:
        """Deixa ``name`` marcado, sem emitir a troca de novo."""

        if name not in self._all:
            return

        index = self._filtered.index(name)

        self._page = index // PAGE_SIZE

        self._refresh()

        row = index % PAGE_SIZE

        if row < self.list.count():
            # ``_on_selection_moved`` só marca; a troca continua por conta
            # de ``itemClicked`` / ``itemActivated``.
            self.list.setCurrentRow(row)

            self.selected = name