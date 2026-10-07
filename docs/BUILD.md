# Build

Como gerar o `petwatch.exe`. Visão geral em [README](../README.md), operação
em [OPERATION.md](OPERATION.md).

## O executável

`dist\petwatch\petwatch.exe` — uma pasta, sem console, da ordem de 100 MB (o número
exato sai no log do build). A máquina alvo não precisa de Python, de PySide6
nem de plugin do Qt.

**O build precisa rodar no Windows.** O PyInstaller não faz cross-compile: o
bootloader do executável é o da plataforma que o construiu. Um build em Linux
não gera um `.exe`, e não há erro que denuncie isso — só um artefato que não
abre.

## Gerar

```powershell
py -3 -m pip install -e ".[build]"
py -3 -m PyInstaller petwatch.spec --noconfirm
```

Leva alguns minutos. `--noconfirm` sobrescreve um build anterior sem perguntar;
`--clean` limpa o cache de análise (use quando trocar imports ou plugins).

### De WSL

Chame o Python do Windows a partir do WSL:

```bash
powershell.exe -NoProfile -Command "py -3 -m PyInstaller petwatch.spec --noconfirm"
```

O repositório precisa estar num caminho que o Windows veja (`C:\...`, e não
`/home/...`) — um `\\wsl$\...` funciona em alguns casos e é lento nos outros;
nesse caso, copie ou cloneie para `C:\` antes. Alternativa: o CI gera o
artefato em `windows-latest` (`.github/workflows/windows.yml`), e o download
fica em **Actions → petwatch-windows**.

## O que entra

| Parte | Vai embutida |
| --- | --- |
| Código e Qt | Sim, via os hooks do PySide6 |
| `pets/eevee` | Sim (~25 KB) — é o tema padrão |
| Os outros 1738 pets | **Não.** Ficam em `%LOCALAPPDATA%\petwatch\pets` |

Os 62 MB da coleção completa não entram: "adicionar um pet" não pode virar
"recompilar".
Quem quiser a coleção inteira aponta `$PETWATCH_PETS_DIR`, deixa a pasta
`pets/` ao lado do `.exe`, ou copia para `%LOCALAPPDATA%\petwatch\pets`.

## Verificar

```powershell
dist\petwatch\petwatch.exe --status   # exit 1 + "não está rodando" = funciona
dist\petwatch\petwatch.exe            # o pet abre na tela
dist\petwatch\petwatch.exe --stop     # encerra
```

O `--status` é a prova de que não falta DLL: um executável com dependência
ausente falha antes de imprimir qualquer coisa.

Antes de olhar a tela, confira se o sprite **anima** e se o log diz qual
arquivo entrou:

```
%LOCALAPPDATA%\petwatch\pet.log
```

```
[pet] Eevee usando spritesheet.webp (anima 56..61 de 72)   # anima
[pet] Eevee usando preview.gif (anima 0..0 de 1)            # estático
```

## Quando dá errado

| Sintoma | Causa |
| --- | --- |
| Pisca uma janela de console | O build passou pelo `pet.py` em vez do `.spec`. O `console=False` está no spec. |
| O pet abre parado | `qwebp.dll` não entrou no bundle; o log mostra `preview.gif`. |
| "Windows proteveu o seu PC" | Executável sem assinatura. É o esperado de todo build local do PyInstaller; publicar exige certificado. |
| "Failed to remove temporary directory" | Era do build antigo em `onefile`. No `onedir` atual não há extração em `%TEMP%`; se aparecer, é resto de versão antiga — encerre o pet e apague `%TEMP%\_MEI*`. |
| O `.exe` não roda no Linux/WSL | Não roda por definição — é PE para Windows. Use `python -m petwatch`. |
| `FileNotFoundError` no primeiro uso | `%LOCALAPPDATA%\petwatch` não existia numa build antiga. Já é corrigido (ver `CHANGELOG.md`); o sintoma era "já existe um pet rodando (pid None)". |

## Ícone

Opcional: coloque um `.ico` multi-tamanho em `build/petwatch.ico` e o spec o
pega. Sem o arquivo, o build sai com o ícone padrão do PyInstaller — e sem
falhar, que é o ponto.
