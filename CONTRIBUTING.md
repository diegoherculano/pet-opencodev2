# Contributing

Obrigado por querer contribuir! Este guia resume o essencial. O resto vive
no `README.md` (arquitetura, eventos, balões) — leia as seções relevantes
antes de mexer no comportamento do pet.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
sudo apt install qt6-image-formats-plugins  # WebP no Linux (ver README)
```

Requer Python >= 3.11 e um servidor [opencode](https://opencode.ai) local
para o modo real. Sem servidor, a suíte cobre tudo com
`QT_QPA_PLATFORM=offscreen`.

## Testes

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
```

- Os 478 testes precisam passar antes de abrir o PR.
- Teste novo acompanha comportamento novo — ver `tests/test_sessions.py`
  e `tests/test_pending.py` como modelo (contrato primeiro, mocks finos).
- Teste de UI não pode depender de `~/.config`: use `prefs_path` temporário
  (ver `tests/test_app.py::PrefsIsolationTests`).

## Lint

```bash
ruff check .
```

O projeto usa `ruff` com `line-length = 88` (ver `pyproject.toml`).

## Pull requests

1. Abra uma issue primeiro se a mudança for grande ou controversa.
2. Um PR, um assunto. Mantenha o diff mínimo.
3. Descreva o sintoma, a causa e como verificou (comando + saída).
4. Atualize o `README.md` se o comportamento visível mudar e adicione uma
   entrada em `CHANGELOG.md` sob `Unreleased`.
5. Não commite o pacote completo de pets (só `pets/eevee` é versionado), logs,
   nem `prefs.json` real.

## Commits

Mensagens curtas no imperativo (`"Corrige X"`, `"Adiciona Y"`), em PT ou EN,
consistentes com o histórico (`git log --oneline`).
