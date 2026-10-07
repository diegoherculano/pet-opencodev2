# Testes

Como rodar e o que a suíte cobre. Visão geral em [README](../README.md).


```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
```

677 testes cobrindo o mapeamento de eventos, o parser SSE, a leitura do
`pet.json`, a escolha do asset, o transporte HTTP, as consultas de
pendência e de sessão, o quadro de instâncias, a pilha de balões
(empilhamento ancorado embaixo, janela fixa, reticências, cor de aviso), a
prova de que o pet não se move ao entrar ou sair um balão, a geometria da
pintura, o repaint da animação, o isolamento das preferências, o
encerramento por sinal e a saída do terminal.

Dois pares de testes são **simétricos de propósito**, e um deles falha se o
outro for "corrigido" demais: `NoteAliveTests` exige que um agente que segue
emitindo eventos fique em "Thinking" (bug 22), e
`test_a_real_still_session_still_goes_ready` exige que um silêncio de verdade
volte para "Ready" (bug 21). O mesmo vale para
`DemotedIsRevocableTests` e para `test_an_idle_the_server_reported_is_not_ours_to_revoke`.

Os testes rodam nos dois cenários: com e sem o plugin de WebP. Alguns
detalhes que valem fora do código:

- `test_interrupt_unblocks_a_stuck_read` sobe um servidor HTTP local que
  serve um stream SSE aberto e confirma que `Connection.interrupt()`
  destrava uma leitura realmente presa.
- `tests/test_app.py` sobe um `PetApplication` de verdade e dispara um
  `SIGINT` no próprio processo.
- `tests/test_assets.py` confere que a grade 8x9 divide a spritesheet dos
  1738 temas e que nenhuma linha de ação cai numa célula vazia.
- `tests/test_ui.py::RepaintTests` roda o event loop de verdade contando
  `QEvent.Paint`: é o que pega um `animate()` que conta os quadros sem
  pedir repaint, ou seja, o pet parado na tela.
- `tests/test_pending.py::LocationNotFoundTagTests` fixa a diferença entre
  os dois 404 que o servidor v2 devolve — rota ausente (corpo vazio) e
  *location* morto (`_tag: LocationNotFoundError`). Ler os dois pelo status
  desligava o "aguardando" inteiro por causa de um projeto apagado, e o
  sintoma era o balão em "Thinking" com a pergunta aberta (bug 20).
- `tests/test_sessions.py::DegradedTests` confere que a trava do stream
  guarda **de quem** é o pedido: o `session.idle` de outra aba não solta a
  espera de uma pergunta que continua aberta.
- `tests/test_sessions.py::Bug20DeadLocationTests` confere que um *location*
  morto sai da varredura (dois GETs por ciclo para sempre) e volta sozinho
  quando a lista de projetos é relida.
- `tests/test_app.py` monta o `PetApplication` com um `prefs_path`
  temporário. Sem isso a suíte lê o tema escolhido no
  `~/.config/petwatch/prefs.json` de quem a roda — e cada `save()`
  sobrescreveria esse arquivo com o estado do app de teste
  (ver bug 12).
- `tests/test_attention.py` rasteriza o widget e garante que o pulso muda
  a pintura sem mudar as medidas do balão.
- `tests/test_menu.py` percorre o menu de tamanho fechando o ciclo em todos
  os presets, a partir dos três pontos de partida.
- `tests/test_menu.py::ThumbnailTests` rasteriza o delegate da grade e conta
  os pixels pintados: é o que pega um seletor que voltou a mostrar só o nome
  (a área fica quase toda branca), um que volta a cortar a página e um que
  troca as faixas — nome em cima da figura em vez de embaixo. Os nomes
  fictícios do fixture comum não servem ali, então essa classe usa pets de
  verdade do repositório.
- `tests/test_daemon.py::DetachTests` roda o desvio num **processo
  separado** — `fork` dentro do runner trocaria os descritores da própria
  suíte. Confere que o comando volta com 0, que a saída do processo vai
  para o log em vez da tela, que o processo continua vivo depois de o pai
  sair, e que o lock de instância atravessa o fork.
- `tests/test_app.py::DetachedStartupTests` faz a janela **falhar** de
  propósito (a suíte já tem um `QApplication`, então um segundo levanta
  `RuntimeError`) e confere que o motivo chega pelo pipe do processo
  original, em uma linha só, e que o lock é solto mesmo assim.

### O que é específico de plataforma

`tests/test_platform.py` cobre o que só existe no Windows **sem precisar do
Windows**, e é a razão de o `petwatch` não ter um diretório de código por
sistema:

- Os **parsers** de porta recebem o texto da listagem e devolvem inteiros, então
  o formato do `netstat -ano`, do `ss -ltn` e do `/proc/net/tcp` é testado a
  partir de amostras, no Linux. Inclui o `/proc` em hexadecimal — `1F90` são
  8080, e ler como decimal não falharia de forma visível: as portas erradas
  só virariam candidatas a mais.
- O **protocolo do named pipe** é exercitado inteiro com `AF_UNIX` num
  diretório temporário: cliente, servidor, handshake com a chave e o caminho
  do handler que estoura. Se o protocolo quebrar, quebra no Linux.
- O **`--noconsole`** é reproduzido trocando `sys.stdout` por `None`, que é
  exatamente o que o PyInstaller faz. Daí saem tanto o `say()` que não
  estoura sem terminal quanto o `configure_logging()` que não deixa as
  mensagens caírem no `lastResort`.
- `assertLogs` com o logger `petwatch` **não** serve para testar o logging
  configurado: `tests/__init__.py` cala esse pacote inteiro para a saída da
  suíte ficar limpa. Esses testes usam um logger fora do pacote.

O que fica de fora, e por quê: `DETACHED_PROCESS`, o `msvcrt` e os caminhos
do registro do Windows só têm significado na plataforma. São conferidos no
build (`.github/workflows/windows.yml`), não na suíte.

