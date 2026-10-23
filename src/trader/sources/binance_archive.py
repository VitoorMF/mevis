"""Arquivos públicos de spot da Binance em `data.binance.vision` (doc 06 §7; SRS FV-12).

Cada arquivo ZIP tem um `.CHECKSUM` com o SHA-256. Os diários saem no dia seguinte; os mensais,
depois que o mês fecha. Não há limite de peso, mas os downloads são feitos um de cada vez e as
esperas de HTTP 429 são respeitadas (termos do Binance Vision §7.1).
"""

import hashlib
import logging
import time
from calendar import monthrange
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from trader.ops.logs import ctx

log = logging.getLogger(__name__)

ARCHIVE_BASE_URL = "https://data.binance.vision"
DATASETS = ("trades", "klines_1m")
_CHUNK = 1 << 20


class ArchiveNotFound(Exception):
    """HTTP 404: arquivo ainda não publicado (ou período sem dados)."""


class ArchiveError(Exception):
    """Falha que persistiu depois das novas tentativas."""


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    dataset: str
    symbol: str
    monthly: bool
    first_day: date
    last_day: date

    @property
    def name(self) -> str:
        period = f"{self.first_day:%Y-%m}" if self.monthly else f"{self.first_day:%Y-%m-%d}"
        kind = "1m" if self.dataset == "klines_1m" else self.dataset
        return f"{self.symbol}-{kind}-{period}.zip"

    @property
    def url_path(self) -> str:
        frequency = "monthly" if self.monthly else "daily"
        if self.dataset == "klines_1m":
            folder = f"klines/{self.symbol}/1m"
        else:
            folder = f"{self.dataset}/{self.symbol}"
        return f"/data/spot/{frequency}/{folder}/{self.name}"

    def days(self) -> list[date]:
        count = (self.last_day - self.first_day).days + 1
        return [self.first_day + timedelta(days=i) for i in range(count)]


def plan_files(dataset: str, symbol: str, start: date, end: date) -> list[ArchiveFile]:
    """Arquivos que cobrem `[start, end]`. Negócios: diários (um mês inteiro passa de 1 GB).
    Candles: mensais para meses completos no período; diários para o restante."""
    if dataset not in DATASETS:
        raise ValueError(f"dataset desconhecido: {dataset}")
    if start > end:
        raise ValueError(f"início {start} depois do fim {end}")
    files = []
    day = start
    while day <= end:
        month_end = day.replace(day=monthrange(day.year, day.month)[1])
        if dataset == "klines_1m" and day.day == 1 and month_end <= end:
            files.append(ArchiveFile(dataset, symbol, True, day, month_end))
            day = month_end + timedelta(days=1)
        else:
            files.append(ArchiveFile(dataset, symbol, False, day, day))
            day += timedelta(days=1)
    return files


class ArchiveClient:
    def __init__(
        self,
        client: httpx.Client,
        base_url: str = ARCHIVE_BASE_URL,
        *,
        attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._sleep = sleep

    def checksum(self, archive: ArchiveFile) -> str:
        """SHA-256 publicado no `.CHECKSUM` (formato `<hex>  <nome>`)."""
        url = f"{self._base_url}{archive.url_path}.CHECKSUM"
        with self._get(url) as response:
            text = response.read().decode("ascii", errors="replace")
        digest = text.split()[0].lower() if text.split() else ""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ArchiveError(f"CHECKSUM ilegível para {archive.name}: {text[:80]!r}")
        return digest

    def download(self, archive: ArchiveFile, target: Path) -> str:
        """Grava o ZIP em `target` e devolve o SHA-256 dos bytes recebidos."""
        url = f"{self._base_url}{archive.url_path}"
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self._attempts + 1):
            digest = hashlib.sha256()
            try:
                with self._get(url) as response, target.open("wb") as out:
                    for chunk in response.iter_bytes(_CHUNK):
                        digest.update(chunk)
                        out.write(chunk)
                return digest.hexdigest()
            except httpx.TransportError as exc:
                # Conexão caiu no meio do corpo: recomeça o arquivo inteiro.
                if attempt == self._attempts:
                    raise ArchiveError(f"download de {archive.name} falhou: {exc!r}") from exc
                self._wait(attempt, None, archive.name, repr(exc))
        raise AssertionError("inalcançável")

    @contextmanager
    def _get(self, url: str) -> Iterator[httpx.Response]:
        """Resposta 200 aberta em modo de fluxo, fechada ao sair do `with`."""
        response = self._open(url)
        try:
            yield response
        finally:
            response.close()

    def _open(self, url: str) -> httpx.Response:
        for attempt in range(1, self._attempts + 1):
            try:
                response = self._client.send(self._client.build_request("GET", url), stream=True)
            except httpx.TransportError as exc:
                if attempt == self._attempts:
                    raise ArchiveError(f"GET {url} falhou: {exc!r}") from exc
                self._wait(attempt, None, url, repr(exc))
                continue
            if response.status_code == 200:
                return response
            response.close()
            if response.status_code == 404:
                raise ArchiveNotFound(url)
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self._attempts:
                raise ArchiveError(f"GET {url}: HTTP {response.status_code}")
            self._wait(attempt, response.headers.get("Retry-After"), url, response.status_code)
        raise AssertionError("inalcançável")

    def _wait(self, attempt: int, retry_after: str | None, what: str, cause: object) -> None:
        seconds = min(2.0**attempt, 60.0)
        if retry_after is not None and retry_after.isdigit():
            seconds = max(seconds, float(retry_after))
        log.warning("nova tentativa", extra=ctx(target=what, cause=str(cause), wait_s=seconds))
        self._sleep(seconds)
