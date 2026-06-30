"""Připojení k SQLite a inicializace schématu databáze."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

from .config import DB_PATH, DATA_DIR, ANTICHEAT_RULES

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Prague")     # „den" se počítá v českém čase (s DST)
except Exception:                            # bez tzdata (slim image) → fallback na UTC, ať to nespadne
    LOCAL_TZ = timezone.utc


def now_iso() -> str:
    """Aktuální čas v ISO formátu (UTC). Časy v DB ukládáme VŽDY v UTC."""
    return datetime.now(timezone.utc).isoformat()


def local_now() -> datetime:
    """Teď v českém čase (Europe/Prague)."""
    return datetime.now(LOCAL_TZ)


def local_date(offset_days: int = 0) -> str:
    """Datum podle ČESKÉHO času (YYYY-MM-DD) – klíč pro denní reset (chat cíl, aktivita…)."""
    return (local_now() + timedelta(days=offset_days)).date().isoformat()


def local_week_id() -> str:
    """ISO týden podle českého času (YYYY-Www) – pro týdenní questy."""
    y, w, _ = local_now().isocalendar()
    return f"{y}-W{w:02d}"


def local_day_start_iso(offset_days: int = 0) -> str:
    """UTC ISO čas začátku ČESKÉHO dne (00:00 Europe/Prague) – pro porovnání s UTC created_at."""
    d = (local_now() + timedelta(days=offset_days)).date()
    return datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    """Nové připojení k databázi (jedno na request)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI spouští sync endpointy ve více vláknech
    # (a teardown závislosti může běžet v jiném vlákně než vytvoření spojení).
    # Každý request má vlastní spojení, nesdílí se souběžně → je to bezpečné.
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # synchronous=NORMAL: s WAL je to bezpečné (DB zůstane konzistentní; při tvrdém pádu
    # OS/power se může ztratit jen pár posledních commitů) a HLAVNĚ to ruší fsync na každý
    # commit → mnohonásobně vyšší write throughput. Bez toho 1 SQLite writer nestíhal nápor
    # (heartbeaty + chat + drop claims) → „database is locked" kaskády a výpadky.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")   # explicitně (čeká na lock místo okamžité chyby)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kick_username TEXT UNIQUE,
    kick_id       TEXT,
    email         TEXT UNIQUE,
    username      TEXT NOT NULL,
    password_hash TEXT,
    points        INTEGER NOT NULL DEFAULT 0,
    role          TEXT NOT NULL DEFAULT 'user',
    avatar_url    TEXT,
    banned        INTEGER NOT NULL DEFAULT 0,
    ban_reason    TEXT,
    last_daily    TEXT,
    daily_streak  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    image_url   TEXT,
    cost_points INTEGER NOT NULL DEFAULT 0,
    category    TEXT,
    type        TEXT NOT NULL DEFAULT 'instant',
    subs_only   INTEGER NOT NULL DEFAULT 0,
    vip_only    INTEGER NOT NULL DEFAULT 0,
    stock       INTEGER NOT NULL DEFAULT -1,
    description TEXT,
    rarity      TEXT,
    ends_at     TEXT,
    hot         INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id   INTEGER REFERENCES products(id) ON DELETE SET NULL,
    points_spent INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS redeem_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    points_value INTEGER NOT NULL DEFAULT 0,
    product_id  INTEGER REFERENCES products(id) ON DELETE SET NULL,
    max_uses    INTEGER NOT NULL DEFAULT 1,
    uses_count  INTEGER NOT NULL DEFAULT 0,
    expires_at  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS points_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    change     INTEGER NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raffle_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raffle_winners (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

-- Přihlašovací relace (session cookie)
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip         TEXT,
    user_agent TEXT,
    last_seen  TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Log přihlášení (pro bezpečnost / anticheat) – historie i po odhlášení
CREATE TABLE IF NOT EXISTS login_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip         TEXT,
    user_agent TEXT,
    method     TEXT,
    created_at TEXT NOT NULL
);

-- Kdo už daný kód uplatnil (zabrání opakovanému použití stejným uživatelem)
CREATE TABLE IF NOT EXISTS redeem_uses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code_id    INTEGER NOT NULL REFERENCES redeem_codes(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(code_id, user_id)
);

