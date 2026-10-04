"""Tamanhos do pet.

O tamanho mexe em três coisas ao mesmo tempo: o sprite desenhado, o
tamanho da janela e a distância da tela. Um preset precisa descrever as
três, senão o balão invade o sprite ou o pet sai da tela.

Com um balão por instância do opencode em ação entrou uma quarta: a
**largura e a altura de cada balão**. A largura é fixa por preset — o
texto é reticado, então a caixa não precisa acompanhar o conteúdo — e os
balões são empilhados **um acima do outro**, do mais urgente para o
menos.

Duas decisões que explicam os números:

- **A largura da janela é fixa** (a de um balão, ou o sprite se for
  maior). Se encolhesse até a quantidade de balões, o sprite — desenhado
  no meio da janela — iria para lá e para cá a cada aba que abre.
- **A altura acompanha a quantidade de balões**, sem teto. Um teto
  esconderia instâncias — e uma aba que o usuário não vê é exatamente a
  que ele precisa notar. O que limita a pilha é o esquecimento (ver
  ``sessions.QUIET_GRACE_SECONDS``), não um número: a instância sai
  quando deixa de estar em ação e ninguém precisa dela.
- **A largura é fixa** para o sprite não saltar de lugar a cada aba que
  abre; a janela cresce para cima, porque está ancorada pelo canto
  inferior direito, e o pet é desenhado no rodapé.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Medidas do balão na escala 1.0 ("médio"). Os outros presets multiplicam
#: por ``text_scale``, como já faziam com o balão único.
BASE_CARD_WIDTH = 152

BASE_CARD_TITLE_FONT = 11

BASE_CARD_SUBTITLE_FONT = 10

#: Folga entre dois balões empilhados. Pequena de propósito: são caixas
#: da mesma pilha, e uma folga grande lê como lista, não como pilha.
STACK_GAP = 6


@dataclass(frozen=True, slots=True)
class PetSize:
    """Um preset de tamanho.

    O texto do balão escala junto: um preset "Pequeno" com a mesma fonte
    do "Grande" fica desproporcional — a janela encolhe e o balão passa
    a ocupar quase toda a largura. Por isso o preset carrega os próprios
    tamanhos de fonte e paddings, derivados dos do "medio".
    """

    key: str
    label: str

    #: Largura e altura máximas do sprite, já com a escala do tema.
    sprite_width: int
    sprite_height: int

    #: Multiplicador aplicado a fonte, padding, raio e demais medidas
    #: do balão definidas no tema.
    text_scale: float = 1.0

    #: Largura fixa de um balão, já escalada.
    card_width: int = BASE_CARD_WIDTH

    #: Fontes do balão, já escaladas. Menores que as do balão único: a
    #: caixa é estreita e o texto longo é reticado.
    card_title_font: int = BASE_CARD_TITLE_FONT

    card_subtitle_font: int = BASE_CARD_SUBTITLE_FONT

    stack_gap: int = STACK_GAP

    # ------------------------------------------------------------
    # Pilha
    # ------------------------------------------------------------

    def window_width_for(self) -> int:
        """Largura da janela: um balão, ou o sprite se ele for maior."""

        return max(self.sprite_width, self.card_width)


#: Presets, do menor para o maior. O "medio" preserva o tamanho que o
#: projeto usava antes de os presets existirem.
#:
#: ``text_scale`` multiplica o balão **e** o sprite, para o conjunto
#: acompanhar em vez de ficar do mesmo tamanho numa janela menor. As
#: larguras saíram de um balão de 152px (médio): a janela do "médio" ficou
#: com 152px de largura, mais estreita que a do balão único antigo.
def _preset(key: str, label: str, sprite: tuple[int, int],
            scale: float) -> PetSize:
    return PetSize(
        key=key,
        label=label,
        sprite_width=sprite[0],
        sprite_height=sprite[1],
        text_scale=scale,
        card_width=max(1, round(BASE_CARD_WIDTH * scale)),
        card_title_font=max(6, round(BASE_CARD_TITLE_FONT * scale)),
        card_subtitle_font=max(6, round(BASE_CARD_SUBTITLE_FONT * scale)),
        stack_gap=max(1, round(STACK_GAP * scale)),
    )


PET_SIZES: dict[str, PetSize] = {
    "small": _preset("small", "Pequeno", (96, 104), 0.82),
    "medium": _preset("medium", "Médio", (132, 144), 1.0),
    "large": _preset("large", "Grande", (200, 218), 1.24),
}

#: Tamanho usado quando nada foi escolhido.
DEFAULT_SIZE_KEY = "medium"

#: Ordem de exibição no menu.
SIZE_ORDER = ("small", "medium", "large")


def get_size(key: str) -> PetSize:
    """Preset ``key``; volta ao padrão para valor desconhecido."""

    return PET_SIZES.get(key, PET_SIZES[DEFAULT_SIZE_KEY])