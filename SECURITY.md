# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | :white_check_mark: |
| < 2.0   | :x:                |

## Reporting a Vulnerability

Não abra issue pública para vulnerabilidades. Entre em contato pelo e-mail
do mantenedor (ver `git log` para o endereço atual) com:

- descrição do problema e impacto estimado;
- passos para reproduzir (comandos, versões do Python/Qt/SO);
- se possível, um PoC mínimo.

Compromisso de resposta inicial em até 7 dias. Correção e divulgação são
coordenadas com quem reportou; o crédito é dado no `CHANGELOG.md`, salvo
pedido em contrário.

## Escopo

O pet **só lê** o servidor local (`GET`s em `/api/event`, `/api/project`,
`/api/form`, `/api/permission/request`, `/api/session/*`) e nunca envia
dados para fora da máquina. Um report válido mostra exfiltração, escrita
inesperada, execução remota ou escalada de privilégio a partir do pet.
