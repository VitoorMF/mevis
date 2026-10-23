"""Conexão WebSocket com os streams combinados da Binance (doc 06 §3.3)."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from websockets.asyncio.client import ClientConnection, connect


class WsConnection(Protocol):
    async def recv(self) -> bytes: ...

    async def close(self) -> None: ...


Connector = Callable[[str], Awaitable[WsConnection]]


def stream_url(base: str, streams: Sequence[str]) -> str:
    """URL de streams combinados com marcas de tempo em microssegundos (doc 06 §4.1)."""
    return f"{base.rstrip('/')}/stream?streams={'/'.join(streams)}&timeUnit=MICROSECOND"


class _WebsocketsConnection:
    def __init__(self, conn: ClientConnection) -> None:
        self._conn = conn

    async def recv(self) -> bytes:
        # decode=False devolve os bytes recebidos, sem decodificar e recodificar (RF-131).
        data = await self._conn.recv(decode=False)
        return data.encode() if isinstance(data, str) else data

    async def close(self) -> None:
        await self._conn.close()


async def websockets_connector(url: str) -> WsConnection:
    """O servidor envia ping a cada 20 s e a biblioteca responde sozinha (RF-114)."""
    conn = await connect(url, open_timeout=None, max_size=2**20, max_queue=4096)
    return _WebsocketsConnection(conn)
