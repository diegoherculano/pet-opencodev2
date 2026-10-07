# -*- mode: python ; coding: utf-8 -*-
"""Build do ``petwatch.exe``: uma pasta, sem dependência externa.

    py -3 -m pip install pyinstaller
    py -3 -m PyInstaller petwatch.spec --noconfirm

Uma pasta ``dist/petwatch`` com o ``petwatch.exe`` dentro, sem console, com
o Qt inteiro ao lado. A máquina alvo não precisa de Python, de PySide6, nem
de nenhum plugin do Qt.

**O build precisa rodar no Windows.** O PyInstaller não faz cross-compile:
o executável producedo aqui carrega o bootloader do sistema que o constrói.
De WSL dá para chamar o Python do Windows —

    powershell.exe -NoProfile -Command "py -3 -m PyInstaller petwatch.spec --noconfirm"

— mas o ``.spec`` e os pets precisam estar num caminho que o Windows veja
(``C:\\...``, não ``/home/...``).

As três decisões do arquivo
---------------------------

``--onedir``
    Uma pasta em vez de um arquivo só. Nada é extraído para ``%TEMP%`` a
    cada arranque, então some o aviso ``Failed to remove temporary
    directory`` do bootloader do ``onefile`` (que aparecia quando o
    antivírus segurava um lock nas DLLs do Qt na hora de apagar o
    ``_MEIxxxxx``). O arranque também fica instantâneo.

``--noconsole``
    O processo solto é um pet de desktop; uma janela de consola preta ao
    lado dele seria o oposto do que o programa promete. O que o terminal
    perdia — as frases do ``--status``/``--stop`` — vira caixa de diálogo
    (ver :mod:`petwatch.console`).

``--add-data pets/eevee``
    Só o tema padrão, ~25 KB. Os 1738 pets são 62 MB de dados de terceiros,
    e não entram no bundle para não transformar "adicionar um pet" em
    "recompilar". O app acha os pets do usuário em
    ``%LOCALAPPDATA%\\petwatch\\pets``, e este aqui é só o que garante um
    build novo ter o que abrir.
"""

from pathlib import Path

# ``SPECPATH`` é a pasta do .spec, dada pelo PyInstaller. O ROOT é sempre
# a raiz do repositório — e o caminho tem de ser o que o Windows entende,
# senão os ``--add-data`` não encontram nada.
ROOT = Path(SPECPATH).resolve()

#: Tema embutido: o mínimo para um ``.exe`` novo ter o que abrir.
BUNDLED_PETS = ROOT / "pets" / "eevee"

#: O ícone do executável. Gerado de ``build/petwatch.ico`` quando existe,
#: e omitido quando não — um build sem ícone funciona, e um build que
#: falha por causa de um ícone seria pior.
ICON = ROOT / "build" / "petwatch.ico"

#: Só entra o que existe. Um build sem a pasta ``pets/`` (que é
#: ``.gitignore`` e não vem no repositório) gera um ``.exe`` sem tema
#: padrão: funciona, com a pasta de pets do usuário, e o aviso fica em
#: vez de o build quebrar.
datas = [(str(BUNDLED_PETS), "pets/eevee")] if BUNDLED_PETS.is_dir() else []

block_cipher = None

a = Analysis(
    [str(ROOT / "pet.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # Nenhum dado além dos pets: o Qt vem inteiro dentro do executável,
    # pelos hooks do PySide6.
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Módulos do PySide6 que o petwatch não toca. Tirados um a um
        # porque o ganho é grande e o lista-los é mais barato do que
        # descobrir o que quebrou depois.
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtUiTools",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtSvgWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtStateMachine",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
        "PySide6.QtXml",
        "PySide6.QtConcurrent",
        # Tkinter e o matplotlib do ambiente de build não fazem parte de
        # um pet.
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
        "PySide6.QtOpenGLFunctions",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="petwatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # O console some: o app abre uma caixa de diálogo quando precisa
    # responder algo (ver petwatch/console.py).
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

# ``onedir``: o .exe fica em dist/petwatch/ com as DLLs ao lado, sem
# extração para %TEMP% — é o que elimina o "Failed to remove temporary
# directory" do onefile.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="petwatch",
)
