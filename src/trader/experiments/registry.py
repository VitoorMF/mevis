"""Registro de experimentos em SQLite, com cópia só de acréscimo no S3 (doc 04 §11; doc 03 §5.3).

Toda execução de estratégia (modelo ou baseline) é gravada como "em andamento" antes de ler o
dataset (RF-271) e termina como "ok" ou "erro", com as tentativas avaliadas e as métricas
(RF-272). Nada é apagado: descartar é uma marca com motivo (RF-273). Cada passo grava também um
JSON em `derived/experiments/` (`<id>.start.json`, `<id>.result.json`, `<id>.discard.json`), de
onde o SQLite pode ser refeito (RF-275).

Uma execução de modelo guarda também o resultado do procedimento (`outcome`: escolhas por dobra,
abstenções, métricas do teste) e arquivos auxiliares, como as previsões, em
`derived/experiments/<id>/`, com o SHA-256 no registro de fim (RF-245).

O número de tentativas de uma trilha, usado no IS-15, é o de identificações de configuração
distintas, com métricas, nas execuções de modelo da trilha; repetições, execuções com erro sem
métricas e baselines não somam (RF-274; DEC-38).
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.storage.objectstore import ObjectStore
from trader.storage.rawfiles import file_sha256

EXPERIMENTS = "derived/experiments"
KINDS = ("baseline", "model")
CONFIG_KEYS = (
    "rung",
    "horizon",
    "model",
    "hyperparameters",
    "threshold",
    "seed",
    "catalog_id",
    "label",
)
"""O que identifica uma tentativa: sem versão do código, período nem dataset (RF-274)."""
SPEC_KEYS = ("rungs", "horizons", "model", "grid", "seed", "windows")
"""O que toda execução registra antes de rodar, além da trilha e do dataset (RF-271)."""
_STAGES = {"start": 0, "result": 1, "discard": 2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('baseline', 'model')),
    track TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    final_evaluation INTEGER NOT NULL,
    code_version TEXT NOT NULL,
    who TEXT NOT NULL,
    started_at TEXT NOT NULL,
    spec TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
    finished_at TEXT,
    error TEXT,
    discarded_at TEXT,
    discard_reason TEXT
);
CREATE TABLE IF NOT EXISTS trials (
    execution_id TEXT NOT NULL REFERENCES executions (id),
    config_id TEXT NOT NULL,
    config TEXT NOT NULL,
    metrics TEXT NOT NULL,
    PRIMARY KEY (execution_id, config_id)
);
CREATE TABLE IF NOT EXISTS outcomes (
    execution_id TEXT PRIMARY KEY REFERENCES executions (id),
    outcome TEXT NOT NULL,
    artifacts TEXT NOT NULL
);
"""


def config_id(config: Mapping[str, Any]) -> str:
    """Resumo SHA-256 (16 primeiros dígitos) da configuração de uma tentativa (RF-274)."""
    if set(config) != set(CONFIG_KEYS):
        raise ValueError(f"configuração precisa das chaves {', '.join(CONFIG_KEYS)}")
    canonical = json.dumps(dict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Trial:
    config: Mapping[str, Any]
    metrics: Mapping[str, Any]
    """Por janela e cenário (RF-272)."""

    @property
    def config_id(self) -> str:
        return config_id(self.config)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "config": dict(self.config),
            "metrics": dict(self.metrics),
        }


