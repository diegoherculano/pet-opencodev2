"""Seletor de pet: busca, paginação e troca em tempo real.

O módulo também cobre o menu de contexto (``PetMenu``), que é o outro
menu do pet — o de tamanho e "sempre no topo".
"""

from __future__ import annotations

import dataclasses
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.sizes import PET_SIZES, SIZE_ORDER  # noqa: E402
from petwatch.ui.menu import MenuHandlers, PetMenu  # noqa: E402
from petwatch.ui.pet_picker import PAGE_SIZE, PetPicker  # noqa: E402


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