-- Dropy: závod o kód z chatu (nejrychlejší berou body)
CREATE TABLE IF NOT EXISTS drops (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    points      INTEGER NOT NULL DEFAULT 0,
    max_winners INTEGER NOT NULL DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS drop_claims (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_id    INTEGER NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(drop_id, user_id)
);

-- Univerzální zámek jednorázových výplat (egg, podobné „1×/den" claimy).
-- INSERT OR IGNORE + kontrola rowcount → atomický „kdo první ten bere", odolný i proti souběhu
-- (PRIMARY KEY garantuje, že odměnu připíše jen jeden request, ne každý paralelní pokus).
CREATE TABLE IF NOT EXISTS claim_locks (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    claim_key  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, claim_key)
);

-- Idempotence Kick webhooku: ID už zpracovaných zpráv. PERZISTENTNÍ (na rozdíl od paměťové
-- dedup) → přežije restart/deploy, takže Kickův retry ani replay po restartu nepřičte body 2×.
-- Staré řádky se průběžně mažou (prune), tabulka neroste donekonečna.
CREATE TABLE IF NOT EXISTS webhook_seen (
    message_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Web Push subscriptiony (notifikace do mobilu i když je appka zavřená). 1 řádka = 1
-- prohlížeč/zařízení; uživatel jich může mít víc. endpoint UNIQUE (re-subscribe přepíše).
CREATE TABLE IF NOT EXISTS push_subs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    ua         TEXT,
    created_at TEXT NOT NULL
);

-- Anticheat pravidla (konfigurace) + klientské signály (fingerprint)
CREATE TABLE IF NOT EXISTS anticheat_rules (
    key       TEXT PRIMARY KEY,
    enabled   INTEGER NOT NULL DEFAULT 1,
    threshold INTEGER
);

CREATE TABLE IF NOT EXISTS client_signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    webdriver  INTEGER NOT NULL DEFAULT 0,
    fp_hash    TEXT,
    ua         TEXT,
    created_at TEXT NOT NULL
);

-- Bany podle otisku zařízení (zabanovaný se nevrátí novým účtem ze stejného prohlížeče)
CREATE TABLE IF NOT EXISTS fingerprint_bans (
    fp_hash    TEXT PRIMARY KEY,
    reason     TEXT,
    created_at TEXT NOT NULL
);

-- Bany podle IP adresy: zabanovaná IP vůbec neotevře web (full-page blok). Časově omezené.
CREATE TABLE IF NOT EXISTS ip_bans (
    ip         TEXT PRIMARY KEY,
    reason     TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT                -- NULL = trvalý ban
);

-- Kick bot (SedlakBOT): uložený OAuth token pro psaní do chatu. Vždy max 1 řádek (id=1).
CREATE TABLE IF NOT EXISTS bot_tokens (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    bot_username        TEXT,
    access_token        TEXT,
    refresh_token       TEXT,
    expires_at          TEXT,
    scope               TEXT,
    broadcaster_channel TEXT,
    broadcaster_user_id TEXT,
    is_demo             INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

-- Log zpráv odeslaných botem (i simulovaných v demu) – pro konzoli + simulovaný chat
CREATE TABLE IF NOT EXISTS bot_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT,
    author     TEXT,
    content    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'manual',   -- manual | drop | system
    sent_real  INTEGER NOT NULL DEFAULT 0,        -- 1 = reálně odesláno na Kick, 0 = demo/simulace
    error      TEXT,
    created_at TEXT NOT NULL
);

-- Obecné nastavení aplikace (klíč → hodnota). Toggly bota, ekonomika atd.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

-- Stav pasivního výdělku uživatele (sledování + chat): cooldowny + denní strop
CREATE TABLE IF NOT EXISTS activity_state (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    day           TEXT,                      -- YYYY-MM-DD (UTC), ke kterému patří počítadla
    earned_today  INTEGER NOT NULL DEFAULT 0,
    watch_today   INTEGER NOT NULL DEFAULT 0,
    chat_today    INTEGER NOT NULL DEFAULT 0,
    games_net_today INTEGER NOT NULL DEFAULT 0,
    last_watch_at TEXT,
    last_chat_at  TEXT
);

