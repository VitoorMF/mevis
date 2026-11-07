"""Períodos de holdout pré-registrados (doc 04 §3.3). Sem E/S.

O holdout da trilha A está lacrado: nenhuma consulta, gráfico ou estatística antes da avaliação
final. Quem processa dados por período recusa estes dias, a menos que a execução declare,
explicitamente, que é a avaliação final (RF-179; DEC-31).
"""

from datetime import date, timedelta

HOLDOUT_A = (date(2026, 7, 23), date(2026, 10, 22))
"""Trilha A: 23/07/2026 a 22/10/2026, inclusive (PD-17)."""


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
