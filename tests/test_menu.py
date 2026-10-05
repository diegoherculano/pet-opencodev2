"""Seletor de pet: busca, paginação e troca em tempo real.

O módulo também cobre o menu de contexto (``PetMenu``), que é o outro
menu do pet — o de tamanho e "sempre no topo".
"""

from __future__ import annotations

import dataclasses
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QListWidgetItem,
    QStyle,
    QStyleOptionViewItem,
)

app = QApplication.instance() or QApplication([])

from petwatch.sizes import PET_SIZES, SIZE_ORDER  # noqa: E402
from petwatch.theme import list_themes  # noqa: E402
from petwatch.ui import pet_picker  # noqa: E402
from petwatch.ui.menu import MenuHandlers, PetMenu  # noqa: E402
from petwatch.ui.pet_picker import (  # noqa: E402
    CAPTION_GAP,
    CELL_PADDING,
    PAGE_SIZE,
    PLACEHOLDER_ROLE,
    THUMB_SIZE,
    PetPicker,
    cell_size,
    page_height,
    page_width,
    thumbnail,
)


class Fixture(unittest.TestCase):
    """PetPicker com uma lista controlada, sem depender do disco."""

    NAMES = [f"pet{i:03d}" for i in range(25)] + ["zeta-unico"]

    def setUp(self):
        self.picker = PetPicker()

        self.addCleanup(self.picker.close)

        self.picker._all = list(self.NAMES)
        self.picker._filtered = list(self.NAMES)
        self.picker._page = 0

        self.picker._refresh()

        self.chosen: list[str] = []
        self.picker.theme_chosen.connect(self.chosen.append)

    # ------------------------------------------------------------
    # Atalhos
    # ------------------------------------------------------------

    def click(self, row: int) -> None:
        item = self.picker.list.item(row)

        self.assertIsNotNone(item, f"linha {row} inexistente")

        self.picker.list.itemClicked.emit(item)

    def rows(self) -> list[str]:
        return [
            self.picker.list.item(i).text()
            for i in range(self.picker.list.count())
        ]