-- Komunitní SUB cíl: kdo dnes giftnul suby (a kolik z toho v happy hour). Po splnění
-- cíle berou odměnu JEN dnešní gifteři z happy hour (hh_subs > 0). Reset = nový den.
CREATE TABLE IF NOT EXISTS subgoal_gifters (
    day      TEXT NOT NULL,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subs     INTEGER NOT NULL DEFAULT 0,   -- kolik subů dnes giftnul celkem
    hh_subs  INTEGER NOT NULL DEFAULT 0,   -- z toho během happy hour
    paid     INTEGER NOT NULL DEFAULT 0,   -- (legacy) starý 1×-per-gifter model
    paid_tier INTEGER NOT NULL DEFAULT 0,  -- nejvyšší tier, za který gifter už dostal odměnu (KUMULATIVNÍ model)
    PRIMARY KEY (day, user_id)
);

-- Farmářský Battle Pass: sezónní (měsíční) postupová dráha. Tier = XP nafarmené OD
-- začátku sezóny (earned_total − baseline). claimed = JSON list vyzvednutých tierů.
CREATE TABLE IF NOT EXISTS battlepass (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    season     TEXT NOT NULL,                -- 'YYYY-MM'
    baseline   INTEGER NOT NULL DEFAULT 0,   -- earned_total na začátku sezóny pro tohoto hráče
    claimed    TEXT NOT NULL DEFAULT '[]',   -- JSON list vyzvednutých tierů
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, season)
);

-- Login kalendář: které dny v měsíci byl hráč aktivní (mark při denním claimu) +
-- vyzvednuté milníky (X aktivních dní = bonus). Reset = nový měsíc.
CREATE TABLE IF NOT EXISTS login_calendar (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month      TEXT NOT NULL,                -- 'YYYY-MM'
    days       TEXT NOT NULL DEFAULT '[]',   -- JSON list dnů (1..31) aktivních v měsíci
    claimed_ms TEXT NOT NULL DEFAULT '[]',   -- JSON list vyzvednutých milníků (počet dní)
    PRIMARY KEY (user_id, month)
);

-- Zahrádka (farm-sim): zasazené plodiny na záhonech. Prázdný záhon = žádný řádek.
-- Plant = zaplať sazbu + nastav ready_at; po dorostení harvest = odměna, řádek smaž.
CREATE TABLE IF NOT EXISTS garden (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plot       INTEGER NOT NULL,             -- 0..N_PLOTS-1
    crop       TEXT NOT NULL,
    planted_at TEXT NOT NULL,
    ready_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, plot)
);

-- Dekorace zahrádky: koupené farmářské ozdoby (cosmetic sink). Vlastní se navždy.
CREATE TABLE IF NOT EXISTS garden_decor (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    decor_key  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, decor_key)
);

-- Statek (mini-farma): zvíře v slotu. ready_at='' = hladové (neprodukuje), jinak ISO konec cyklu.
-- Loop: koupě → krmení (ready_at=teď+hodiny) → sebrání produktu (ready_at='' → zas hlad).
CREATE TABLE IF NOT EXISTS farm_animals (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slot       INTEGER NOT NULL,             -- 0..n_slots-1
    animal_key TEXT NOT NULL,
    ready_at   TEXT NOT NULL DEFAULT '',     -- '' = hladové; jinak ISO konec produkčního cyklu
    fed_count  INTEGER NOT NULL DEFAULT 0,   -- kolikrát nakrmeno (pro levely zvířete v P2)
    bought_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, slot)
);

-- PvP hry o body: piškvorky (gomoku). 1v1 se sázkou, escrow vkladů, vítěz bere bank.
CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL DEFAULT 'gomoku',
    status      TEXT NOT NULL DEFAULT 'open',   -- open | active | finished | cancelled
    stake       INTEGER NOT NULL,                -- vklad KAŽDÉHO hráče (bank = 2× stake)
    board       TEXT NOT NULL,                   -- řetězec BOARD*BOARD znaků: '.', '1', '2'
    turn        INTEGER NOT NULL DEFAULT 1,      -- kdo je na tahu (1 = p1/X, 2 = p2/O)
    p1_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    p2_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    winner      INTEGER,                         -- 0 = remíza, 1 = p1, 2 = p2, NULL = běží
    move_count  INTEGER NOT NULL DEFAULT 0,
    last_move_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Duely (PvP o bank): coinflip/dice = okamžité vyhodnocení při přijetí výzvy, rps = na kola.
