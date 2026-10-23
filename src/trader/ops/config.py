"""Configuração do coletor (RF-184). Valores padrão vêm do SRS §11.1 e do doc 06."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

STREAM_SUFFIXES = ("trade", "kline_1m", "depth20@100ms", "depth@100ms")
"""Streams coletados ao vivo (doc 06 §3.3)."""


class ConfigError(ValueError):
    """Configuração inválida: o coletor não inicia (RF-184)."""


@dataclass(frozen=True)
class CollectorConfig:
    symbol: str = "BTCUSDT"
    data_dir: Path = Path("data")
    ws_base_urls: tuple[str, ...] = (
        "wss://data-stream.binance.vision",
        "wss://stream.binance.com:9443",
    )
    rest_base_url: str = "https://data-api.binance.vision"
    open_timeout_s: float = 10.0
    silence_limits_s: Mapping[str, float] = field(
        default_factory=lambda: {
            "trade": 30.0,
            "kline_1m": 10.0,
            "depth20@100ms": 2.0,
            "depth@100ms": 2.0,
        }
    )
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 60.0
    rotation_after_s: float = 23 * 3600.0
    handover_timeout_s: float = 60.0
    handover_overlap_s: float = 2.0
    snapshot_interval_s: float = 900.0
    snapshot_limit: int = 5000
    snapshot_min_interval_s: float = 10.0
    clock_probe_interval_s: float = 300.0
    clock_offset_alert_ms: float = 250.0
    flush_interval_s: float = 1.0
    health_interval_s: float = 5.0
    metrics_interval_s: float = 60.0
    rest_weight_budget_per_min: int = 1200
    statsd_host: str | None = None
    statsd_port: int = 8125
    instance: str = "main"
    """Nome da instância (`a` ou `b` na troca sem lacuna); separa o arquivo de saúde."""

    @property
    def streams(self) -> tuple[str, ...]:
        prefix = self.symbol.lower()
        return tuple(f"{prefix}@{suffix}" for suffix in STREAM_SUFFIXES)

    def silence_limit_s(self, stream: str) -> float:
        return self.silence_limits_s[stream.partition("@")[2]]

    def validate(self) -> "CollectorConfig":
        if not self.symbol.isalnum() or not self.symbol.isupper():
            raise ConfigError(f"symbol deve ser alfanumérico em maiúsculas: {self.symbol!r}")
        if not self.ws_base_urls or any(
            not url.startswith(("wss://", "ws://")) for url in self.ws_base_urls
        ):
            raise ConfigError("ws_base_urls precisa de ao menos uma URL ws:// ou wss://")
        if not self.rest_base_url.startswith(("https://", "http://")):
            raise ConfigError("rest_base_url precisa ser http(s)")
        if set(self.silence_limits_s) != set(STREAM_SUFFIXES):
            raise ConfigError(f"silence_limits_s precisa ter exatamente {STREAM_SUFFIXES}")
        positive = {f.name: getattr(self, f.name) for f in fields(self) if f.name.endswith("_s")}
        positive |= {f"silence_limits_s.{k}": v for k, v in self.silence_limits_s.items()}
        for name, value in positive.items():
            if isinstance(value, (int, float)) and value <= 0:
                raise ConfigError(f"{name} deve ser positivo")
        if not 0 < self.rest_weight_budget_per_min <= 6000:
            raise ConfigError("rest_weight_budget_per_min deve estar entre 1 e 6000 (MD-08)")
        if not 1 <= self.snapshot_limit <= 5000:
            raise ConfigError("snapshot_limit deve estar entre 1 e 5000")
        if self.backoff_initial_s > self.backoff_max_s:
            raise ConfigError("backoff_initial_s maior que backoff_max_s")
        if not self.instance.replace("-", "").isalnum():
            raise ConfigError(
                f"instance deve ser alfanumérico (hífen permitido): {self.instance!r}"
            )
        return self


@dataclass(frozen=True)
class PipelineConfig:
    """Pipeline horário (doc 03 §6.3). `symbol` e `data_dir` vêm de `[collector]` se omitidos."""

    symbol: str = "BTCUSDT"
    data_dir: Path = Path("data")
    compression_level: int = 9
    orphan_grace_s: float = 300.0
    boundary_margin_s: float = 300.0
    late_limit_s: float = 5.0
    clock_offset_alert_ms: float = 250.0
    clock_reference_window_s: float = 600.0
    local_retention_days: float = 7.0
    replica_dir: Path | None = None
    """Réplica em diretório local (desenvolvimento)."""
    replica_s3_bucket: str | None = None
    """Réplica no S3 (produção, RF-136). Tem prioridade sobre `replica_dir`."""
    aws_region: str | None = None
    statsd_host: str | None = None
    statsd_port: int = 8125
    trades_row_group: int = 50_000
    books_row_group: int = 2_000

    @property
    def streams(self) -> tuple[str, ...]:
        prefix = self.symbol.lower()
        return tuple(f"{prefix}@{suffix}" for suffix in STREAM_SUFFIXES)

    def validate(self) -> "PipelineConfig":
        if not self.symbol.isalnum() or not self.symbol.isupper():
            raise ConfigError(f"symbol deve ser alfanumérico em maiúsculas: {self.symbol!r}")
        if not 1 <= self.compression_level <= 22:
            raise ConfigError("compression_level deve estar entre 1 e 22")
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name.endswith(("_s", "_ms", "_days", "_row_group")) and value <= 0:
                raise ConfigError(f"{f.name} deve ser positivo")
        return self


def _read_tables(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"não foi possível ler {path}: {exc}") from exc


def _check_keys(cls: type, values: Mapping[str, Any]) -> None:
    unknown = set(values) - {f.name for f in fields(cls)}
    if unknown:
        raise ConfigError(f"chaves desconhecidas: {sorted(unknown)}")


def load_pipeline_config(path: Path | None = None, **overrides: Any) -> PipelineConfig:
    tables = _read_tables(path)
    collector = tables.get("collector", {})
    shared = ("symbol", "data_dir", "statsd_host", "statsd_port")
    values: dict[str, Any] = {k: collector[k] for k in shared if k in collector}
    values.update(tables.get("pipeline", {}))
    values.update({k: v for k, v in overrides.items() if v is not None})
    _check_keys(PipelineConfig, values)
    for key in ("data_dir", "replica_dir"):
        if values.get(key) is not None:
            values[key] = Path(values[key])
    try:
        config = replace(PipelineConfig(), **values)
    except TypeError as exc:
        raise ConfigError(str(exc)) from exc
    return config.validate()


def load_config(path: Path | None = None, **overrides: Any) -> CollectorConfig:
    """Lê a tabela `[collector]` de um TOML (opcional) e aplica `overrides` não nulos."""
    values: dict[str, Any] = dict(_read_tables(path).get("collector", {}))
    values.update({k: v for k, v in overrides.items() if v is not None})
    _check_keys(CollectorConfig, values)
    if "data_dir" in values:
        values["data_dir"] = Path(values["data_dir"])
    if "ws_base_urls" in values:
        values["ws_base_urls"] = tuple(values["ws_base_urls"])
    if "silence_limits_s" in values:
        values["silence_limits_s"] = {
            **CollectorConfig().silence_limits_s,
            **values["silence_limits_s"],
        }
    try:
        config = replace(CollectorConfig(), **values)
    except TypeError as exc:
        raise ConfigError(str(exc)) from exc
    return config.validate()
