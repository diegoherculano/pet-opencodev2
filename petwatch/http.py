"""Transporte HTTP do stream de eventos.

Usa ``http.client`` em vez de ``urllib.request`` por um motivo prático:
``HTTPConnection.sock`` é público, e desligar o socket é o que destrava um
``readline()`` já preso em outra thread. Sem isso a thread do monitor fica
bloqueada até o timeout do socket e o encerramento derruba o processo com
SIGABRT.

Fechar a resposta (``response.close()``) **não** resolve: ele espera o
timeout e só então falha. Medido nesta máquina:

    sock.shutdown(SHUT_RDWR) -> leitura liberada em 0.00s
    response.close()         -> leitura liberada em 8.48s (e com erro)
"""

from __future__ import annotations

import http.client
import logging
import socket

log = logging.getLogger(__name__)


class HttpError(RuntimeError):
    """Falha ao abrir a conexão."""


class Connection:
    """Uma requisição GET com leitura incremental do corpo.

    Pode ser usada como gerenciador de contexto::

        with Connection(...) as conn:
            if conn.status != 200:
                ...
            line = conn.readline()
    """

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        headers: dict[str, str],
        *,
        timeout: float,
    ) -> None:
        self._conn = http.client.HTTPConnection(host, port, timeout=timeout)

        self._path = path
        self._headers = headers

        self._response: http.client.HTTPResponse | None = None
        self._closed = False

    # ------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------

    def open(self) -> Connection:
        """Envia a requisição e prepara a leitura do corpo."""

        try:
            self._conn.request("GET", self._path, headers=self._headers)
            self._response = self._conn.getresponse()

        except OSError as exc:
            self.close()

            raise HttpError(f"{self._path}: {exc}") from exc

        log.debug("[pet] %s respondeu %s", self._path, self._response.status)

        return self

    def _require_open(self) -> http.client.HTTPResponse:
        """Devolve a resposta ou explica por que ela não está disponível.

        Ler depois de ``close()`` levantaria um ``AttributeError`` confuso
        do ``http.client``, então a checagem é explícita.
        """

        if self._closed:
            raise HttpError("conexão fechada")

        if self._response is None:
            raise HttpError("conexão não aberta")

        return self._response

    @property
    def status(self) -> int:
        return self._require_open().status

    @property
    def headers(self):
        return self._require_open().headers

    def readline(self) -> bytes:
        """Próxima linha do corpo; ``b""`` no fim do stream."""

        return self._require_open().readline()

    # ------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------

    def interrupt(self) -> None:
        """Desliga o socket para destravar uma leitura em outra thread.

        Seguro de chamar a qualquer momento, inclusive antes da conexão
        estar pronta ou depois de já ter sido fechada.
        """

        sock = self._conn.sock

        if sock is None:
            return

        try:
            sock.shutdown(socket.SHUT_RDWR)

        except OSError:
            # Já desligado ou fechado: nada a fazer.
            pass

    def close(self) -> None:
        """Fecha a resposta e a conexão. Idempotente."""

        if self._closed:
            return

        self._closed = True

        for close in (
            getattr(self._response, "close", None),
            self._conn.close,
        ):
            if close is None:
                continue

            try:
                close()

            except Exception as exc:
                log.debug("[pet] erro fechando conexão: %s", exc)

    # ------------------------------------------------------------
    # Gerenciador de contexto
    # ------------------------------------------------------------

    def __enter__(self) -> Connection:
        return self.open()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