-- Vklady obou hráčů jsou v escrow (odečtou se hned), vítěz bere bank (mínus volitelný rake).
CREATE TABLE IF NOT EXISTS duels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,                   -- 'coinflip' | 'dice' | 'rps'
    status      TEXT NOT NULL DEFAULT 'open',    -- open | active | finished | cancelled
    stake       INTEGER NOT NULL,                -- vklad KAŽDÉHO hráče (bank = 2× stake)
    p1_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    p2_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    winner      INTEGER,                         -- 0=remíza, 1=p1, 2=p2, NULL=běží
    state       TEXT NOT NULL DEFAULT '',        -- JSON: výsledek (strana/hody/kola RPS)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Predikce (sázení bodů na výsledek – CS2 zápasy/eventy). Pari-mutuel: výherci si dělí bank
-- podle výše sázky. Vklady jsou v escrow (odečtou se hned), výplata po vyhodnocení streamerem.
CREATE TABLE IF NOT EXISTS predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    question         TEXT NOT NULL,
    game             TEXT NOT NULL DEFAULT 'CS2',
    status           TEXT NOT NULL DEFAULT 'open',   -- open | locked | resolved | cancelled
    winner_option_id INTEGER,                          -- vyplněno po vyhodnocení
    created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL,
    locked_at        TEXT,
    resolved_at      TEXT
);
CREATE TABLE IF NOT EXISTS prediction_options (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
    label         TEXT NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prediction_bets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
    option_id     INTEGER NOT NULL REFERENCES prediction_options(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount        INTEGER NOT NULL,
    payout        INTEGER NOT NULL DEFAULT 0,         -- kolik vyplaceno po vyhodnocení (0 = prohra)
    created_at    TEXT NOT NULL,
    UNIQUE(prediction_id, user_id)                    -- 1 sázka na predikci (lze navyšovat na STEJNOU možnost)
);
CREATE TABLE IF NOT EXISTS dm_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,                 -- vlastník vlákna (ne-staff účastník)
    from_id    INTEGER NOT NULL,                 -- kdo poslal (staff, nebo sám user = odpověď)
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seen       INTEGER NOT NULL DEFAULT 0        -- příjemce zprávu viděl
);
CREATE INDEX IF NOT EXISTS idx_dm_user ON dm_messages(user_id, id);
CREATE TABLE IF NOT EXISTS fair_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    game        TEXT NOT NULL,                    -- 'wheel' | 'duel' | ...
    server_hash TEXT NOT NULL,                    -- commit platný pro tuto hru
    client_seed TEXT NOT NULL,
    nonce       INTEGER NOT NULL,
    result      INTEGER NOT NULL,                 -- výsledný index (u kola segment)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fairlog_user ON fair_log(user_id, id);
