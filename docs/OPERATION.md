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
`XDG_STATE_HOME`, com `--log` para mudar. No Windows é
`%LOCALAPPDATA%\petwatch\pet.log`. Ele passa de 1 MiB e é virado para
`pet.log.1` — uma geração só, o suficiente para o log do processo anterior.

## Windows

No Windows o mesmo código roda das duas maneiras: instalado com `pip` e
`python -m petwatch`, ou como `petwatch.exe`, um arquivo único que não
precisa de Python, de PySide6 nem de nenhum plugin do Qt na máquina.

```powershell
python -m petwatch             # instalado, com terminal
petwatch.exe                   # o .exe, por duplo clique
petwatch.exe --status          # responde numa caixa de diálogo
petwatch.exe --stop
```

### O .exe

```powershell
py -3 -m pip install -e ".[build]"
py -3 -m PyInstaller petwatch.spec --noconfirm   # -> dist\petwatch.exe
```

O build **precisa rodar no Windows** — o PyInstaller não faz cross-compile, e
o repositório precisa estar num caminho que o Windows veja. De WSL:

```bash
powershell.exe -NoProfile -Command "py -3 -m PyInstaller petwatch.spec --noconfirm"
```

Passo a passo, verificação e erros comuns em [`BUILD.md`](BUILD.md); o CI
faz esse build em `windows-latest`.

O `petwatch.spec` monta um `onefile` **sem console**. Um processo solto é um
pet de desktop: uma janela de console preta ao lado dele seria o oposto do
que o programa promete. O que o terminal perdia — as frases do `--status` e
do `--stop`, que um duplo clique não tem onde mostrar — vira caixa de diálogo
(`petwatch/console.py`), e só nesses casos: o lançamento normal não abre
popup nenhum.

Um efeito colateral do `--noconsole` que vale mais uma linha: o PyInstaller
põe `sys.stdout` e `sys.stderr` em `None`, e um `logging.basicConfig()` sem
argumentos cria um `StreamHandler` para um `stderr` que não existe. As
mensagens caem no `lastResort`, que **as descarta sem avisar** — o sintoma
seria um pet funcionando com um log vazio e nenhum erro na tela. Por isso
`configure_logging()` monta o handler à mão e aponta para o arquivo.

### Os pets

Os 62 MB dos 1738 temas não entram no executável: dentro do `onefile` eles
seriam extraídos para `%TEMP%` a cada arranque, e "adicionar um pet" viraria
"recompilar". O `petwatch.exe` embute só o tema padrão (`eevee`, ~25 KB), e os
pets do usuário são procurados nesta ordem:

1. `$PETWATCH_PETS_DIR`
2. `pets/` ao lado do `.exe`
3. `%LOCALAPPDATA%\petwatch\pets`
4. os que vieram embutidos no executável

O diretório que **existe** vence, e o resto continua servindo de reserva para
`load_theme` — é isso que permite um `.exe` novo ter o que abrir sem inventar
um segundo caminho para a coleção.

### O que mudou dentro do app

Quatro decisões que são do Windows e não têm equivalente no POSIX.

**A porta não vem de `ss`.** Não existe `ss -ltnp` no Windows, e o
`netstat -ano` devolve PID, não nome — casar por nome exigiria um `tasklist`
por processo e continuaria frágil: o serviço pode se chamar `opencode.exe`,
`opencode-ai.exe`, `bun.exe`, estar num namespace de rede ou num container. A
descoberta agora pergunta **qual das portas em escuta é o opencode**, e a
resposta vem do próprio servidor: `200` **e** `Content-Type:
text/event-stream` em `/api/event`, com a senha. Quem não for o opencode
devolve 404, 401 ou HTML, e a sondagem é sempre em `127.0.0.1`. Isso também
simplificou o Linux, que agora lê `/proc/net/tcp` em vez de forkar um `ss` a
cada dois segundos.

**A instância única é um named pipe.** Não há `fcntl`, e `os.kill(pid,
SIGTERM)` no Windows é `TerminateProcess` — morte seca, sem handler, sem
`shutdown()`, sem gravar as preferências. O pipe resolve as duas coisas:
nome é exclusivo, então o primeiro que abre o nome é o dono da instância, e é
por ele que o `--stop` chega ao caminho limpo do menu (o mesmo `quit()` do
item **Fechar**). O `CTRL_BREAK_EVENT`, que seria o sinal de verdade, foi
descartado: exige que o filho divida o console do pai, que é justamente a
janela preta que o `--noconsole` elimina.

**Quem assume a instância é o filho, não o pai.** O `flock` atravessa o
`fork` por herança; no Windows não há herança de handle confiável entre
processos, então o processo original apenas *pergunta* se há pet rodando e
lança o filho com `--child`. A janela entre a pergunta e a resposta é de
poucos milissegundos e não tem consequência: se dois `petwatch.exe`
disparados no mesmo instante passarem os dois, o segundo filho falha ao
assumir o pipe e sai com código 1 dizendo que já existe um pet.

**A confirmação da janela vai por arquivo.** Um handle herdado pelo filho não
aparece como descritor nele — o CRT reconstrói a tabela a partir do
`STARTUPINFO`, e só dos três descritores padrão — então o filho escreveria
num número que o pai não enxerga. Um arquivo temporário com um nome só não
depende de nenhum desses detalhes, e a falha de abertura chega na tela
inteira: é o que evita o sintoma de "não aconteceu nada" com o traceback num
arquivo que ninguém sabe que existe.

Os diretórios seguem a plataforma: `%LOCALAPPDATA%\petwatch` para o estado e
o log, `%APPDATA%\petwatch\prefs.json` para as preferências. `~/.config` e
`~/.local/state` são convenções que ninguém adota no Windows.

**O "sempre no topo" funciona no Windows.** No Wayland o flag é aceito e
descartado (é o caso do WSLg, e o item do menu fica desativado com o motivo
na dica); no Windows ele vira `WS_EX_TOPMOST` e o gerenciador de janelas
honra.

## Menu (botão direito)

| Item | O que faz |
| --- | --- |
| **Tamanho** | Pequeno, Médio, Grande. A fonte do balão escala junto |
| **Selecionar pet…** | Grade de miniaturas com busca e paginação; a troca é em tempo real |
| **Sempre no topo** | Marca/desmarca; lembra entre sessões |
| **Fechar** | Sai do aplicativo |

O seletor mostra a **miniatura** de cada tema: o `preview.gif` do diretório, ou
qualquer outra imagem que o Qt consiga abrir, com o nome da pasta embaixo. O
nome sozinho não diz nada sobre o bicho, e a figura sozinha não dá para
procurar. O nome continua filtrando a busca e é o que o clique emite; na tela
ele volta sob a figura, na barra de status (sob o cursor ou na seleção) e na
dica do item — este último porque nome longo vira reticências na célula.

A lista vem dos **nomes das pastas** em `pets/`, sem ler nenhum `pet.json`:
quem decide quantos pets existem é o usuário, então a pasta é a fonte da
verdade. Com o pacote completo ([codex-pokepets](https://github.com/dnnyngyen/codex-pokepets),
mesmo layout) são 1738 pastas, 10 por página, em 5 colunas. Só a página
visível é decodificada, e cada miniatura fica em cache enquanto o diálogo
estiver aberto.

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

