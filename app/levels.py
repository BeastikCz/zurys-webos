"""Levely / XP.

XP = `users.earned_total` = nasbíráno sedláků za celou dobu (jen kladné změny v points_log).
Roste, NIKDY se neresetuje, utrácení ho nesráží → osobní postup pro každého (non-zero-sum).
Hodnotu drží achievements daemon (přepočet každých 10 min) + jednorázový backfill při startu.

Křivka: k dosažení levelu L je potřeba `C * L²` XP (každý level dražší než minulý).
  L0 = 0 XP, L10 = 25 000, L20 = 100 000, L30 = 225 000, L50 = 625 000 (C=250).
Čistá funkce – jde testovat i renderovat na klientovi stejně.
"""
LEVEL_C = 250


def xp_for_level(level: int) -> int:
    """Kolik XP je potřeba k dosažení daného levelu (kumulativně)."""
    level = max(0, int(level))
    return LEVEL_C * level * level


def level_info(earned) -> dict:
    """Z nasbíraného XP → level + postup do dalšího. Bezpečné pro None/záporné."""
    earned = max(0, int(earned or 0))
    lvl = int((earned / LEVEL_C) ** 0.5)
    cur = xp_for_level(lvl)
    nxt = xp_for_level(lvl + 1)
    span = nxt - cur
    into = earned - cur
    return {
        "level": lvl,
        "xp": earned,            # celkem nasbíráno
        "into": into,            # XP nad rámec aktuálního levelu
        "span": span,            # kolik XP má tento level „šířku"
        "to_next": nxt - earned,  # kolik chybí do dalšího levelu
        "pct": round(into * 100 / span) if span else 0,
    }