CREATE TABLE IF NOT EXISTS rank_snapshots (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,                        -- YYYY-MM-DD
    rank    INTEGER NOT NULL,
    PRIMARY KEY (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_ranksnap_day ON rank_snapshots(day);
CREATE INDEX IF NOT EXISTS idx_pred_opts_pred ON prediction_options(prediction_id);
CREATE INDEX IF NOT EXISTS idx_pred_bets_pred ON prediction_bets(prediction_id);

-- Audit log: kdo (admin), kdy a co provedl (kritické admin akce – body, ban, role, kódy, dropy…)
CREATE TABLE IF NOT EXISTS admin_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    admin_name TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    details    TEXT,
    ip         TEXT,
    created_at TEXT NOT NULL
);

-- Patch notes / novinky (changelog – ať diváci vidí, že se na webu pořád pracuje). Spravuje admin/broadcaster.
CREATE TABLE IF NOT EXISTS admin_user_meta (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    watchlisted     INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT '',
    updated_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by_name TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS patch_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    tag        TEXT NOT NULL DEFAULT 'new',   -- new | improve | fix
    published  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patchnotes_created ON patch_notes(created_at);

-- Odznaky / achievementy: 1 řádek na (uživatel, odznak); tier = nejvyšší dosažený stupeň
CREATE TABLE IF NOT EXISTS user_badges (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge_key  TEXT NOT NULL,
    tier       INTEGER NOT NULL DEFAULT 1,
    awarded_at TEXT NOT NULL,
    PRIMARY KEY (user_id, badge_key)
);
CREATE INDEX IF NOT EXISTS idx_user_badges_user ON user_badges(user_id);

-- Postup v denních/týdenních úkolech (baseline = stav statu na začátku období; diff = postup)
CREATE TABLE IF NOT EXISTS quest_progress (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    quest_key  TEXT NOT NULL,
    period_id  TEXT NOT NULL,
    baseline   INTEGER NOT NULL DEFAULT 0,
    claimed    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, quest_key, period_id)
);

-- Vlastněná kosmetika (barvy nicku / rámečky / bannery). Katalog je v kódu (cosmetics.py),
-- tady se drží jen co kdo koupil. Nasazené kousky jsou ve sloupcích users.cos_*.
CREATE TABLE IF NOT EXISTS cosmetic_owns (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_key    TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    PRIMARY KEY (user_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);
CREATE INDEX IF NOT EXISTS idx_dropclaims_drop ON drop_claims(drop_id);
CREATE INDEX IF NOT EXISTS idx_signals_user ON client_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_points_log_user ON points_log(user_id);
CREATE INDEX IF NOT EXISTS idx_points_log_created ON points_log(created_at);
CREATE INDEX IF NOT EXISTS idx_raffle_product ON raffle_entries(product_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_login_user ON login_events(user_id);
CREATE INDEX IF NOT EXISTS idx_login_ip ON login_events(ip);
CREATE INDEX IF NOT EXISTS idx_login_created ON login_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_user_meta_watch ON admin_user_meta(watchlisted);
CREATE INDEX IF NOT EXISTS idx_botmsg_created ON bot_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);
CREATE INDEX IF NOT EXISTS idx_games_players ON games(p1_id, p2_id);
CREATE INDEX IF NOT EXISTS idx_duels_status ON duels(status);
CREATE INDEX IF NOT EXISTS idx_duels_players ON duels(p1_id, p2_id);

CREATE TABLE IF NOT EXISTS partner_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    url TEXT NOT NULL,
    reward INTEGER NOT NULL DEFAULT 100,
    icon TEXT DEFAULT '🤝',
    enabled INTEGER NOT NULL DEFAULT 1,
    mode TEXT NOT NULL DEFAULT 'once',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS partner_link_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    link_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, link_id)
);
CREATE INDEX IF NOT EXISTS idx_plc_user ON partner_link_claims(user_id);
CREATE TABLE IF NOT EXISTS partner_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS partner_flash_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    link_id INTEGER NOT NULL,
    round_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, link_id, round_id)
);
CREATE INDEX IF NOT EXISTS idx_pfc_user ON partner_flash_claims(user_id);

