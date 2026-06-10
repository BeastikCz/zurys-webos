"""Hashování hesel (PBKDF2-SHA256) a generování tokenů – jen standardní knihovna."""
import hashlib
import hmac
import secrets
from collections.abc import Iterable, Sequence

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000
_RNG = secrets.SystemRandom()


def hash_password(password: str) -> str:
    """Vrátí řetězec 'pbkdf2_sha256$iterace$salt$hash'."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Ověří heslo proti uloženému hashi (časově konstantní porovnání)."""
    try:
        algo, iters, salt, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def new_token() -> str:
    """Náhodný token pro session cookie."""
    return secrets.token_urlsafe(32)


def new_code(prefix: str = "") -> str:
    """Náhodný redeem kód, např. 'STREAM-AB12CD'."""
    body = secrets.token_hex(3).upper()
    return f"{prefix}{body}" if prefix else body


def secure_choice(items: Sequence):
    """Vybere jednu polozku pres OS CSPRNG (vhodne pro losovani/hry o body)."""
    if not items:
        raise ValueError("secure_choice() needs at least one item")
    return _RNG.choice(items)


def secure_randint(a: int, b: int) -> int:
    """Vyber celeho cisla vcetne obou hran pres OS CSPRNG."""
    return _RNG.randint(a, b)


def secure_weighted_choice(items: Iterable, weights: Iterable[int]):
    """Vazeny vyber bez modulu random, aby odmeny nevisely na predikovatelnem PRNG."""
    pairs = [(item, max(0, int(weight))) for item, weight in zip(items, weights)]
    total = sum(weight for _, weight in pairs)
    if not pairs or total <= 0:
        raise ValueError("secure_weighted_choice() needs positive weights")
    pick = secrets.randbelow(total)
    upto = 0
    for item, weight in pairs:
        upto += weight
        if pick < upto:
            return item
    return pairs[-1][0]