class ListTests(Fixture):
    def test_lists_folder_names(self):
        """A lista vem dos nomes de pasta, sem ler pet.json."""

        self.assertEqual(len(self.picker._all), 26)

        self.picker.search.setText("")

        self.assertEqual(self.rows()[0], "pet000")

    def test_pages_hold_page_size_items(self):
        self.picker.search.setText("")

        self.picker._refresh()

        self.assertEqual(len(self.rows()), PAGE_SIZE)
        self.assertEqual(self.picker.page_count, 3)

    def test_last_page_is_partial(self):
        self.picker.search.setText("")
        self.picker._page = self.picker.page_count - 1
        self.picker._refresh()

        self.assertEqual(len(self.rows()), 26 - PAGE_SIZE * 2)

    def test_page_label(self):
        self.picker.search.setText("")
        self.picker._page = 0
        self.picker._refresh()

        self.assertIn("Página 1 de 3", self.picker._page_label.text())
        self.assertIn("26 pets", self.picker._page_label.text())

    def test_singular_label(self):
        self.picker.search.setText("zeta-unico")
        self.picker._refresh()

        self.assertIn("(1 pet)", self.picker._page_label.text())

    def test_paging_navigation(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.picker._go(1)
        self.assertEqual(self.picker._page, 1)
        self.assertEqual(self.rows()[0], "pet010")

        self.picker._go(-1)
        self.assertEqual(self.picker._page, 0)

    def test_paging_clamps_at_the_edges(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.picker._go(-5)
        self.assertEqual(self.picker._page, 0)

        self.picker._page = self.picker.page_count - 1
        self.picker._refresh()
        self.picker._go(5)
        self.assertEqual(self.picker._page, self.picker.page_count - 1)

    def test_buttons_reflect_position(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.assertFalse(self.picker.previous_button.isEnabled())
        self.assertTrue(self.picker.next_button.isEnabled())

        self.picker._go(1)
        self.assertTrue(self.picker.previous_button.isEnabled())

        self.picker._go(99)
        self.assertTrue(self.picker.next_button.isEnabled())


class ThumbnailFixture(Fixture):
    """Mesma coisa que :class:`Fixture`, mas com pets de verdade.

    A miniatura vem do disco, então um nome fictício (``pet000``) não
    produz imagem nenhuma e os testes de figura não teriam o que ver.
    """

    def setUp(self):
        super().setUp()

        real = list_themes()[:PAGE_SIZE]

        self.assertTrue(real, "o repositório precisa de pets para o teste")

        self.picker._all = list(real)
        self.picker._filtered = list(real)
        self.picker._page = 0
        self.picker._thumbs.clear()

        self.picker._refresh()

    def render(self, item: QListWidgetItem) -> QImage:
        """Rasteriza o delegate do item como a tela faria."""

        index = self.picker.list.indexFromItem(item)

        option = QStyleOptionViewItem()
        option.initFrom(self.picker.list)

        # O retângulo real vem em coordenadas da viewport, que para as
        # linhas abaixo da primeira cai fora do canvas; só o tamanho
        # importa para a pintura.
        option.rect = QRect(
            QPoint(0, 0),
            self.picker.list.visualItemRect(item).size(),
        )

        option.state |= QStyle.State_Enabled

        canvas = QImage(option.rect.size(), QImage.Format_ARGB32)

        canvas.fill(Qt.white)

        painter = QPainter(canvas)

        self.picker.list.itemDelegate().paint(painter, option, index)

        painter.end()

        return canvas

    def painted_ratio(self, image: QImage) -> float:
        """Fração do quadro com algo diferente do branco de fundo.

        Amostragem em passo 2: uma figura pequena já passa folgado, e o
        teste fica rápido mesmo com o quadro inteiro.
        """

        samples = 0
        painted = 0

        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                samples += 1

                if image.pixelColor(x, y).name() != "#ffffff":
                    painted += 1

        return painted / max(1, samples)

    def caption_height(self) -> int:
        """Altura da faixa do nome, com a fonte em uso."""

        return self.picker.list.fontMetrics().height()

    def caption_painted(self, item: QListWidgetItem) -> bool:
        """Diz se a faixa do nome embaixo da figura foi pintada."""

        canvas = self.render(item)

        top = canvas.height() - CELL_PADDING - self.caption_height()

        band = canvas.copy(
            QRect(0, max(0, top), canvas.width(), self.caption_height())
        )

        return self.painted_ratio(band) > 0.05

    def name_cell(self, name: str) -> QListWidgetItem:
        """Item de ``name`` na página atual, pelo texto."""

        for row in range(self.picker.list.count()):
            item = self.picker.list.item(row)

            if item.text() == name:
                return item

        self.fail(f"{name} não está na página")


class CellTests(unittest.TestCase):
    """A geometria da célula: figura em cima, nome embaixo."""

    def test_the_cell_leaves_room_for_the_name(self):
        """A altura da célula cobre figura + folga + nome."""

        caption = 20

        cell = cell_size(caption)

        self.assertEqual(cell.width(), THUMB_SIZE + CELL_PADDING * 2)

        self.assertEqual(
            cell.height(),
            THUMB_SIZE + CAPTION_GAP + caption + CELL_PADDING * 2,
        )

    def test_a_taller_font_makes_a_taller_cell(self):
        """O nome é o que define a altura; a fonte é o que a mede."""

        self.assertGreater(cell_size(30).height(), cell_size(14).height())

    def test_the_page_size_leaves_no_scrollbar(self):
        """A janela inicial é larga e alta o bastante para a página."""

        cell = cell_size(14)

        # 5 colunas de ``GRID_COLUMNS`` e 2 linhas de ``GRID_ROWS``, mais a
        # moldura do diálogo.
        self.assertGreater(
            page_width(cell.width()),
            cell.width() * 5,
        )

        self.assertGreater(
            page_height(cell.height()),
            cell.height() * 2,
        )


class ThumbnailTests(ThumbnailFixture):
    """A lista mostra a figura do pet, não o nome da pasta."""

    def test_items_carry_a_thumbnail(self):
        """O item da página carrega um pixmap, não só o nome."""

        for row in range(self.picker.list.count()):
            with self.subTest(name=self.picker.list.item(row).text()):
                self.assertIsInstance(
                    self.picker.list.item(row).data(Qt.DecorationRole),
                    QPixmap,
                )

    def test_name_still_lives_in_the_item(self):
        """A busca e o clique dependem do texto; a figura é que entra."""

        item = self.picker.list.item(0)

        self.assertEqual(item.text(), self.picker._all[0])
        self.assertIsInstance(item.data(Qt.DecorationRole), QPixmap)

    def test_tooltip_carries_the_name(self):
        """O balão do cursor nomeia a figura."""

        self.assertEqual(self.picker.list.item(0).toolTip(), self.picker._all[0])

    def test_delegate_draws_the_pixmap(self):
        """A miniatura ocupa a grade; um nome miúdo não ocuparia."""

        rendered = self.render(self.picker.list.item(0))

        # O delegate deixa respiro nas bordas e uma faixa para o nome,
        # então a área útil é menor que o quadro; 15% é folgado para
        # figura e impossível para texto miúdo.
        self.assertGreater(self.painted_ratio(rendered), 0.15)

    def test_the_name_is_painted_below_the_pixmap(self):
        """O nome volta para a tela, embaixo da figura."""

        for row in range(self.picker.list.count()):
            with self.subTest(name=self.picker.list.item(row).text()):
                self.assertTrue(self.caption_painted(self.picker.list.item(row)))

    def test_the_caption_is_below_the_pixmap(self):
        """Regressão: nome acima da figura troca as duas faixas."""

        item = self.picker.list.item(0)

        canvas = self.render(item)

        height = canvas.height()

        figure_bottom = height - (
            CAPTION_GAP + self.caption_height() + CELL_PADDING
        )

        figure = canvas.copy(QRect(0, 0, canvas.width(), figure_bottom))

        caption = canvas.copy(
            QRect(0, figure_bottom, canvas.width(), height - figure_bottom)
        )

        # Figura pintada em cima, nome embaixo: a faixa de baixo é a mais
        # esparsa das duas.
        self.assertGreater(self.painted_ratio(figure), 0.10)
        self.assertLess(self.painted_ratio(caption), 0.5)

    def test_a_long_name_is_elided_not_cut(self):
        """Nome que não cabe vira reticências, não metade da palavra."""

        long_name = "a" * 80

        self.picker._all = [long_name, self.picker._all[0]]
        self.picker._filtered = list(self.picker._all)

        self.picker._refresh()

        item = self.name_cell(long_name)

        self.assertTrue(self.caption_painted(item))
        self.assertEqual(item.toolTip(), long_name)

    def test_every_repository_pet_has_a_thumbnail(self):
        """Nenhum tema do repositório fica sem figura."""

        for name in list_themes()[:25]:
            with self.subTest(name=name):
                self.assertFalse(thumbnail(name).isNull())

    def test_the_thumbnail_is_square_bounded(self):
        """A miniatura cabe na grade, sem esticar a figura."""

        pixmap = thumbnail(self.picker._all[0], size=48)

        self.assertLessEqual(max(pixmap.width(), pixmap.height()), 48)
        self.assertGreater(min(pixmap.width(), pixmap.height()), 0)

    def test_unknown_pet_has_no_thumbnail(self):
        """Nome sem pasta devolve pixmap vazio, não estoura."""

        self.assertTrue(thumbnail("nao-existe-mesmo").isNull())

    def test_thumbnails_are_memoized(self):
        """Voltar a uma página não decodifica a imagem de novo."""

        calls: list[str] = []

        real = pet_picker.thumbnail

        def counting(name, *args, **kwargs):
            calls.append(name)

            return real(name, *args, **kwargs)

        pet_picker.thumbnail = counting

        self.addCleanup(setattr, pet_picker, "thumbnail", real)

        self.picker._thumbs.clear()

        for _ in range(3):
            self.picker._page = 0
            self.picker._refresh()

        # Uma decodificação por pet da página, mesmo com três passagens.
        self.assertEqual(len(calls), self.picker.list.count())

    def test_paging_back_reuses_the_cache(self):
        """A miniatura da página 0 não é refeita ao voltar."""

        self.picker._go(1)
        self.picker._go(-1)

        self.assertEqual(len(self.picker._thumbs), self.picker.list.count())

    def test_a_pet_without_a_figure_still_lists(self):
        """Diretório sem imagem não some da lista."""

        self.picker._all = ["nao-existe-mesmo"]
        self.picker._filtered = ["nao-existe-mesmo"]

        self.picker._refresh()

        self.assertEqual(self.rows(), ["nao-existe-mesmo"])
        self.assertIsNone(self.picker.list.item(0).data(Qt.DecorationRole))

    def test_a_page_fits_without_scrolling(self):
        """Regressão: a janela inicial cortava a última linha da página."""

        self.picker.resize(
            page_width(self.picker._cell.width()),
            page_height(self.picker._cell.height()),
        )

        app.processEvents()

        scrollbars = (
            self.picker.list.horizontalScrollBar(),
            self.picker.list.verticalScrollBar(),
        )

        for bar in scrollbars:
            with self.subTest(bar=bar.orientation()):
                self.assertEqual(bar.maximum(), 0)

    def test_the_message_spans_the_grid(self):
        """A linha de "nenhum pet encontrado" não fica numa célula."""

        self.picker.show()

        self.picker.search.setText("naoexiste")

        app.processEvents()

        item = self.picker.list.item(0)

        self.assertTrue(item.data(PLACEHOLDER_ROLE))
        self.assertGreater(
            item.sizeHint().width(),
            self.picker._cell.width(),
        )

    def test_the_message_follows_the_window(self):
        """Redimensionar a janela reestica a mensagem."""

        self.picker.show()

        self.picker.search.setText("naoexiste")

        app.processEvents()

        before = self.picker.list.item(0).sizeHint().width()

        self.picker.resize(self.picker.width() + 120, self.picker.height())

        app.processEvents()

        self.assertGreater(
            self.picker.list.item(0).sizeHint().width(),
            before,
        )


class StatusTests(Fixture):
    """O nome da pasta aparece na barra de status."""

    def test_names_the_selection(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.picker.list.setCurrentRow(1)

        self.assertEqual(self.picker.status.text(), "pet001")

    def test_names_the_hovered_pet(self):
        """Como o nome não é pintado, ele acompanha o cursor."""

        self.picker.search.setText("")
        self.picker._refresh()

        rect = self.picker.list.visualItemRect(self.picker.list.item(2))

        self.picker._on_hovered(rect.center())

        self.assertEqual(self.picker.status.text(), "pet002")

    def test_hovering_the_gap_keeps_the_selection_name(self):
        """Passar pelo vão entre figuras não apaga o nome da seleção."""

        self.picker.search.setText("")
        self.picker.list.setCurrentRow(1)
        self.picker._refresh()

        self.picker.list.setCurrentRow(1)

        self.picker._on_hovered(QPoint(-1, -1))

        self.assertEqual(self.picker.status.text(), "pet001")

    def test_cleared_when_leaving_the_grid(self):
        """Sem seleção, sair da grade limpa o nome."""

        self.picker.search.setText("")
        self.picker._refresh()

        self.picker.list.setCurrentRow(-1)
        self.picker._on_hovered(QPoint(-1, -1))

        self.assertEqual(self.picker.status.text(), "")

    def test_cleared_after_a_refresh(self):
        """O nome da página anterior não fica pendurado."""

        self.picker.search.setText("")
        self.picker.list.setCurrentRow(1)
        self.picker._refresh()

        self.assertEqual(self.picker.status.text(), "")

    def test_no_match_message_is_not_named(self):
        """A linha de aviso não é um pet, então não ganha nome."""

        self.picker.search.setText("naoexiste")

        self.assertEqual(self.picker.status.text(), "")


class SearchTests(Fixture):
    def test_filters_by_substring(self):
        self.picker.search.setText("pet01")

        self.assertIn("pet010", self.picker._filtered)
        self.assertNotIn("pet000", self.picker._filtered)

    def test_search_is_case_insensitive(self):
        self.picker.search.setText("ZETA")

        self.assertEqual(self.picker._filtered, ["zeta-unico"])

    def test_no_match_shows_a_message(self):
        self.picker.search.setText("naoexiste")

        self.assertEqual(self.rows(), ["(nenhum pet encontrado)"])
        self.assertFalse(self.picker.next_button.isEnabled())
        self.assertFalse(self.picker.previous_button.isEnabled())

    def test_search_restarts_at_first_page(self):
        self.picker.search.setText("")
        self.picker._go(2)
        self.picker._refresh()

        self.picker.search.setText("pet01")

        self.assertEqual(self.picker._page, 0)

    def test_clearing_restores_everything(self):
        self.picker.search.setText("zeta")
        self.picker.search.clear()

        self.assertEqual(len(self.picker._filtered), 26)


class RealtimeTests(Fixture):
    def test_click_emits_the_theme(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.click(0)

        self.assertEqual(self.chosen, ["pet000"])

    def test_clicking_already_selected_still_emits(self):
        """O clique precisa funcionar mesmo sem mudar a seleção."""

        self.picker.search.setText("")
        self.picker._refresh()

        self.click(0)
        self.click(0)
        self.click(0)

        self.assertEqual(self.chosen, ["pet000"] * 3)

    def test_typing_never_switches(self):
        for letter in "pet01":
            self.picker.search.setText(self.picker.search.text() + letter)

        self.assertEqual(self.chosen, [])

    def test_paging_never_switches(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.picker._go(1)

        self.assertEqual(self.chosen, [])

    def test_search_never_switches(self):
        self.picker.search.setText("pet01")

        self.assertEqual(self.chosen, [])

    def test_no_match_never_switches(self):
        self.picker.search.setText("naoexiste")

        self.click(0)

        self.assertEqual(self.chosen, [])

    def test_activated_emits(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.picker.list.itemActivated.emit(self.picker.list.item(2))

        self.assertEqual(self.chosen, ["pet002"])

    def test_preselect_marks_without_emitting(self):
        self.chosen.clear()

        self.picker.preselect("pet012")

        self.assertEqual(self.chosen, [])
        self.assertEqual(self.picker.selected, "pet012")
        self.assertEqual(self.picker.list.currentItem().text(), "pet012")

    def test_preselect_jumps_to_the_right_page(self):
        self.picker.preselect("pet020")

        self.assertEqual(self.picker._page, 2)

    def test_preselect_ignores_unknown_name(self):
        self.picker.search.setText("")
        self.picker._refresh()

        self.picker.preselect("nao-existe")

        self.assertEqual(self.picker._page, 0)


class WindowTests(unittest.TestCase):
    def test_is_modeless_so_the_pet_stays_visible(self):
        picker = PetPicker()

        self.addCleanup(picker.close)

        self.assertFalse(picker.isModal())
        self.assertTrue(
            picker.windowFlags() & Qt.WindowStaysOnTopHint
        )

    def test_has_a_search_field(self):
        picker = PetPicker()

        self.addCleanup(picker.close)

        self.assertTrue(picker.search.placeholderText())


class RepositoryTests(unittest.TestCase):
    def test_real_folder_listing_is_paginated(self):
        picker = PetPicker()

        self.addCleanup(picker.close)

        total = len(picker._all)

        self.assertGreater(total, 100)
        self.assertEqual(
            picker.page_count,
            (total + PAGE_SIZE - 1) // PAGE_SIZE,
        )

    def test_every_name_is_a_loadable_theme(self):
        from petwatch.theme import load_theme

        picker = PetPicker()

        self.addCleanup(picker.close)

        for name in picker._all[:25]:
            with self.subTest(name=name):
                self.assertIsNotNone(load_theme(name).asset_path)


class ContextMenuFixture(unittest.TestCase):
    """``PetMenu`` com o ciclo real: o clique volta pelo ``sync_size``."""

    def setUp(self):
        self.picked: list[str] = []
        self.top: list[bool] = []

        # O on_size do app real termina em menu.sync_size(); sem essa
        # volta o menu não sabe qual preset passou a valer.
        def on_size(key: str) -> None:
            self.picked.append(key)

            self.menu.sync_size(key)

        self.menu = PetMenu(
            None,
            MenuHandlers(
                on_size=on_size,
                on_open_picker=lambda: None,
                on_toggle_on_top=self.top.append,
                on_quit=lambda: None,
                current_size=self.START,
            ),
        )

        self.addCleanup(self.menu.close)

    #: Sobrescrito pelas subclasses que testam outro preset de partida.
    START = "small"

    def action(self, key: str):
        return self.menu._size_actions[key]

    def checked(self) -> list[str]:
        return [key for key, a in self.menu._size_actions.items() if a.isChecked()]


class SizeActionTests(ContextMenuFixture):
    def test_marks_only_the_current_size(self):
        self.assertEqual(self.checked(), [self.START])

    def test_clicking_emits_the_size(self):
        self.action("medium").trigger()

        self.assertEqual(self.picked, ["medium"])

    def test_clicking_the_marked_size_does_nothing(self):
        """Reclicar no item ativo não redimensiona de novo."""

        self.action(self.START).trigger()

        self.assertEqual(self.picked, [])

    def test_every_preset_is_reachable(self):
        """Nenhum preset fica preso: o ciclo passa por todos."""

        # O preset de partida é o único clique que não emite nada, e
        # só na primeira volta: depois dele o menu já sincronizou.
        expected = [
            key
            for key in SIZE_ORDER
            if key != self.START
        ] + [self.START]

        for key in expected:
            with self.subTest(size=key):
                self.action(key).trigger()

                self.assertEqual(self.picked[-1], key)

        self.assertEqual(len(self.picked), len(expected))

    def test_can_go_back_to_the_starting_size(self):
        """Regressão: voltar ao preset de partida não era aplicado.

        A guarda de "já está nesse tamanho" lia um valor congelado na
        montagem do menu, então toda volta ao tamanho inicial era
        descartada — o item marcava, mas o pet não redimensionava.
        """

        for key in SIZE_ORDER:
            if key == self.START:
                continue

            self.action(key).trigger()

        self.assertNotEqual(self.picked[-1], self.START)

        self.action(self.START).trigger()

        self.assertEqual(self.picked[-1], self.START)
        self.assertEqual(self.checked(), [self.START])

    def test_sync_size_moves_the_mark(self):
        self.menu.sync_size("large")

        self.assertEqual(self.checked(), ["large"])

    def test_sync_size_matching_by_key_not_by_label(self):
        """Dois presets com o mesmo rótulo não podem marcar os dois."""

        small_label = PET_SIZES["small"].label

        PET_SIZES["large"] = dataclasses.replace(
            PET_SIZES["large"], label=small_label
        )

        self.addCleanup(
            PET_SIZES.__setitem__,
            "large",
            dataclasses.replace(PET_SIZES["large"], label="Grande"),
        )

        self.menu.sync_size("small")

        self.assertEqual(self.checked(), ["small"])


class SizeActionFromEveryStart(ContextMenuFixture):
    """A volta ao preset de partida vale para os três pontos de partida."""

    START = "medium"


class SizeActionFromLarge(ContextMenuFixture):
    START = "large"


class OnTopTests(ContextMenuFixture):
    def test_reflects_the_starting_state(self):
        self.assertTrue(self.menu.on_top_action.isChecked())

        self.menu.on_top_action.trigger()

        self.assertEqual(self.top, [False])
        self.assertFalse(self.menu.on_top_action.isChecked())

    def test_stays_clickable_when_the_backend_supports_it(self):
        self.assertTrue(self.menu.on_top_action.isEnabled())

    def test_sync_does_not_re_emit(self):
        self.menu.sync_on_top(False)

        self.assertEqual(self.top, [])
        self.assertFalse(self.menu.on_top_action.isChecked())


class OnTopUnsupportedTests(unittest.TestCase):
    """Backend sem z-order: o item continua visível, mas sem prometer nada."""

    def setUp(self):
        self.top: list[bool] = []

        self.menu = PetMenu(
            None,
            MenuHandlers(
                on_size=lambda key: None,
                on_open_picker=lambda: None,
                on_toggle_on_top=self.top.append,
                on_quit=lambda: None,
                on_top_supported=False,
            ),
        )

        self.addCleanup(self.menu.close)

    def test_the_item_is_still_there(self):
        """Esconder o item pareceria que a opção nunca existiu."""

        self.assertIsNotNone(self.menu.on_top_action)

    def test_the_item_is_disabled(self):
        self.assertFalse(self.menu.on_top_action.isEnabled())

    def test_the_tooltip_names_the_reason(self):
        tooltip = self.menu.on_top_action.toolTip()

        self.assertIn(QGuiApplication.platformName(), tooltip)
        self.assertIn("z-order", tooltip)

    def test_the_stored_preference_is_still_shown(self):
        """O flag continua sendo aplicado; só o efeito é que não existe."""

        self.assertTrue(self.menu.on_top_action.isChecked())


if __name__ == "__main__":
    unittest.main()