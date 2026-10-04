# petwatch

Pet de desktop que observa um servidor [opencode](https://opencode.ai) local
e mostra o que está acontecendo: pronto, trabalhando ou aguardando resposta —
um balão por sessão em ação.

![petwatch em ação: balão "Thinking" sobre o sprite do Eevee](docs/demo.png)

[![CI](https://github.com/diegoherculano/pet-opencodev2/actions/workflows/ci.yml/badge.svg)](https://github.com/diegoherculano/pet-opencodev2/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

## Recursos

- Um balão por sessão do opencode em ação, com o nome da sessão
- "Aguardando resposta" verificado no servidor — nunca chutado do stream
- Roda em segundo plano: o prompt volta na hora, `--status` / `--stop`
- 1738 temas, seletor com busca, 3 tamanhos, sempre no topo, ícone na bandeja
- Só lê: nenhum `POST`/`DELETE`, então não responde nem interrompe nada

## Requisitos

- Python >= 3.11 e Linux com Qt (PySide6)
- Servidor `opencode` local rodando
- No Ubuntu, o plugin de WebP do Qt: `sudo apt install qt6-image-formats-plugins`

## Instalação

```bash
pip install -e .
```

Sem instalar também funciona, direto da fonte:

```bash
python pet.py            # ou: python -m petwatch
```

## Uso

```bash
python pet.py              # segundo plano; prompt volta na hora
python pet.py --status     # está rodando? qual o pid?
python pet.py --stop       # encerra
python pet.py --foreground # colado no terminal (aqui o Ctrl+C funciona)
```

- **Botão esquerdo** arrasta o pet · **botão direito** abre o menu
- Log em `~/.local/state/petwatch/pet.log`, prefs em `~/.config/petwatch/prefs.json`
- Observar um projeto só: `PETWATCH_DIRECTORY=/caminho/do/projeto python pet.py`

## Documentação

- [`docs/OPERATION.md`](docs/OPERATION.md) — segundo plano, menu, prefs, log
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — pipeline, eventos, watchdog, sprites
- [`docs/TESTING.md`](docs/TESTING.md) — a suíte de 478 testes
- [`docs/BUGS.md`](docs/BUGS.md) — histórico de bugs corrigidos
- [`CHANGELOG.md`](CHANGELOG.md) — mudanças por versão

## Contribuindo

Leia o [`CONTRIBUTING.md`](CONTRIBUTING.md) antes do PR. Toda interação segue
o [`Código de Conduta`](CODE_OF_CONDUCT.md). Vulnerabilidades: siga o
[`SECURITY.md`](SECURITY.md), nunca abra issue pública.

## Licença

MIT — ver [`LICENSE`](LICENSE).
