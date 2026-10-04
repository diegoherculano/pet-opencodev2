# Operação

Segundo plano, menu, preferências e log. Visão geral em [README](../README.md).


```bash
python pet.py              # segundo plano; prompt na hora, exit 0
python pet.py -b           # idem, explícito (útil em scripts/autostart)
python pet.py --status     # diz se há pet rodando e qual o pid (exit 1 se não)
python pet.py --stop       # encerra o que estiver rodando
python pet.py --foreground # colado no terminal, log e traceback na tela
python pet.py --help
```

O modo normal é segundo plano porque o pet é um serviço de janela, não um
programa de terminal. Como implementar isso **dentro** do app — e não com
`nohup`/`setsid` na linha de comando — dá três coisas que um wrapper não
daria: o processo solto sabe que já existe outro (e diz qual é o pid), o
`--stop` sabe para onde mandar o sinal, e a falha de abertura da janela
chega no terminal em vez de sumir num log.

**O que muda para quem usa.** O prompt volta em milissegundos e o `Ctrl+C`
deixa de valer — o processo não está mais no grupo de jobs do shell, e é
por isso que ele não morreria junto com o shell. Quem encerra é o item
**Fechar** do menu ou o `--stop`. Tudo que ia aparecer na tela vai para
`pet.log`, incluindo o que não é Python: os avisos do Qt e do loader de
plugins escrevem no descritor 2, que agora é o log.

**Como é feito.** Dois `fork` e um `setsid` (um fork só deixaria o processo
como líder de sessão do terminal, que continuaria mandando `Ctrl+C` para
ele), `stdin` no `/dev/null`, e `stdout`/`stderr` apontados para o log.
O desvio vem **antes** do `QApplication`: o Qt precisa abrir a conexão com
o servidor de display depois do `setsid`, e um `QApplication` construído
antes do fork não pode ser usado nos dois processos.

O processo original não some às cegas: ele fica esperando o filho dizer
que a janela abriu, por um pipe, e só então devolve o terminal. Se a janela
não abrir, o motivo chega na tela — do contrário o sintoma de "não aconteceu
nada" seria o silêncio, e o traceback estaria num arquivo que ninguém foi
avisado de que existe.

**Só uma instância.** O processo solto toma um `flock` em `pet.pid`, e o
segundo `python pet.py` percebe o lock ocupado e sai. O lock pertence ao
descritor, não ao processo: um `SIGKILL` faz o kernel soltá-lo, e o
`pet.pid` que sobra vira só um número velho em vez de um bloqueio eterno —
por isso a presença do pet é decidida pelo lock, nunca pelo conteúdo do
arquivo. Verificado com um `kill -9` no meio:

```
$ kill -9 $(cat ~/.local/state/petwatch/pet.pid)
$ python pet.py --status
[pet] não está rodando. Log: /home/…/petwatch/pet.log   # exit 1
$ python pet.py
[pet] rodando em segundo plano (pid 339527). Log: …     # exit 0
```

**Onde o log mora.** `~/.local/state/petwatch/pet.log`, respeitando
`XDG_STATE_HOME`, com `--log` para mudar. Ele passa de 1 MiB e é virado para
`pet.log.1` — uma geração só, o suficiente para o log do processo anterior.

## Menu (botão direito)

| Item | O que faz |
| --- | --- |
| **Tamanho** | Pequeno, Médio, Grande. A fonte do balão escala junto |
| **Selecionar pet…** | Janela com busca e paginação; a troca é em tempo real |
| **Sempre no topo** | Marca/desmarca; lembra entre sessões |
| **Fechar** | Sai do aplicativo |

O seletor lista os **nomes das pastas** em `pets/`, sem ler nenhum
`pet.json`: quem decide quantos pets existem é o usuário, então a pasta é a
fonte da verdade. São 1738 pastas, 10 por página, 174 páginas.

A troca só acontece num clique explícito. Digitar na busca, mudar de página
e filtrar não trocam o pet — do contrário, digitar "pi" já trocaria o pet
para o primeiro resultado.

**"Sempre no topo" não funciona no WSLg.** O Wayland não tem z-order:
`xdg-shell` não tem um pedido de "manter acima" e o plugin do Qt ignora
`Qt.WindowStaysOnTopHint`, então o flag é aceito e descartado. No X11 ele
vira `_NET_WM_STATE_ABOVE` e passa a depender do gerenciador de janelas — o
do WSLg não anuncia o átomo em `_NET_SUPPORTED` e o ignora, mas um
i3/mutter/kwin normal honra. Por isso o item do menu fica visível e
desativado nesses casos, com o motivo na dica, e o app avisa no log em vez
de aceitar a opção em silêncio. Não há como contornar: as janelas do WSLg são
sintetizadas como janelas filhas do Windows, e a ordem na área de trabalho é
decidida pelo compositor do Windows.

Preferências em `~/.config/petwatch/prefs.json` (tema, tamanho, topo).
Gravação atômica e tolerante a arquivo corrompido: o app sempre abre.

`Ctrl+C` e `kill` passam pelo mesmo caminho limpo do botão direito: um
handler de sinal chama `quit()`, que dispara `aboutToQuit` e o
`shutdown()`. Sem esse handler o `SIGINT` levanta `KeyboardInterrupt`
dentro do slot que o Qt está executando; o PySide6 imprime o traceback e o
event loop continua, então o processo não sai. O `SIGTERM` é o que o
`--stop` usa; o `Ctrl+C` só chega ao app em `--foreground`.

