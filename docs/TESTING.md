# Testes

Como rodar e o que a suíte cobre. Visão geral em [README](../README.md).


```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
```

478 testes cobrindo o mapeamento de eventos, o parser SSE, a leitura do
`pet.json`, a escolha do asset, o transporte HTTP, as consultas de
pendência e de sessão, o quadro de instâncias, a pilha de balões
(empilhamento ancorado embaixo, janela fixa, reticências, cor de aviso), a
prova de que o pet não se move ao entrar ou sair um balão, a geometria da
pintura, o repaint da animação, o isolamento das preferências, o
encerramento por sinal e a saída do terminal.

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
- `tests/test_app.py` monta o `PetApplication` com um `prefs_path`
  temporário. Sem isso a suíte lê o tema escolhido no
  `~/.config/petwatch/prefs.json` de quem a roda — e cada `save()`
  sobrescreveria esse arquivo com o estado do app de teste
  (ver bug 12).
- `tests/test_attention.py` rasteriza o widget e garante que o pulso muda
  a pintura sem mudar as medidas do balão.
- `tests/test_menu.py` percorre o menu de tamanho fechando o ciclo em todos
  os presets, a partir dos três pontos de partida.
- `tests/test_daemon.py::DetachTests` roda o desvio num **processo
  separado** — `fork` dentro do runner trocaria os descritores da própria
  suíte. Confere que o comando volta com 0, que a saída do processo vai
  para o log em vez da tela, que o processo continua vivo depois de o pai
  sair, e que o lock de instância atravessa o fork.
- `tests/test_app.py::DetachedStartupTests` faz a janela **falhar** de
  propósito (a suíte já tem um `QApplication`, então um segundo levanta
  `RuntimeError`) e confere que o motivo chega pelo pipe do processo
  original, em uma linha só, e que o lock é solto mesmo assim.

