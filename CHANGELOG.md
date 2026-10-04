# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui, no formato
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Alterado

- README enxutado no padrão de grandes projetos (visão geral + links);
  detalhes movidos para `docs/` (`OPERATION`, `ARCHITECTURE`, `TESTING`,
  `BUGS`). URLs do projeto declaradas no `pyproject`.

## [2.0.0] - 2026-10-03

### Adicionado

- Pet em módulos (`petwatch/`): config, daemon, states, theme, assets, http,
  discovery, sse, events, pending, sessions, idle, sizes, prefs, monitor,
  `ui/` (attention, menu, pet_picker, bubble, pet_widget, tray).
- Segundo plano: `python pet.py` devolve o terminal, log em
  `~/.local/state/petwatch/pet.log`, `--status`/`--stop`/`--foreground`.
- Um balão por instância do opencode em ação (`SessionBoard`,
  `GET /api/session/active`), com nome da sessão no subtítulo.
- Estado "aguardando" decidido por consulta ao servidor (`GET /api/form`,
  `GET /api/permission/request` por *location*), não pelo stream.
- Menu do botão direito: tamanho, seletor de pet, sempre no topo, fechar.
- Ícone na bandeja com o mesmo menu; watchdog de 45s; pulso de atenção.

### Corrigido

- Ver a seção "Bugs corrigidos nesta versão" no `README.md` (19 itens, do
  sprite ausente ao falso positivo de "Waiting").
