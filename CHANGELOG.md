# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui, no formato
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Adicionado

- **Windows.** O mesmo código roda em Linux, macOS e Windows, e sai como um
  `petwatch.exe` único que não precisa de Python, PySide6 nem plugin de Qt
  na máquina (`petwatch.spec`, onefile sem console, tema padrão embutido). O
  build roda no CI em `windows-latest` porque o PyInstaller não faz
  cross-compile; ver `docs/OPERATION.md`.
- **A porta do opencode vem da sondagem, não do nome do processo.** Antes,
  `discovery.py` rodava `ss -ltnp` e casava a linha que continha
  `"opencode"` — impossível no Windows, onde não há `ss` e o `netstat -ano`
  devolve PID em vez de nome, e frágil mesmo no Linux (o serviço pode estar
  num namespace de rede ou com outro nome). Agora o app lista as **portas em
  escuta** da máquina (`/proc/net/tcp`, `ss -ltn` ou `netstat -ano`, por
  plataforma) e pergunta a cada uma se é o opencode: `200` **e**
  `Content-Type: text/event-stream` em `/api/event`, com a senha. O tipo de
  conteúdo é o que separa o opencode de qualquer outro servidor local — sem
  ele, o primeiro dev server que respondesse "ok" seria adotado. No Linux a
  troca também economiza um `fork` a cada 2 s. A lista tem TTL de 10 s, a
  porta que respondeu é a primeira da próxima varredura, o teto de sondagens
  mantém o pior caso previsível, e `PETWATCH_PORT` pula a busca.
- **A instância única do Windows é um named pipe** (`pipe.py`,
  `instance.py`). Não há `fcntl`, e `os.kill(pid, SIGTERM)` no Windows é
  `TerminateProcess`: morte seca, sem handler, sem `shutdown()`, sem gravar
  as preferências. Nome de pipe é exclusivo, então o mesmo mecanismo garante
  "só um pet" e dá ao `--stop` um caminho limpo — o `quit()` do item
  **Fechar**, e não um `taskkill`. Quem assume a instância é o filho (o pai
  só pergunta), porque no Windows não há herança de handle confiável entre
  processos. A confirmação de que a janela abriu vai por arquivo temporário
  em vez de pipe, pelo mesmo motivo.
- **`console.py`**, que faz o app funcionar sem `sys.stdout`: num build
  `--noconsole` o PyInstaller põe `stdout` e `stderr` em `None`, e um
  `basicConfig()` sem argumentos manda as mensagens para o `lastResort`, que
  as descarta **sem avisar**. As frases do `--status`/`--stop` e da falha de
  abertura viram caixa de diálogo (`MessageBoxW` via `ctypes`, sem
  dependência nova).
- Os pets agora são procurados em `$PETWATCH_PETS_DIR`, `pets/` ao lado do
  executável, `%LOCALAPPDATA%\petwatch\pets` e, por último, os que vieram
  embutidos no executável. O diretório que existe vence; o resto continua
  como reserva para `load_theme`. Isso conserta também o `pip install`, que
  não achava os pets em nenhuma plataforma.
- `PETWATCH_PORT` para fixar a porta e pular a varredura.
- `PETWATCH_PASSWORD` para fixar a senha sem chamar o CLI. É a saída para o
  pet no Windows com o servidor no WSL, cuja senha mora em outro sistema de
  arquivos — e no Windows o pet ainda tenta o WSL sozinho (`wsl` +
  `~/.opencode/bin/opencode2 service get password`, com o `service.json` de
  reserva), porque cada sistema tem o seu `service.json`.

### Corrigido

- **O primeiro `pet.py` numa máquina sem diretório de estado** respondia
  "já existe um pet rodando (pid None)", sem pet na tela e sem pista do
  motivo. `claim()` recebia `FileNotFoundError` do `os.open` porque a pasta
  ainda não existia, e traduzia "não consegui abrir o arquivo" em "outro pet
  está rodando" — a leitura que a função faz do erro. Quem assume a instância
  cria o diretório agora; perguntar (`--status`) continua não deixando
  rastro. No Windows é o mesmo caso em `%LOCALAPPDATA%\petwatch`.
- `detached_command()` reexecutava o `pet.py` ao lado do pacote mesmo num
  build congelado, onde não há script e `sys.executable` é o próprio `.exe`.

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
- **Pet no Windows com o opencode no WSL.** O pet é WSL-only no Windows:
  o CLI procurado era só o `opencode2`, mas no Windows o scoop instala
  `opencode.exe` (sem o subcomando `service`), e a senha do Windows é
  outra — o servidor do WSL respondia `401` e o pet ficava em
  "conectando", só com a animação e sem balão. Agora o CLI local nem é
  consultado no Windows: a ordem é `PETWATCH_PASSWORD` e WSL direto
  (com cache de 60 s, porque o `wsl.exe` custa ~1 s por chamada).
- **Terminal piscando no `.exe`.** Cada `netstat`/`wsl` aberto pelo pet
  criava uma janela de console, e o monitor reconecta a cada 2 s — o
  sintoma era um terminal que não parava de aparecer. Os filhos agora sobem
  com `CREATE_NO_WINDOW`.

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