class Registry:
    def __init__(self, path: Path, store: ObjectStore) -> None:
        self.path = path
        self.store = store
        self.records = path.parent / "records"
        self.artifacts = path.parent / "artifacts"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ escrita

    def start(
        self,
        *,
        kind: str,
        track: str,
        dataset_id: str,
        final_evaluation: bool,
        code_version: str,
        who: str,
        spec: Mapping[str, Any],
        now: datetime,
    ) -> str:
        """Registra a execução antes de ler o dataset (RF-271) e devolve o id."""
        if kind not in KINDS:
            raise ValueError(f"tipo de execução desconhecido: {kind}")
        missing = [key for key in SPEC_KEYS if key not in spec]
        if missing:
            raise ValueError(f"execução sem {', '.join(missing)} (RF-271)")
        execution_id = "exp-" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        record = {
            "stage": "start",
            "id": execution_id,
            "kind": kind,
            "track": track,
            "dataset_id": dataset_id,
            "final_evaluation": final_evaluation,
            "code_version": code_version,
            "who": who,
            "started_at": now.astimezone(UTC).isoformat(),
            "spec": dict(spec),
        }
        with self._db:
            self._apply(record)
        self._publish(f"{execution_id}.start.json", record)
        return execution_id

    def finish(
        self,
        execution_id: str,
        *,
        trials: Sequence[Trial],
        now: datetime,
        error: str | None = None,
        outcome: Mapping[str, Any] | None = None,
        artifacts: Mapping[str, str] | None = None,
    ) -> None:
        """Fecha a execução com as tentativas avaliadas, mesmo quando termina com erro (RF-272).

        `artifacts` leva o nome e o SHA-256 de cada arquivo já enviado com `put_artifact`."""
        if self._row(execution_id)["status"] != "running":
            raise ValueError(f"execução já terminada: {execution_id}")
        record: dict[str, Any] = {
            "stage": "result",
            "id": execution_id,
            "status": "error" if error else "ok",
            "finished_at": now.astimezone(UTC).isoformat(),
            "error": error,
            "trials": [trial.to_dict() for trial in trials],
        }
        if outcome is not None:
            record["outcome"] = dict(outcome)
            record["artifacts"] = dict(artifacts or {})
        with self._db:
            self._apply(record)
        self._publish(f"{execution_id}.result.json", record)

    def discard(self, execution_id: str, reason: str, now: datetime) -> None:
        """Marca a execução como descartada, sem apagar nada (RF-273)."""
        if self._row(execution_id)["discarded_at"] is not None:
            raise ValueError(f"execução já descartada: {execution_id}")
        if not reason.strip():
            raise ValueError("o descarte precisa de um motivo")
        record = {
            "stage": "discard",
            "id": execution_id,
            "discarded_at": now.astimezone(UTC).isoformat(),
            "reason": reason,
        }
        with self._db:
            self._apply(record)
        self._publish(f"{execution_id}.discard.json", record)

    def put_artifact(self, execution_id: str, local: Path) -> str:
        """Envia um arquivo da execução para `derived/experiments/<id>/` e devolve o SHA-256."""
        if self._row(execution_id)["status"] != "running":
            raise ValueError(f"execução já terminada: {execution_id}")
        sha = file_sha256(local)
        self.store.put(local, artifact_key(execution_id, local.name), sha)
        return sha

    def artifact(self, execution_id: str, name: str) -> Path:
        """Caminho local de um arquivo da execução, baixado se faltar, conferido pelo SHA-256."""
        shown = self.show(execution_id)
        expected = shown.get("artifacts", {}).get(name)
        if expected is None:
            raise ValueError(f"execução {execution_id} sem o arquivo {name}")
        local = self.artifacts / execution_id / name
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            self.store.get(artifact_key(execution_id, name), local, expected)
        elif file_sha256(local) != expected:
            raise ValueError(f"{local}: SHA-256 diferente do registro")
        return local

    # ------------------------------------------------------------------ leitura

    def count(self, track: str) -> int:
        """Tentativas da trilha para o IS-15 (RF-274; DEC-38)."""
        row = self._db.execute(
            """
            SELECT COUNT(DISTINCT t.config_id) FROM trials t
            JOIN executions e ON e.id = t.execution_id
            WHERE e.kind = 'model' AND e.track = ?
            """,
            (track,),
        ).fetchone()
        return int(row[0])

    def executions(self, track: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM executions"
        params: tuple[str, ...] = ()
        if track is not None:
            query += " WHERE track = ?"
            params = (track,)
        rows = self._db.execute(query + " ORDER BY id", params).fetchall()
        return [self._execution(row) for row in rows]

    def show(self, execution_id: str) -> dict[str, Any]:
        execution = self._execution(self._row(execution_id))
        rows = self._db.execute(
            "SELECT * FROM trials WHERE execution_id = ? ORDER BY config_id", (execution_id,)
        ).fetchall()
        execution["trials"] = [
            {
                "config_id": row["config_id"],
                "config": json.loads(row["config"]),
                "metrics": json.loads(row["metrics"]),
            }
            for row in rows
        ]
        outcome = self._db.execute(
            "SELECT outcome, artifacts FROM outcomes WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if outcome is not None:
            execution["outcome"] = json.loads(outcome["outcome"])
            execution["artifacts"] = json.loads(outcome["artifacts"])
        return execution

    def latest(self, dataset_id: str, kind: str = "model") -> list[dict[str, Any]]:
        """A execução terminada mais recente, não descartada, de cada degrau, horizonte e modelo
        do dataset (RF-281)."""
        rows = self._db.execute(
            """
            SELECT * FROM executions
            WHERE dataset_id = ? AND kind = ? AND status = 'ok' AND discarded_at IS NULL
            ORDER BY id
            """,
            (dataset_id, kind),
        ).fetchall()
        chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            execution = self._execution(row)
            spec = execution["spec"]
            key = (json.dumps(spec["rungs"]), json.dumps(spec["horizons"]), str(spec["model"]))
            chosen[key] = execution
        return [self.show(e["id"]) for e in chosen.values()]

    def trial_metrics(self, track: str) -> dict[str, dict[str, Any]]:
        """Métricas de cada tentativa da trilha, pela execução de modelo mais recente que a
        avaliou: as mesmas tentativas que `count` conta (RF-274, RF-278)."""
        rows = self._db.execute(
            """
            SELECT t.config_id, t.metrics FROM trials t
            JOIN executions e ON e.id = t.execution_id
            WHERE e.kind = 'model' AND e.track = ?
            ORDER BY e.id
            """,
            (track,),
        ).fetchall()
        return {row["config_id"]: json.loads(row["metrics"]) for row in rows}

    # ------------------------------------------------------------------ interno

    def _row(self, execution_id: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM executions WHERE id = ?", (execution_id,)).fetchone()
        if row is None:
            raise ValueError(f"execução desconhecida: {execution_id}")
        return row  # type: ignore[no-any-return]

    @staticmethod
    def _execution(row: sqlite3.Row) -> dict[str, Any]:
        execution = dict(row)
        execution["spec"] = json.loads(execution["spec"])
        execution["final_evaluation"] = bool(execution["final_evaluation"])
        return execution

    def _apply(self, record: Mapping[str, Any]) -> None:
        stage = record["stage"]
        if stage == "start":
            self._db.execute(
                """
                INSERT INTO executions (id, kind, track, dataset_id, final_evaluation,
                    code_version, who, started_at, spec, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    record["id"],
                    record["kind"],
                    record["track"],
                    record["dataset_id"],
                    int(record["final_evaluation"]),
                    record["code_version"],
                    record["who"],
                    record["started_at"],
                    json.dumps(record["spec"], sort_keys=True),
                ),
            )
        elif stage == "result":
            self._db.execute(
                "UPDATE executions SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (record["status"], record["finished_at"], record["error"], record["id"]),
            )
            self._db.executemany(
                "INSERT INTO trials (execution_id, config_id, config, metrics) VALUES (?, ?, ?, ?)",
                [
                    (
                        record["id"],
                        trial["config_id"],
                        json.dumps(trial["config"], sort_keys=True),
                        json.dumps(trial["metrics"], sort_keys=True),
                    )
                    for trial in record["trials"]
                ],
            )
            if record.get("outcome") is not None:
                self._db.execute(
                    "INSERT INTO outcomes (execution_id, outcome, artifacts) VALUES (?, ?, ?)",
                    (
                        record["id"],
                        json.dumps(record["outcome"], sort_keys=True),
                        json.dumps(record.get("artifacts", {}), sort_keys=True),
                    ),
                )
        elif stage == "discard":
            self._db.execute(
                "UPDATE executions SET discarded_at = ?, discard_reason = ? WHERE id = ?",
                (record["discarded_at"], record["reason"], record["id"]),
            )
        else:
            raise ValueError(f"registro desconhecido: {stage}")

    def _publish(self, name: str, record: Mapping[str, Any]) -> None:
        """Grava o registro local e no S3; nunca substitui um registro (RF-273)."""
        local = self.records / name
        if local.exists():
            raise ValueError(f"registro já existe: {name}")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        self.store.put(local, f"{EXPERIMENTS}/{name}", file_sha256(local))


def artifact_key(execution_id: str, name: str) -> str:
    return f"{EXPERIMENTS}/{execution_id}/{name}"


def rebuild(path: Path, records: Iterable[Mapping[str, Any]], store: ObjectStore) -> Registry:
    """Refaz o SQLite a partir dos registros de `derived/experiments/` (RF-275).

    O arquivo novo só substitui o antigo depois de completo."""
    tmp = path.with_name(path.name + ".rebuild")
    tmp.unlink(missing_ok=True)
    registry = Registry(tmp, store)
    ordered = sorted(records, key=lambda r: (r["id"], _STAGES[r["stage"]]))
    with registry._db:
        for record in ordered:
            registry._apply(record)
    registry.close()
    tmp.replace(path)
    return Registry(path, store)
