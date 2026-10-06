# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui, no formato
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Corrigido

- **"Ready" no meio do trabalho** (bug 22), o oposto do bug 21. O relógio
  de silêncio da instância só era movido por eventos que viravam estado **e**
  traziam `sessionID`, e no stream real nenhum dos dois é comum: 771 dos 819
  eventos de um turno de 150 s eram `session.reasoning.delta` (não vira estado
  por desenho) e os eventos de arquivo e shell não trazem `sessionID` — o
  dono deles é o `location`, que estava no envelope e nunca era lido. Some-se
  que o carimbo usava o relógio do último poll (20 s), encurtando o silêncio
  válido de 45 s para 25 s. Agora `monitor.session_alive` publica todo evento,
  `note_alive` move o relógio sem mudar estado, e `Instance.demoted` marca o
  "pronto" por silêncio como reversível — um rebaixamento deixou de ser
  permanente. O carimbo também passou a usar o relógio real, para o timeout
  voltar a ser um número só em vez de uma faixa de 40 s a 65 s. O silêncio de
  verdade ainda devolve para "Ready" (bug 21).
- **Balão preso em "Thinking" com o agente parado** (bug 21). Duas falhas
  independentes: `demote_stale` media `touched_at`, que a consulta de
  `/api/session/active` reescreve a cada ciclo, então uma sessão que o
  servidor continuava listando como `running` sem emitir evento nenhum
  nunca era rebaixada; e a única chamada de `demote_stale` estava atrás do
  watchdog **global**, que não dispara enquanto outra aba trabalha. Agora
  existe `Instance.evented_at`, movido só pelo stream, e `on_active`
  também envelhece o balão. O opencode em si não era travado pelo pet —
  medido antes de mudar qualquer linha, e está em `docs/BUGS.md`.
- **"Thinking" com a pergunta aberta na tela** (bug 20). Um projeto que já
  não tem pasta continua em `GET /api/project`, e `/api/form` responde
  **404** a ele — o mesmo 404 de um servidor sem a rota, que desligava o
  "aguardando" inteiro. As duas coisas agora se distinguem pelo `_tag` do
  corpo (`LocationNotFoundError` = *location* morto; corpo vazio = rota
  ausente), e um *location* morto é isolado em vez de derrubar a consulta.
- A trava da degradação passou a guardar **de quem** é o pedido. O
  `session.idle` de outra aba não solta mais a espera de uma aba que
  continua com a pergunta aberta — o `monitor` extrai o `sessionID` uma vez
  e o entrega em `ask_seen` e no `released` novo.
- O balão diz "Waiting" junto com o sprite na degradação. Quem decide o que
  desenhar é `SessionBoard.cards()`, e não mais a interface.
- *Location* morto sai da varredura em vez de custar dois GETs a cada
  ciclo; volta sozinho quando a lista de projetos é relida.
- O aviso de degradação subiu de `INFO` para `WARNING`, para aparecer no log
  do modo solto.

### Alterado

- Seletor de pet mostra a **miniatura** de cada tema (`preview.gif` do
  diretório, ou qualquer outra imagem que o Qt consiga abrir) com o nome
  da pasta embaixo, em vez do nome sozinho. O nome continua filtrando a
  busca e sendo o que o clique emite; na tela ele aparece sob a figura, na
  barra de status (sob o cursor ou na seleção) e na dica do item, porque
  nome longo vira reticências na célula.
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
