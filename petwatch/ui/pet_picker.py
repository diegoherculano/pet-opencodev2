"""Diálogo de escolha de pet.

Lista os temas de ``pets/`` pelo **nome da pasta**, com busca e paginação.
Nada é lido dos ``pet.json`` para montar a lista: o usuário decide
quantos pets existem e eles podem mudar, então a pasta é a fonte da
verdade. Ler 1738 JSONs para mostrar nomes seria acoplar a UI a um
formato que o usuário não precisa ter.

Cada item é uma **miniatura** do pet — o ``preview.gif`` do diretório, ou
qualquer outra imagem que o Qt consiga abrir — com o nome da pasta embaixo,
porque nome sozinho não diz nada sobre o bicho. O nome continua nos dados do
item (é o que a busca filtra e o que o clique emite); na tela ele volta duas
vezes: sob a figura e na barra de status, seguindo o cursor ou a seleção.
Nomes longos viram reticências sob a figura, então a dica do item e a barra
de status guardam o texto inteiro.

A troca é em tempo real: clicar já repinta o pet, sem botão de
confirmar. Por isso o diálogo é modeless.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..assets import candidate_paths, is_supported
from ..config import PETS_DIR
from ..theme import list_themes

log = logging.getLogger(__name__)

#: Petões por página.
PAGE_SIZE = 10

#: Lado da miniatura, em pixels.
THUMB_SIZE = 96

#: Respiro entre a borda da célula e o conteúdo.
CELL_PADDING = 8

#: Espaço entre a miniatura e o nome.
CAPTION_GAP = 4

#: Colunas da grade. Uma página inteira de :data:`PAGE_SIZE` cabe nelas
#: sem rolagem, que é o que define a altura inicial do diálogo.
GRID_COLUMNS = 5

#: Linhas que uma página ocupa em :data:`GRID_COLUMNS` colunas.
GRID_ROWS = -(-PAGE_SIZE // GRID_COLUMNS)

#: Folga da moldura em volta da grade: barra de busca, barra de status,
#: paginação, botões, bordas da lista e a barra de rolagem. Medido, não
#: calculado — serve para a janela inicial caber, não para ser exato.
CHROME_WIDTH = 84
CHROME_HEIGHT = 168


def cell_size(caption_height: int) -> QSize:
    """Célula da grade: a miniatura em cima, o nome embaixo.

    A altura do nome vem da fonte em uso, então a célula é calculada na
    montagem do diálogo, com as métricas reais.
    """

    return QSize(
        THUMB_SIZE + CELL_PADDING * 2,
        CELL_PADDING * 2 + THUMB_SIZE + CAPTION_GAP + caption_height,
    )


def page_width(width: int) -> int:
    """Largura para :data:`GRID_COLUMNS` células, sem rolagem."""

    return width * GRID_COLUMNS + CHROME_WIDTH


def page_height(height: int) -> int:
    """Altura para :data:`GRID_ROWS` células, sem rolagem."""

    return height * GRID_ROWS + CHROME_HEIGHT

#: Uma página é uma fatia da lista filtrada.
Page = list

#: Marca a linha de aviso ("nenhum pet encontrado"), que não é um pet.
PLACEHOLDER_ROLE = Qt.UserRole + 1


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


def thumbnail(name: str, size: int = THUMB_SIZE) -> QPixmap:
    """Miniatura do pet ``name``; pixmap vazio se o diretório não tiver imagem.

    A ordem é a mesma do pet em si (``preview.gif`` primeiro, depois
    qualquer outra imagem decodificável), então a miniatura é sempre o
    mesmo bicho que a troca vai pintar.

    Só o primeiro quadro de uma imagem animada entra: um GIF animado
    seria dez decodificações por miniatura, e aqui a figura parada é o
    que identifica o pet.
    """

    for path in candidate_paths(PETS_DIR / name, None):
        if not path.exists() or not is_supported(path):
            continue

        reader = QImageReader(str(path))

        if reader.canRead():
            image = reader.read()

            if not image.isNull():
                return QPixmap.fromImage(
                    image.scaled(
                        size,
                        size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )

    # Diretório sem imagem é um item sem figura, não um erro: inundar o
    # log a cada página não ajuda ninguém.
    log.debug("[pet] sem miniatura para %s", name)

    return QPixmap()


class ThumbnailDelegate(QStyledItemDelegate):
    """Pinta a miniatura com o nome embaixo.

    O nome da pasta é o texto do item — é dele que a busca e o clique
    tiram o que precisam — e aqui ele volta para a tela embaixo da
    figura, onde cabe inteiro na maioria dos casos e vira reticências
    quando não. A linha de aviso de busca vazia é o único item sem
    miniatura, e ela usa o desenho padrão: sua célula tem a largura da
    grade toda e o texto precisa ficar no meio dela.
    """

    def paint(self, painter, option, index) -> None:
        if index.data(PLACEHOLDER_ROLE):
            super().paint(painter, option, index)

            return

        painter.save()

        selected = bool(option.state & QStyle.State_Selected)

        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, option.palette.alternateBase())

        # O nome come a faixa de baixo da célula; o resto é da figura.
        caption_height = option.fontMetrics.height()

        area = option.rect.adjusted(
            CELL_PADDING,
            CELL_PADDING,
            -CELL_PADDING,
            -(CAPTION_GAP + caption_height + CELL_PADDING),
        )

        pixmap: QPixmap | None = index.data(Qt.DecorationRole)

        if pixmap is not None:
            scaled = pixmap.scaled(
                area.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            painter.drawPixmap(
                area.x() + (area.width() - scaled.width()) // 2,
                area.y() + (area.height() - scaled.height()) // 2,
                scaled,
            )

        palette = option.palette

        painter.setPen(
            palette.color(QPalette.ColorRole.HighlightedText)
            if selected
            else palette.color(QPalette.ColorRole.Text)
        )

        caption = QRect(
            option.rect.left() + CELL_PADDING,
            option.rect.bottom() - CELL_PADDING - caption_height,
            option.rect.width() - CELL_PADDING * 2,
            caption_height,
        )

        text = str(index.data(Qt.DisplayRole) or "")

        painter.drawText(
            caption,
            Qt.AlignCenter,
            option.fontMetrics.elidedText(text, Qt.ElideRight, caption.width()),
        )

        painter.restore()


class PetPicker(QDialog):
    """Escolhe o pet pela miniatura da pasta."""

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

        # O diálogo lista as pastas de pets/; a fonte é o disco.
        self._all: list[str] = list_themes()
        self._filtered: list[str] = list(self._all)
        self._page = 0

        #: Nome da pasta selecionada, sem efeito colateral.
        self.selected: str | None = None

        #: Miniaturas já decodificadas neste diálogo, por nome de pasta.
        self._thumbs: dict[str, QPixmap] = {}

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

        self.list.setViewMode(QListView.IconMode)

        # Grade de miniaturas: o item não se move nem se reordena quando a
        # janela muda de tamanho, só o número de colunas muda.
        self.list.setMovement(QListView.Static)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setWordWrap(False)

        # Sem ``setGridSize``: o tamanho da célula vem do ``sizeHint`` de
        # cada item, senão a linha de aviso ficaria do tamanho de uma
        # miniatura e o texto seria cortado.
        self.list.setSpacing(4)

        self.list.setSelectionMode(QAbstractItemView.SingleSelection)

        # O tamanho da célula vem do ``sizeHint`` de cada item, não de
        # ``uniformItemSizes``: a linha de aviso de busca vazia é mais
        # larga que uma miniatura e as duas precisam caber na mesma
        # grade. Com uma página cheia de pets os dois caminhos dão o
        # mesmo tamanho, então a perda é só na criação do item.
        self.list.setUniformItemSizes(False)

        # O delegate desenha a miniatura com o nome embaixo; a célula é
        # calculada com a fonte em uso, porque é a altura do nome que
        # define quantas linhas a grade tem.
        self.list.setItemDelegate(ThumbnailDelegate(self.list))

        #: Célula da grade: miniatura em cima, nome embaixo.
        self._cell = cell_size(self.list.fontMetrics().height())

        # Um clique simples troca na hora: é o comportamento esperado de
        # um seletor que se aplica em tempo real.
        self.list.itemClicked.connect(self._on_clicked)

        # Enter e duplo clique também escolhem.
        self.list.itemActivated.connect(self._on_activated)

        # currentItemChanged também dispara quando os itens são recriados
        # por uma busca ou paginação, então ele só serve para manter a
        # seleção em dia — nunca para trocar o pet.
        self.list.currentItemChanged.connect(self._on_selection_moved)

        # O texto não é pintado, então o nome vai para a barra de status,
        # seguindo o cursor. Sem isso, escolher um pet cujo nome não se
        # reconhece vira adivinhação — o que a miniatura deveria ter
        # resolvido.
        self.list.viewport().installEventFilter(self)

        self.status = QLabel(self)

        # Nome de pasta é texto puro: ``setTextFormat`` impede que um
        # nome com ``<`` vire marcação e apague a barra inteira.
        self.status.setTextFormat(Qt.PlainText)

        # A barra nunca some quando não há nome, senão a página pulava de
        # altura a cada mudança de página.
        self.status.setMinimumHeight(self.status.fontMetrics().height())

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
        layout.addWidget(self.status)
        layout.addLayout(pager)
        layout.addLayout(buttons)

        # A largura inicial deixa :data:`GRID_COLUMNS` colunas caberem sem
        # rolagem horizontal, e a altura, :data:`GRID_ROWS` linhas — ou
        # seja, uma página inteira visível de uma vez.
        width = page_width(self._cell.width())
        height = page_height(self._cell.height())

        self.setMinimumWidth(width)

        self.resize(max(self.width(), width), max(self.height(), height))

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

    # ------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

        # A mensagem de busca vazia ocupa a largura toda da grade, então
        # precisa acompanhar a janela.
        self._fit_message()

    def _fit_message(self) -> None:
        """Estica a linha de aviso pela largura da grade, se houver uma."""

        item = self.list.item(0)

        if item is None or not item.data(PLACEHOLDER_ROLE):
            return

        item.setSizeHint(
            QSize(self.list.viewport().width(), self._cell.height())
        )

    def eventFilter(self, watched, event) -> bool:
        """Acompanha o cursor na grade para nomear a figura apontada."""

        if (
            watched is self.list.viewport()
            and event.type() == QEvent.MouseMove
        ):
            self._on_hovered(event.position().toPoint())

        return super().eventFilter(watched, event)

    def _go(self, delta: int) -> None:
        page = self._page + delta

        if 0 <= page < self.page_count:
            self._page = page

            self._refresh()

    def _refresh(self) -> None:
        self.list.clear()

        items = self.page_slice(self._page)

        for name in items:
            self.list.addItem(self._make_item(name))

        # Recomeça sem seleção: marcar a primeira linha faria o clique
        # nela parecer não valer nada, já que ela já seria a atual.
        self.selected = None

        # Sem seleção depois de recriar os itens, o nome da página anterior
        # não pode ficar pendurado na barra de status.
        self._say(None)

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
            item = QListWidgetItem("(nenhum pet encontrado)")

            # Centralizado e com a largura da grade inteira: como não há
            # miniatura, o texto é a única pista de que a busca não achou
            # nada, e cortado numa célula de miniatura ele não diria
            # nada.
            item.setTextAlignment(Qt.AlignCenter)
            item.setData(PLACEHOLDER_ROLE, True)
            item.setSizeHint(self._cell)

            self.list.addItem(item)
            self.list.setCurrentRow(-1)

            self._fit_message()

    def _make_item(self, name: str) -> QListWidgetItem:
        """Item da miniatura de ``name``, com o nome como texto."""

        item = QListWidgetItem(name)

        # Sem miniatura, o delegate desenha o texto sozinho no meio da
        # célula; com miniatura, o nome é desenhado por ele, embaixo.
        item.setTextAlignment(Qt.AlignCenter)

        item.setSizeHint(self._cell)

        pixmap = self._thumbnail(name)

        if not pixmap.isNull():
            # ``QPixmap`` e não ``QIcon``: o delegate desenha o pixmap
            # direto, sem a variantem que o ícone traz junto.
            item.setData(Qt.DecorationRole, pixmap)

        # Nome longo vira reticências embaixo da figura, então a dica
        # guarda o texto inteiro.
        item.setToolTip(name)

        return item

    def _thumbnail(self, name: str) -> QPixmap:
        """Miniatura de ``name``, memoizada por diálogo.

        Só a página visível é desenhada, então dá para decodificar de
        novo a cada paginação sem travar. O cache evita o retrabalho de
        quem folheia para trás e para frente no mesmo diálogo.
        """

        if name not in self._thumbs:
            self._thumbs[name] = thumbnail(name)

        return self._thumbs[name]

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
            self._say(name)

    def _say(self, name: str | None) -> None:
        """Escreve ``name`` na barra de status, ou limpa se não houver."""

        self.status.setText(name or "")

    def _on_hovered(self, position) -> None:
        """O cursor mudou de figura; o nome acompanha.

        Sem isso, escolher um pet cujo nome não se reconhece vira
        adivinhação, que é justamente o que a miniatura resolveu.
        """

        item = self.list.itemAt(position)

        name = item.text() if item is not None else None

        if name and not name.startswith("("):
            self._say(name)
        elif self.list.currentItem() is None:
            # Saiu da grade sem passar por um pet e não há seleção: limpa,
            # para o nome não ficar de resíduo. Havendo seleção, o nome
            # dela é a melhor resposta — os vãos entre figuras não devem
            # apagar o que o usuário já tinha escolhido.
            self._say(None)

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