CREATE TABLE IF NOT EXISTS blackjack_games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bet         INTEGER NOT NULL,
    deck        TEXT NOT NULL,                    -- JSON: zbývající karty (server-only)
    player      TEXT NOT NULL,                    -- JSON: hráčovy karty
    dealer      TEXT NOT NULL,                    -- JSON: dealerovy karty (skrytá do odhalení)
    status      TEXT NOT NULL DEFAULT 'active',   -- active | done
    result      TEXT,                              -- blackjack | win | push | lose | bust
    payout      INTEGER NOT NULL DEFAULT 0,        -- připsáno zpět (vč. vrácené sázky)
    doubled     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bj_user ON blackjack_games(user_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bj_one_active ON blackjack_games(user_id) WHERE status='active';

-- Soukromý sdílený blackjack stůl (multiplayer, vs dealer, jen na kód/link)
CREATE TABLE IF NOT EXISTS bj_rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,             -- join kód (do linku)
    host_id     INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'betting',  -- betting | playing | done | closed
    dealer      TEXT NOT NULL DEFAULT '[]',       -- JSON dealer karty
    deck        TEXT NOT NULL DEFAULT '[]',       -- JSON shoe (server-only)
    deck_pos    INTEGER NOT NULL DEFAULT 0,       -- atomické tahání karet (RETURNING)
    round_no    INTEGER NOT NULL DEFAULT 0,
    phase_until TEXT,                             -- auto-flow: deadline aktuální fáze (betting/done) → auto-posun na pollu
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bj_seats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL REFERENCES bj_rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    bet         INTEGER NOT NULL DEFAULT 0,
    hand        TEXT NOT NULL DEFAULT '[]',       -- JSON hráčovy karty (sdílený stůl = vidí všichni)
    state       TEXT NOT NULL DEFAULT 'idle',     -- idle | ready | acting | stood | bust | resolved
    result      TEXT,
    payout      INTEGER NOT NULL DEFAULT 0,
    hand2       TEXT NOT NULL DEFAULT '[]',       -- split: druhá ruka (JSON karty)
    bet2        INTEGER NOT NULL DEFAULT 0,       -- split: sázka 2. ruky
    state2      TEXT,                             -- split: stav 2. ruky (NULL = bez splitu)
    result2     TEXT,
    payout2     INTEGER NOT NULL DEFAULT 0,
    active_hand INTEGER NOT NULL DEFAULT 1,       -- split: která ruka je na tahu (1 / 2)
    acted_at    TEXT,                             -- pro AFK auto-stand
    seen_at     TEXT,
    joined_at   TEXT NOT NULL,
    UNIQUE(room_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_bjseat_room ON bj_seats(room_id);
CREATE TABLE IF NOT EXISTS bj_chat (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL REFERENCES bj_rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    username    TEXT NOT NULL,
    msg         TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bjchat_room ON bj_chat(room_id, id);

-- Mines (single-player provably-fair): 1 aktivní hra na uživatele. layout = JSON pozic bomb,
-- revealed = JSON odkrytých bezpečných polí. Bomby se hráči NEposílají, dokud hra běží.
CREATE TABLE IF NOT EXISTS mines_games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bet         INTEGER NOT NULL,
    mines       INTEGER NOT NULL,
    layout      TEXT NOT NULL,
    revealed    TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'active',
    payout      INTEGER NOT NULL DEFAULT 0,
    server_hash TEXT,
    client_seed TEXT,
    nonce       INTEGER,
    created_at  TEXT NOT NULL,
    ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_mines_user ON mines_games(user_id, status);

CREATE TABLE IF NOT EXISTS gift_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount        INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    note          TEXT,                              -- důvod od odesílatele (nepovinný)
    escrow_log_id INTEGER,                            -- řádek v points_log s blokací u odesílatele
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    decided_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_gift_requests_status ON gift_requests(status, id);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    icon        TEXT NOT NULL DEFAULT '🔔',
    title       TEXT NOT NULL,
    body        TEXT,
    link        TEXT,
    read        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, read, id);

CREATE TABLE IF NOT EXISTS mod_applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    answers     TEXT NOT NULL,                   -- JSON {pole: odpověď}
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | rejected
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_modapp_status ON mod_applications(status, id);

CREATE TABLE IF NOT EXISTS anniversary_awards (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    milestone_days INTEGER NOT NULL,        -- který milník (30/90/180/365…) už byl vyplacen
    awarded_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, milestone_days)
);
"""


# Sloupce doplněné do existujících tabulek (migrace bez resetu DB)
_MIGRATIONS = [
    ("activity_state", "games_net_today", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "banned", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "ban_reason", "TEXT"),
    ("users", "kick_username", "TEXT"),
    ("users", "kick_id", "TEXT"),
    ("users", "last_daily", "TEXT"),
    ("users", "daily_streak", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "last_wheel", "TEXT"),
    ("users", "steam_trade_url", "TEXT"),
    ("users", "bio", "TEXT"),          # osobní bio na profilu (max 160 znaků)
    ("users", "fav_game", "TEXT"),     # vypíchnutá oblíbená hra (showcase)
    ("users", "prestige", "INTEGER NOT NULL DEFAULT 0"),   # prestige level (spálené sedláky = status, anti-inflace sink)
    ("users", "earned_total", "INTEGER NOT NULL DEFAULT 0"),   # celkem za život NAFARMENO (jen kladné přírůstky) → základ pro XP/level
    ("garden", "pest", "INTEGER NOT NULL DEFAULT 0"),          # chrobáci: 0=nezachráněno, 2=zachráněno (aktivita derivovaná z pest_at + okna)
    ("garden", "pest_at", "TEXT"),                             # KDY se chrobáci objeví (ISO) / NULL = bez chrobáků. Aktivní = pest_at .. pest_at+okno
    ("garden", "notified", "INTEGER NOT NULL DEFAULT 0"),      # bitmask in-app notifikací: 1=úroda dozrála, 2=chrobáci (1× na záhon, brání spamu)
    ("battlepass", "claimed_premium", "TEXT NOT NULL DEFAULT '[]'"),   # prémiová (sub-only) řada Battle Passu – JSON list vyzvednutých tierů
    # Responsible gaming – denní limit sázek (Tipsport-style). 0/NULL = bez limitu.
    ("users", "wager_limit", "INTEGER"),                   # aktuální denní strop sázek
    ("users", "wager_limit_pending", "INTEGER"),           # navýšení čeká na zítřek (snížit jde hned)
    ("users", "wagered_today", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "wager_day", "TEXT"),                        # den, ke kterému wagered_today platí
    ("users", "is_sub", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "is_vip", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "is_og", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "sub_expires_at", "TEXT"),
    ("sessions", "ip", "TEXT"),
    ("sessions", "user_agent", "TEXT"),
    ("sessions", "last_seen", "TEXT"),
    ("products", "description", "TEXT"),
    ("products", "rarity", "TEXT"),
    ("products", "ends_at", "TEXT"),
    ("products", "hot", "INTEGER NOT NULL DEFAULT 0"),
    ("products", "period", "TEXT"),
    ("products", "max_per_person_pct", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "product_name", "TEXT"),
    ("client_signals", "fp_hash", "TEXT"),
    ("drop_claims", "ip", "TEXT"),
    ("drop_claims", "fp_hash", "TEXT"),
    ("games", "active_at", "TEXT"),
    ("predictions", "lock_at", "TEXT"),      # auto-lock: ISO čas, kdy se sázky samy zavřou (NULL = ruční)
    ("users", "last_league", "TEXT"),        # nejvyšší dosažená liga (pro detekci postupu)
    ("users", "pending_rankup", "TEXT"),     # fronta konfet: liga k oslavě při dalším načtení
    ("users", "last_rank", "INTEGER"),       # poslední známá pozice (pro „přeskočil tě")
    ("users", "pending_overtake", "TEXT"),   # fronta hlášky „přeskočil tě" (JSON {by, rank})
    ("partner_links", "mode", "TEXT NOT NULL DEFAULT 'once'"),  # 'once' (1× navždy) / 'flash' (random obnova)
    ("users", "cos_name", "TEXT"),       # nasazená barva nicku (klíč z cosmetics.CATALOG)
    ("users", "cos_frame", "TEXT"),      # nasazený rámeček avataru
    ("users", "cos_banner", "TEXT"),     # nasazený profil banner
    ("users", "gamble_block_until", "TEXT"),  # sebevyloučení ze sázek: ISO konec / "permanent" / NULL
    ("users", "timeout_until", "TEXT"),       # timeout (dočasný blok webu): ISO konec / NULL. Zrcadlí Kick timeout.
    ("users", "egg_found_at", "TEXT"),        # easter egg „Tajný sedlák": kdy našel (1×/uživatel gate + 🥚 odznak)
    ("subgoal_gifters", "paid", "INTEGER NOT NULL DEFAULT 0"),  # SUB cíl: (legacy) gifter už dostal odměnu (1× model)
    ("subgoal_gifters", "paid_tier", "INTEGER NOT NULL DEFAULT 0"),  # SUB cíl: nejvyšší vyplacený tier (kumulativní model)
    ("users", "earned_today", "INTEGER NOT NULL DEFAULT 0"),  # XP z FARMENÍ nasbírané dnes (denní strop XP)
    ("users", "earned_day", "TEXT"),          # den (local) pro reset earned_today
    ("users", "fair_server_seed", "TEXT"),    # provably fair: tajný server seed (aktuální)
    ("users", "fair_server_hash", "TEXT"),    # SHA-256 commit (ukázán předem)
    ("users", "fair_client_seed", "TEXT"),    # client seed (hráč si mění)
    ("users", "fair_nonce", "INTEGER NOT NULL DEFAULT 0"),
    # mines_games: dorovnání sloupců na PRE-EXISTUJÍCÍ prod tabulku (CREATE IF NOT EXISTS byla
    # no-op, protože tabulka už existovala s jiným/starým schématem). Nullable – kód je vždy plní.
    ("mines_games", "layout", "TEXT"),
    ("mines_games", "server_hash", "TEXT"),
    ("mines_games", "client_seed", "TEXT"),
    ("mines_games", "nonce", "INTEGER"),
    ("mines_games", "ended_at", "TEXT"),
    # gift_requests: tabulka už na prod vznikla bez sloupce `note` (CREATE IF NOT EXISTS = no-op),
    # tady ho dorovnáme. Nullable – nepovinný důvod od odesílatele.
    ("gift_requests", "note", "TEXT"),
    # BJ auto-flow: deadline fáze (betting → auto-rozdání, done → auto-nové kolo). NULL = bez odpočtu.
    ("bj_rooms", "phase_until", "TEXT"),
    # BJ split: druhá ruka (rozdělení páru). state2 NULL = seat bez splitu.
    ("bj_seats", "hand2", "TEXT NOT NULL DEFAULT '[]'"),
    ("bj_seats", "bet2", "INTEGER NOT NULL DEFAULT 0"),
    ("bj_seats", "state2", "TEXT"),
    ("bj_seats", "result2", "TEXT"),
    ("bj_seats", "payout2", "INTEGER NOT NULL DEFAULT 0"),
    ("bj_seats", "active_hand", "INTEGER NOT NULL DEFAULT 1"),
]


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    """Přečte hodnotu z app_settings (string), nebo vrátí default."""
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Zapíše hodnotu do app_settings (upsert). Necommituje – commit volá caller."""
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_iso()),
    )


