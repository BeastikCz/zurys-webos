"""Provably fair (commit-reveal) – ověřitelná náhoda her.

Princip (jako CSGORoll/Stake):
  1. Server vygeneruje tajný `server_seed`, ukáže PŘEDEM jeho SHA-256 hash (commit).
  2. Hráč má svůj `client_seed` (může si ho změnit) a počítadlo `nonce` (roste s každou hrou).
  3. Výsledek hry = deterministická funkce z `HMAC-SHA256(server_seed, "client_seed:nonce")`.
  4. Po ROTACI seedu server odhalí starý `server_seed`. Hráč si ověří:
     - `sha256(odhalený) == commit` (server seed neměnil dodatečně)
     - přepočítá `HMAC(...)` pro každou hru → musí sedět s tím, co padlo.
  → Server nemůže výsledek ošvindlit (hash byl zveřejněn dřív, než znal client_seed/nonce),
    a hráč si vše ověří sám. Čistá matika, žádná důvěra potřeba.

Stejný výpočet jde 1:1 udělat v prohlížeči (HMAC-SHA256 přes SubtleCrypto) → veřejná „Ověřit" stránka.
"""
import hashlib
import hmac
import secrets

_POW32 = 0x100000000   # 2^32


def new_server_seed() -> str:
    """Nový tajný server seed (64 hex znaků)."""
    return secrets.token_hex(32)


def new_client_seed() -> str:
    """Výchozí client seed (hráč si může změnit)."""
    return secrets.token_hex(8)


def seed_hash(server_seed: str) -> str:
    """SHA-256 commit, který ukážeme PŘEDEM (než hráč zná výsledek)."""
    return hashlib.sha256(server_seed.encode()).hexdigest()


def digest(server_seed: str, client_seed: str, nonce: int) -> str:
    """HMAC-SHA256(server_seed, 'client_seed:nonce') → hex. Klíč = server_seed jako UTF-8."""
    msg = f"{client_seed}:{nonce}".encode()
    return hmac.new(server_seed.encode(), msg, hashlib.sha256).hexdigest()


def roll_float(server_seed: str, client_seed: str, nonce: int) -> float:
    """Deterministický float v [0,1) z prvních 8 hex znaků digestu (32 bitů)."""
    return int(digest(server_seed, client_seed, nonce)[:8], 16) / _POW32


def weighted_index(server_seed: str, client_seed: str, nonce: int, weights) -> int:
    """Vybere index podle vah (stejná sémantika jako secure_weighted_choice), deterministicky."""
    weights = list(weights)
    total = sum(weights)
    r = roll_float(server_seed, client_seed, nonce) * total
    acc = 0
    for i, w in enumerate(weights):
        acc += w
        if r < acc:
            return i
    return len(weights) - 1


def mine_positions(server_seed: str, client_seed: str, nonce: int, total: int, mines: int):
    """Deterministicky a ověřitelně vybere `mines` různých pozic bomb z `total` polí (Mines).
    Fisher-Yates shuffle řízený bytestreamem SHA256(digest:counter) → reprodukovatelné i v JS."""
    mines = max(1, min(int(mines), total - 1))
    base = digest(server_seed, client_seed, nonce)
    buf, ctr = bytearray(), 0

    def rand_below(n: int) -> int:
        nonlocal buf, ctr
        while len(buf) < 4:
            buf.extend(hashlib.sha256(f"{base}:{ctr}".encode()).digest())
            ctr += 1
        v = int.from_bytes(buf[:4], "big")
        del buf[:4]
        return v % n

    arr = list(range(total))
    for i in range(total - 1, 0, -1):
        j = rand_below(i + 1)
        arr[i], arr[j] = arr[j], arr[i]
    return sorted(arr[:mines])
