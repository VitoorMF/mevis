"""Períodos de holdout pré-registrados (doc 04 §3.3). Sem E/S.

O holdout da trilha A está lacrado: nenhuma consulta, gráfico ou estatística antes da avaliação
final. Quem processa dados por período recusa estes dias, a menos que a execução declare,
explicitamente, que é a avaliação final (RF-179; DEC-31). O holdout da trilha B começa no
primeiro minuto depois do commit de congelamento (doc 04 §3.3, regra 4; RF-228).
"""

from datetime import UTC, date, datetime, time, timedelta

HOLDOUT_A = (date(2026, 7, 23), date(2026, 10, 22))
"""Trilha A: 23/07/2026 a 22/10/2026, inclusive (PD-17)."""
HOLDOUT_B_START: datetime | None = None
"""Trilha B: primeiro minuto depois do commit de congelamento, em UTC. Fica `None` até esse
commit, que o fixa aqui; até lá, não há holdout B a recusar."""


def holdout_days(start: date, end: date) -> list[date]:
    """Dias de `[start, end]` que caem no holdout da trilha A."""
    first, last = max(start, HOLDOUT_A[0]), min(end, HOLDOUT_A[1])
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def in_holdout(day: date) -> bool:
    return HOLDOUT_A[0] <= day <= HOLDOUT_A[1]


class HoldoutRefused(ValueError):
    """O período pedido inclui o holdout da trilha A sem a opção da avaliação final (RF-179)."""


def refuse_holdout(start: date, end: date, *, allow: bool, requirement: str) -> list[date]:
    """Levanta `HoldoutRefused` se `[start, end]` toca o holdout sem `allow`; devolve os dias."""
    reserved = holdout_days(start, end)
    if reserved and not allow:
        first, last = HOLDOUT_A
        raise HoldoutRefused(
            f"o período inclui {len(reserved)} dia(s) do holdout da trilha A"
            f" ({first:%d/%m/%Y} a {last:%d/%m/%Y}); só com a opção da avaliação final"
            f" (doc 04 §3.3; {requirement})"
        )
    return reserved


def touches_holdout_b(start: date, end: date) -> bool:
    """`[start, end]` (dias inteiros, UTC) tem algum minuto a partir do congelamento da trilha B."""
    freeze = HOLDOUT_B_START
    return freeze is not None and datetime.combine(end + timedelta(days=1), time(), UTC) > freeze


def refuse_holdout_b(start: date, end: date, *, allow: bool, requirement: str) -> bool:
    """Levanta `HoldoutRefused` se `[start, end]` toca o holdout B sem `allow`."""
    touched = touches_holdout_b(start, end)
    if touched and not allow:
        raise HoldoutRefused(
            f"o período inclui minutos do holdout da trilha B (a partir de {HOLDOUT_B_START});"
            f" só com a opção da avaliação final (doc 04 §3.3; {requirement})"
        )
    return touched