def init_db() -> None:
    """Vytvoří tabulky a doplní chybějící sloupce (bez nutnosti reset DB)."""
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        for table, col, ddl in _MIGRATIONS:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # Garden v1 ukládala aktivní útok jako pest=1 bez času. Převeď ho na v2
        # časovaný útok od zasazení; pest=0 pak znamená nezachráněný a funguje rescue.
        conn.execute(
            "UPDATE garden SET pest_at = planted_at, pest = 0 "
            "WHERE pest = 1 AND pest_at IS NULL"
        )
        # výchozí anticheat pravidla (idempotentně)
        for r in ANTICHEAT_RULES:
            conn.execute("INSERT OR IGNORE INTO anticheat_rules (key, enabled, threshold) VALUES (?, ?, ?)",
                         (r["key"], 0 if r.get("default_off") else 1, r["threshold"]))
        # Jednorázový reset baseline u „earn" questů: změnila se definice statu 'earned'
        # (nově se počítají JEN body za sledování + chat na streamu, ne všechny kladné body).
        # Staré baseline řádky jsou v jiném měřítku → smazat, ať se přepočítají z nové definice
        # (jinak by postup zůstal zaseknutý na 0). Spustí se právě jednou (flag v app_settings).
        if get_setting(conn, "_mig_earned_streamonly", "") != "1":
            conn.execute("DELETE FROM quest_progress WHERE quest_key IN ('d_earn','w_earn')")
            set_setting(conn, "_mig_earned_streamonly", "1")
        # Staré auto-bany byly v seznamu bez expirace, i když text sliboval 24 h.
        # Jednorázově jim dej 24 h od nasazení; ruční bany bez důvodu zůstanou trvalé.
        if get_setting(conn, "_mig_mines_ban_expiry", "") != "1":
            raw_ids = get_setting(conn, "mines_ban_uids", "")
            try:
                ids = {int(v) for v in json.loads(raw_ids)} if raw_ids else set()
            except (ValueError, TypeError):
                ids = set()
            expiries = {}
            if ids:
                qm = ",".join("?" * len(ids))
                rows = conn.execute(f"SELECT id,ban_reason FROM users WHERE id IN ({qm})", list(ids)).fetchall()
                expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                for row in rows:
                    reason = row["ban_reason"] or ""
                    if reason.startswith("Automaticky zablokovano") or reason.startswith("Automaticky zablokováno") or reason.startswith("Ban 24 h"):
                        expiries[str(row["id"])] = expires
            set_setting(conn, "mines_ban_expires", json.dumps(expiries, sort_keys=True))
            set_setting(conn, "_mig_mines_ban_expiry", "1")
        conn.commit()
    finally:
        conn.close()
