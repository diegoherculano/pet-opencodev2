#!/usr/bin/env python3
"""Ponto de entrada do pet.

A implementação vive no pacote ``petwatch``:

- ``petwatch.config``      — constantes e caminhos
- ``petwatch.states``      — estados e rótulos
- ``petwatch.theme``       — leitura do ``pet.json``
- ``petwatch.assets``      — escolha do asset decodificável e fatiamento
- ``petwatch.http``        — transporte do stream, leitura interrompível
- ``petwatch.discovery``   — senha, portas e sondagem do servidor
- ``petwatch.sse``         — parser do stream de eventos
- ``petwatch.events``      — evento -> estado
- ``petwatch.monitor``     — loop de conexão (roda em thread)
- ``petwatch.ui``          — janela, sprite e balão
- ``petwatch.app``         — montagem, sinais e encerramento
"""

from __future__ import annotations

from petwatch.app import main

if __name__ == "__main__":
    raise SystemExit(main())
