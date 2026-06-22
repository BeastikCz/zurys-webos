"""WebOS – věrnostní bodový shop pro streamera. Vstupní bod FastAPI aplikace."""
import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import ipban, ddos, alerts, maintenance, navaja_import, awp_import, season_award
from .backup import start_backup_daemon
from .autodrop import start_autodrop_daemon
from .live_events import start_live_events_daemon
from .partners_flash import start_partners_flash_daemon
from .digest import start_digest_daemon
from .achievements import start_achievements_daemon
from .order_cleanup import start_order_cleanup_daemon
from .anniversary import start_anniversary_daemon
from .config import WEB_DIR, UPLOAD_DIR, SESSION_COOKIE, STAFF_ROLES, TRUSTED_IPS
from .db import init_db, get_conn, now_iso, get_setting, set_setting
from .deps import client_ip
from .seed import seed_if_empty, sync_changelog
from .routers import auth, shop, cart, misc, admin, drops, botconsole, games, predictions, kickhook, blackjack, dm, mines

# Vypínač HER (piškvorky/duely/blackjack). Mimo provoz, když WEBOS_GAMES_OFF=1 (nastaveno ve fly.toml).
# Lokálně/testy (bez env) = hry zapnuté, ať projdou herní testy. Zpět do provozu: WEBOS_GAMES_OFF=0 + deploy.
GAMES_OFF = os.environ.get("WEBOS_GAMES_OFF", "0") == "1"

# Origin lock: pouští jen requesty přes Cloudflare (s tajným headerem). Vypnuto = fail-open,
# dokud není WEBOS_ORIGIN_SECRET nastaven (Fly secret). Pak přímý přístup na *.fly.dev bez
# klíče dostane 403. Revert = secret smazat. CF klíč přidává Transform Rule (Set X-Origin-Verify).
WEBOS_ORIGIN_SECRET = os.environ.get("WEBOS_ORIGIN_SECRET", "")
# VŽDY průchozí (Fly health-check chodí PŘÍMO na stroj, bez CF; + diagnostika):
_ORIGIN_LOCK_FREE = {"/api/health", "/api/monitor/healthz", "/api/_origin_check"}

# Inicializace databáze a ukázkových dat při startu
init_db()
seed_if_empty()
sync_changelog()     # novinky z kódu (seed.CHANGELOG) – nasyncuje jen nové; přidat novinku = řádek + deploy

# Načti IP bany do paměti (rychlá kontrola v middleware bez DB dotazu na každý request)
_c = get_conn()
try:
    ipban.load(_c)
    ddos.set_autoban(get_setting(_c, "ddos_autoban", "1") == "1")  # auto-ban toggle (přežije restart)
    maintenance.load(_c)   # údržbový režim (přežije restart/deploy)
finally:
    _c.close()
# Auto-záloha DB: 1× denně snapshot do data/backups/, retence 7 dní
start_backup_daemon()
# Auto-drop scheduler: spouští dropy samy v intervalu (když je zapnutý + live)
start_autodrop_daemon()
# Denní bezpečnostní/ekonomický digest na Discord (1× denně po ranní hodině)
start_digest_daemon()
# Achievementy: scanner uděluje odznaky podle statů (1× za 10 min, backfill při startu)
start_achievements_daemon()
# Partner Flash bonus: random obnova 'flash' odkazů + bot oznámí v chatu (jen když live)
start_partners_flash_daemon()
# Live události: při startu streamu zapne Happy Hour (×násobič na X min) + oznámí v chatu
start_live_events_daemon()
# Auto-úklid objednávek: maže vyřízené starší 30 dní (ať tabulka nebobtná) + alert na hromadu čekajících
start_order_cleanup_daemon()
# Výročí v komunitě: bonus + notifikace při překročení milníku (1m/3m/půlrok/1r/2r)
start_anniversary_daemon()

# Jednorázově: přechod na desku 9×9 → vrať vklady u zbylých rozehraných (staré velikosti) her.
# Flag v app_settings, ať to neběží při každém restartu (jinak by rušilo i nové 9×9 hry).
_g = get_conn()
try:
    if get_setting(_g, "games_board_v", "") != "9":
        _n = games.cancel_inprogress_refund(_g)
        set_setting(_g, "games_board_v", "9")
        _g.commit()
        if _n:
            print(f"[games] přechod na 9×9: vráceno {_n} rozehraných her")
finally:
    _g.close()

# Jednorázově: stávající subi (is_sub=1) s rolí 'user' → povýšit roli na 'sub' (ať se SUB ukáže i v roli).
_s = get_conn()
try:
    if get_setting(_s, "subs_role_backfill", "") != "done":
        _sn = _s.execute("UPDATE users SET role = 'sub' WHERE is_sub = 1 AND role = 'user'").rowcount
        set_setting(_s, "subs_role_backfill", "done")
        _s.commit()
        if _sn:
            print(f"[subs] backfill: role 'sub' u {_sn} stávajících subů")
finally:
    _s.close()

# Jednorázově: staré importované suby (is_sub=1, role 'sub') BEZ data expirace → nahodit +32 dní,
# ať se „re-validují". Aktivní se do měsíce obnoví Kick renewalem (přepíše datum reálným),
# neaktivní za 32 dní vyprší. Ruční odznaky (role 'user'/staff) schválně vynecháváme.
# Revert: do app_settings ukládáme {sentinel, ids} → stačí vrátit ta ID na NULL.
_lb = get_conn()
try:
    if not get_setting(_lb, "subs_legacy_backfill_v1", ""):
        _sentinel = (datetime.now(timezone.utc) + timedelta(days=32)).isoformat()
        _ids = [r["id"] for r in _lb.execute(
            "SELECT id FROM users WHERE is_sub = 1 AND sub_expires_at IS NULL AND role = 'sub'"
        ).fetchall()]
        if _ids:
            _lb.execute(
                "UPDATE users SET sub_expires_at = ? "
                "WHERE is_sub = 1 AND sub_expires_at IS NULL AND role = 'sub'",
                (_sentinel,),
            )
        set_setting(_lb, "subs_legacy_backfill_v1", json.dumps({"sentinel": _sentinel, "ids": _ids}))
        _lb.commit()
        print(f"[subs] legacy backfill: {len(_ids)} subu -> expirace {_sentinel}")
finally:
    _lb.close()

# Jednorázově: dorovnej u stávajících objednávek snapshot jména odměny (orders.product_name),
# ať po smazání odměny nezmizí jméno z historie. Osiřelé (produkt už neexistuje) zůstanou prázdné.
_ob = get_conn()
try:
    if not get_setting(_ob, "orders_name_backfill_v1", ""):
        _on = _ob.execute(
            "UPDATE orders SET product_name = (SELECT name FROM products WHERE products.id = orders.product_id) "
            "WHERE (product_name IS NULL OR product_name = '') AND product_id IN (SELECT id FROM products)"
        ).rowcount
        set_setting(_ob, "orders_name_backfill_v1", "done")
        _ob.commit()
        if _on:
            print(f"[orders] backfill jmen: {_on} objednavek")
finally:
    _ob.close()


# Data-level pojistka pro JEDNORÁZOVÉ migrace níže. Normálně je hlídá flag v app_settings
# (get_setting). Kdyby se flag ZTRATIL (restore DB ze starší zálohy, wipe/restore app_settings),
# migrace by se spustila ZNOVU a podruhé odečetla earned_total / přepsala zálohu / znovu vynulovala
# level. Tahle funkce to pozná přímo z DAT: když cílová záloha už má řádky, migrace na těchto datech
# proběhla → přeskoč ji i bez flagu. (marker_table je vždy literál z kódu, ne vstup → bez injection.)
def _migr_done(conn, marker_table: str) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (marker_table,)).fetchone():
        return False                                  # tabulka ještě neexistuje → migrace neproběhla
    return conn.execute(f"SELECT 1 FROM {marker_table} LIMIT 1").fetchone() is not None


# Jednorázově: dopočítej earned_total (lifetime nafarmeno) = součet KLADNÝCH points_log.
# → veteráni rovnou dostanou level podle historie. Aditivní, žádná data se nemažou.
# Pojistka: běží těsně PŘED level_reset_v1; když už level_reset_backup existuje (reset proběhl),
# proběhl i tenhle backfill → po ztrátě flagu ho NEPOUŠTĚJ znovu (je to ABSOLUTNÍ přepis – vrátil by
# earned_total zpět na lifetime součet a zničil reset + gift/BP opravy + opravu over-deduction).
_xp = get_conn()
try:
    if not get_setting(_xp, "earned_total_backfill_v1", "") and not _migr_done(_xp, "level_reset_backup"):
        _xp.execute(
            "UPDATE users SET earned_total = COALESCE("
            "(SELECT SUM(change) FROM points_log WHERE points_log.user_id = users.id AND change > 0), 0)")
        set_setting(_xp, "earned_total_backfill_v1", "done")
        _xp.commit()
        print("[xp] earned_total backfill hotov (level z historie)")
finally:
    _xp.close()

# Jednorázově (na žádost): RESET levelů – všichni od úrovně 1. Sedláci/body se NEMAŽOU,
# nuluje se JEN lifetime XP (earned_total). Záloha předem do level_reset_backup → revert = restore.
# Battle Pass baseline aktuální sezóny → 0 (jinak by pass zamrznul: počítá earned_total − baseline);
# už vyzvednuté tiery zůstávají (žádný double-pay). Idempotentní (guard setting). Aditivně přidá
# i changelog hlášku hráčům, ať je jim jasné proč jim spadl level.
_lr = get_conn()
try:
    if not get_setting(_lr, "level_reset_v1", "") and not _migr_done(_lr, "level_reset_backup"):
        from .db import local_date
        _lr.execute("CREATE TABLE IF NOT EXISTS level_reset_backup ("
                    "user_id INTEGER PRIMARY KEY, earned_total INTEGER NOT NULL)")
        # OR IGNORE (ne REPLACE): zálohu pravého PŘED-reset stavu nikdy nepřepiš už-resetovanou hodnotou.
        _lr.execute("INSERT OR IGNORE INTO level_reset_backup (user_id, earned_total) "
                    "SELECT id, earned_total FROM users")
        _lr.execute("UPDATE users SET earned_total = 0")
        _lr.execute("UPDATE battlepass SET baseline = 0 WHERE season = ?", (local_date()[:7],))
        _lr.execute(
            "INSERT INTO patch_notes (title, body, tag, published, created_at) VALUES (?, ?, ?, ?, ?)",
            ("🏆 Level Pass startuje – všichni od úrovně 1!",
             "Spustili jsme Level Pass a s ním férový restart: všem se úroveň vynulovala na 1. "
             "Tvoje sedláky, skiny i odměny zůstávají! 🌾 Závod o milníky (a 🔪 nožík na úrovni 100) "
             "teď začíná úplně od začátku – kdo nejvíc farmí, vyhrává. 💪",
             "🏆 Level Pass", 1, now_iso()))
        set_setting(_lr, "level_reset_v1", "done")
        _lr.commit()
        print("[lvl] reset levelu hotov - vsichni od 1, body netknute, zaloha v level_reset_backup")
finally:
    _lr.close()

# Jednorazove: Exchange/dary omylem pridavaly XP do earned_total (a tim i Battle Pass).
# Body zustavaji, opravuje se jen XP/level progress. Idempotentni + zaloha pred upravou.
_gx = get_conn()
try:
    if not get_setting(_gx, "gift_xp_repair_v1", "") and not _migr_done(_gx, "gift_xp_repair_backup"):
        _gx.execute("CREATE TABLE IF NOT EXISTS gift_xp_repair_backup ("
                    "user_id INTEGER PRIMARY KEY, earned_total_before INTEGER NOT NULL, "
                    "gift_xp INTEGER NOT NULL, created_at TEXT NOT NULL)")
        _gx.execute(
            "INSERT OR IGNORE INTO gift_xp_repair_backup "
            "(user_id, earned_total_before, gift_xp, created_at) "
            "SELECT u.id, COALESCE(u.earned_total,0), COALESCE(g.gift_xp,0), ? "
            "FROM users u JOIN ("
            "  SELECT user_id, SUM(change) AS gift_xp FROM points_log "
            "  WHERE change > 0 AND reason LIKE 'Dar od %' GROUP BY user_id"
            ") g ON g.user_id = u.id",
            (now_iso(),))
        _gx.execute(
            "UPDATE users SET earned_total = MAX(0, COALESCE(earned_total,0) - COALESCE(("
            "  SELECT SUM(change) FROM points_log "
            "  WHERE points_log.user_id = users.id AND change > 0 AND reason LIKE 'Dar od %'"
            "),0))")
        set_setting(_gx, "gift_xp_repair_v1", "done")
        _gx.commit()
        print("[xp] gift XP repair hotov - Dar od se nepocita do earned_total")
finally:
    _gx.close()

# Jednorazove: Battle Pass odmeny nesmi samy odemykat dalsi Battle Pass tiery.
# Body z claimu zustavaji, opravuje se jen XP omylem pridane pres earned_total.
_bpx = get_conn()
try:
    if not get_setting(_bpx, "battlepass_reward_xp_repair_v1", "") and not _migr_done(_bpx, "battlepass_xp_repair_backup"):
        _bpx.execute("CREATE TABLE IF NOT EXISTS battlepass_xp_repair_backup ("
                     "user_id INTEGER PRIMARY KEY, earned_total_before INTEGER NOT NULL, "
                     "bp_xp INTEGER NOT NULL, created_at TEXT NOT NULL)")
        _bpx.execute(
            "INSERT OR IGNORE INTO battlepass_xp_repair_backup "
            "(user_id, earned_total_before, bp_xp, created_at) "
            "SELECT u.id, COALESCE(u.earned_total,0), COALESCE(b.bp_xp,0), ? "
            "FROM users u JOIN ("
            "  SELECT user_id, SUM(change) AS bp_xp FROM points_log "
            "  WHERE change > 0 AND lower(reason) LIKE 'battle pass%' GROUP BY user_id"
            ") b ON b.user_id = u.id",
            (now_iso(),))
        _bpx.execute(
            "UPDATE users SET earned_total = MAX(0, COALESCE(earned_total,0) - COALESCE(("
            "  SELECT SUM(change) FROM points_log "
            "  WHERE points_log.user_id = users.id AND change > 0 AND lower(reason) LIKE 'battle pass%'"
            "),0))")
        set_setting(_bpx, "battlepass_reward_xp_repair_v1", "done")
        _bpx.commit()
        print("[xp] battlepass reward XP repair hotov - claim odmeny nedava XP")
finally:
    _bpx.close()

# Jednorázově: import ručně dodaných tiketů do tomboly Navaja (ghost účty bez Kicku).
# Idempotentní (flag navaja_import_v1) + plně vratné (navaja_import.undo). Viz modul.
_nv = get_conn()
try:
    _nres = navaja_import.run(_nv)
    if _nres.get("tickets_added"):
        print(f"[navaja] import: +{_nres['tickets_added']} tiketu, "
              f"{_nres['accounts_created']} novych uctu (produkt #{_nres['product_id']})")
    for _a in navaja_import.apply_adjustments(_nv):
        print(f"[navaja] uprava {_a['key']}: {_a['nick']} +{_a['added']}/-{_a['removed']}")
finally:
    _nv.close()

# Jednorázově: import ručně dodaných tiketů do tomboly AWP | Printstream (giveaway log).
# DOČASNĚ VYPNUTO (na žádost 2026-06-17): AWP import se při bootu NEspouští.
#   - na prod už proběhl dřív (v316), takže flag awp_import_v1 je stejně nastavený;
#   - tohle je pojistka, aby se za žádných okolností nepřidaly tikety znovu.
# Až bude chtít znovu aplikovat / nově: odkomentuj blok níž + redeploy.
# Idempotentní (flag awp_import_v1) + plně vratné (awp_import.undo). Viz modul.
# _aw = get_conn()
# try:
#     _ares = awp_import.run(_aw)
#     if _ares.get("tickets_added"):
#         print(f"[awp] import: +{_ares['tickets_added']} tiketu, "
#               f"{_ares['accounts_created']} novych uctu (produkt #{_ares['product_id']})")
#     for _a in awp_import.apply_adjustments(_aw):
#         print(f"[awp] uprava {_a['key']}: {_a['nick']} +{_a['added']}/-{_a['removed']}")
# finally:
#     _aw.close()

# Sezónní šampioni: na přelomu měsíce grantne TOP 3 minulé sezóny exkluzivní rámeček.
# Flag-gated + start-baseline (bez retroaktivity) – první grant až po konci sezóny, ve které se nasadilo.
_sc = get_conn()
try:
    _scr = season_award.run(_sc)
    if _scr.get("awarded"):
        print(f"[season] sampioni {_scr.get('season')}: ramecek pro {_scr['awarded']} hracu")
finally:
    _sc.close()

# Jednorázově: hry dány mimo provoz → vrať zamčené vklady (otevřené+rozehrané piškvorky, otevřené duely).
# Revert (až hry zase pojedou): GAMES_OFF=False níže + smaž flag 'games_off_refund_v1'.
_go = get_conn()
try:
    if GAMES_OFF and get_setting(_go, "games_off_refund_v1", "") != "done":
        _gn = games.cancel_inprogress_refund(_go)
        _dn = games.refund_open_duels(_go)
        set_setting(_go, "games_off_refund_v1", "done")
        _go.commit()
        if _gn or _dn:
            print(f"[games] mimo provoz: vraceno {_gn} her + {_dn} duelu")
finally:
    _go.close()

# Jednorázově: refund za zrušené kosmetické kousky (v1 bannery vypadaly špatně) – vrátí sedláky
# komu je koupil + sundá nasazené. Flag v app_settings, ať to neběží při každém startu.
_cb = get_conn()
try:
    from . import cosmetics
    if get_setting(_cb, "cosmetics_banner_refund_v1", "") != "done":
        _rn = cosmetics.refund_removed(_cb)
        set_setting(_cb, "cosmetics_banner_refund_v1", "done")
        _cb.commit()
        if _rn:
            print(f"[cosmetics] refund zrusenych banneru: {_rn} polozek")
finally:
    _cb.close()

# Na Fly (produkce) vypneme veřejné API docs/schema – ať se útočníkovi nenabízí mapa API.
# Lokálně (bez FLY_APP_NAME) zůstávají zapnuté pro vývoj.
_PROD = bool(os.environ.get("FLY_APP_NAME"))
app = FastAPI(
    title="WebOS – bodový shop",
    docs_url=None if _PROD else "/api/docs",
    redoc_url=None,
    openapi_url=None if _PROD else "/api/openapi.json",
)


# Bezpečnostní hlavičky pro všechny odpovědi (levná pojistka proti sniffingu/clickjackingu)
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}
if _PROD:
    _SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


def _csp(script_src: str) -> str:
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        f"script-src {script_src}; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "upgrade-insecure-requests"
    )


# CSP se nastavuje per-request (viz security_headers). Hlavní SPA běží STRICT: script-src 'self',
# tj. ŽÁDNÝ inline JS (app.js nemá inline on*= ani <script> bloky) → tvrdší obrana proti XSS
# z uživatelského obsahu. Overlaye (/overlay/*) a údržbová stránka mají vlastní inline <script>,
# pro ně držíme RELAXED ('unsafe-inline'). style-src 'unsafe-inline' zůstává všude (inline styly v UI).
_CSP_STRICT = _csp("'self'")
_CSP_RELAXED = _csp("'self' 'unsafe-inline'")


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PRED_ADMIN_SUFFIXES = ("/lock", "/unlock", "/resolve", "/cancel")


def _csrf_protected_path(request: Request) -> bool:
    """Mutační admin/staff endpointy musí přijít ze stejného originu jako web."""
    path = request.url.path
    if path.startswith("/api/admin/") and request.method in _UNSAFE_METHODS:
        return True
    # Historická pojistka: maintenance umí i GET s ?to=..., UI používá POST.
    if path == "/api/admin/maintenance" and request.query_params.get("to"):
        return True
    if request.method in _UNSAFE_METHODS and path == "/api/predictions":
        return True
    if request.method in _UNSAFE_METHODS and path.startswith("/api/predictions/"):
        return any(path.endswith(s) for s in _PRED_ADMIN_SUFFIXES)
    return False


def _same_origin(request: Request, value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    req_host = (request.headers.get("host") or "").lower()
    return bool(req_host and (parsed.netloc or "").lower() == req_host)


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """Produkční CSRF pojistka pro admin/staff mutace.

    SameSite=Lax už hodně pomáhá, ale tady navíc odmítneme mutační admin request,
    který nepřišel ze stejného webu (Origin/Referer musí sedět na aktuální host).
    Lokálně je vypnuto, aby šly dál jednoduché testy a vývoj přes HTTP.
    """
    if _PROD and _csrf_protected_path(request):
        origin = request.headers.get("origin") or ""
        referer = request.headers.get("referer") or ""
        if not ((_same_origin(request, origin) if origin else False)
                or (_same_origin(request, referer) if referer else False)):
            return JSONResponse(status_code=403, content={"detail": "Neplatný původ požadavku."})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if _PROD:
        # Overlaye + údržbová stránka mají inline <script> → RELAXED CSP; zbytek (hlavně SPA) STRICT.
        # Údržbovou stránku poznáme podle X-Maintenance (servíruje se i na "/"), overlaye podle cesty.
        relaxed = (response.headers.get("X-Maintenance") == "1"
                   or request.url.path.startswith("/overlay/")
                   or request.url.path == "/maintenance.html")
        response.headers.setdefault("Content-Security-Policy", _CSP_RELAXED if relaxed else _CSP_STRICT)
    # SPA shell (index.html na "/") vždy revalidovat → ?v= busting se projeví hned a
    # uživatelům nezůstane viset stará verze app.js/styles.css v cache (bez toho prohlížeč
    # heuristicky cachoval index.html a držel starý kód i po deployi). Assety s ?v= se cacheovat smí.
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif request.url.path.endswith((".js", ".css")):
        # Verzované assety (?v=) → cacheovat natvrdo (nový deploy = nové ?v= = nová URL),
        # ať se app.js/styles.css nestahuje a nerevaliduje při každé navigaci.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.url.path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico")):
        # Obrázky (produktové, maint slideshow, ikony) mají stabilní cesty → ať si je
        # browser/Cloudflare drží a netahají se z workeru pořád dokola. Týden stačí.
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


def _is_staff_request(request) -> bool:
    """Je request od přihlášeného staffa (admin/broadcaster/mod)? Ti se NIKDY neautobanují
    – jinak aktivní admin (vyřizování objednávek = spousta requestů) trefí DDoS práh a
    zabanuje si sám sebe. Krátký dotaz mimo Depends; volá se jen když IP překročí práh."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        conn = get_conn()
        try:
            sess = conn.execute("SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
            if not sess or sess["expires_at"] < now_iso():
                return False
            u = conn.execute("SELECT role FROM users WHERE id = ?", (sess["user_id"],)).fetchone()
            return bool(u and u["role"] in STAFF_ROLES)
        finally:
            conn.close()
    except Exception:
        return False


@app.middleware("http")
async def ip_ban_guard(request: Request, call_next):
    """Zabanovaná IP nedostane appku vůbec – vrátí se blokační stránka (403).

    Navíc lehká detekce náporu: počítá JEN reálné klientské IP (Fly-Client-IP) a při
    výrazném překročení prahu dá IP krátký dočasný auto-ban (vše v paměti, žádné DB
    na hot-path). Interní Fly proxy ani loopback se nikdy nepočítají ani nebanují.
    """
    # Kick webhook je ověřený podpisem – vyřaď z IP banů i DDoS počítání
    # (jinak by chat-heavy stream mohl zabanovat Kickovu IP a eventy by ustaly).
    if request.url.path == "/api/kick/webhook":
        return await call_next(request)
    ip = client_ip(request)
    rec = ipban.check(ip)
    if rec is not None:
        return ipban.block_page(ip, rec)
    if request.headers.get("fly-client-ip"):
        rate = ddos.observe(ip)
        if (ddos.autoban_enabled() and rate > ddos.AUTOBAN_PER_MIN
                and ip not in TRUSTED_IPS and not _is_staff_request(request)):
            if ipban.temp_ban(ip, f"Auto: nápor (>{ddos.AUTOBAN_PER_MIN} req/min)", ddos.AUTOBAN_MINUTES):
                ddos.note_autoban(ip, rate, now_iso())
                alerts.send(
                    "Auto-ban IP pri naporu",
                    detail=f"ip={ip}\nrate={rate}/min\nban={ddos.AUTOBAN_MINUTES} min",
                    key=f"ddos-autoban:{ip}",
                    cooldown=600,
                    ping=True,
                )
            blocked = ipban.check(ip)
            if blocked is not None:
                return ipban.block_page(ip, blocked)
    return await call_next(request)

# Údržbový režim: když je zapnutý, běžní návštěvníci dostanou údržbovou stránku
# (a API vrací 503), ale JEN admin (vlastník) vidí web normálně a může testovat.
# Pojistka proti zamčení: /api/health, /api/auth/*, /api/admin/* (to hlídá
# admin_guard) a Kick webhook projdou VŽDY → admin může režim vypnout i přes
# /api/admin/maintenance?to=off, i kdyby SPA nešlo načíst.
@app.middleware("http")
async def maintenance_guard(request: Request, call_next):
    if not maintenance.is_on():
        return await call_next(request)
    path = request.url.path
    if (path == "/api/health" or path == "/api/monitor/healthz" or path == "/api/_origin_check"
            or path.startswith("/api/auth/")
            or path.startswith("/api/admin/") or path == "/api/kick/webhook"
            or path == "/maintenance.png" or path == "/og-image.png"
            or path.startswith("/maint-")):   # health + maintenance obrázky/slideshow (/maint-N.jpg) musí projít i návštěvníkům
        return await call_next(request)
    if maintenance.bypasses_maintenance(request):
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/uploads/"):
        return JSONResponse(
            status_code=503,
            content={"detail": "Probíhá údržba. Web se brzy vrátí. 🛠️"},
            headers={"X-Maintenance": "1", "Retry-After": "300"},
        )
    # Statické assety (JS/CSS/fonty/mapy) pouštíme i během údržby. Jsou neškodné –
    # SPA stejně nefunguje bez API (vrací 503) a návštěvník dál vidí údržbu na "/".
    # Hlavní důvod: kdyby /app.js vracelo údržbové HTML, Cloudflare si ho zacachuje
    # pod URL assetu (.js → veřejná cache) a po vypnutí údržby servíruje HTML místo
    # JS → rozbitý web. Servírujeme tedy skutečný soubor (cache pak drží správný obsah).
    if path.endswith((".js", ".css", ".woff", ".woff2", ".ttf", ".map")):
        return await call_next(request)
    return HTMLResponse(content=maintenance.page_html(), status_code=200,
                        headers={"X-Maintenance": "1"})


@app.middleware("http")
async def origin_lock(request: Request, call_next):
    """Pustí jen provoz přes Cloudflare (tajný header). Přímý přístup na *.fly.dev → 403.
    Aktivní jen když je WEBOS_ORIGIN_SECRET nastaven; /api/health ap. jsou vždy průchozí."""
    if WEBOS_ORIGIN_SECRET and request.url.path not in _ORIGIN_LOCK_FREE:
        if request.headers.get("x-origin-verify") != WEBOS_ORIGIN_SECRET:
            return JSONResponse(status_code=403,
                                content={"detail": "Přímý přístup k serveru blokován (origin lock)."})
    return await call_next(request)


# Alert na neošetřené chyby (500) → Discord (pokud je webhook), pak vrátí čistý 500.
@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception):
    msg = str(exc)[:400]
    locked = "database is locked" in msg.lower()
    # „database is locked" je přechodná + při zátěži zasáhne víc cest naráz → jeden hrubý
    # klíč, delší cooldown, BEZ pingu (jinak zaplaví Discord, jak se stalo při výpadku).
    alerts.send("🔴 Chyba 500: " + request.method + " " + request.url.path,
                detail=type(exc).__name__ + ": " + msg,
                key=("500:db-locked" if locked else "500:" + request.url.path),
                cooldown=(900 if locked else 300), ping=(not locked))
    return JSONResponse(status_code=500, content={"detail": "Něco se pokazilo, zkus to prosím znovu."})


def _games_off_guard():
    if GAMES_OFF:
        raise HTTPException(status_code=503, detail="Hry jsou dočasně mimo provoz. 🔧")


# API routery (vše pod /api)
app.include_router(auth.router, prefix="/api")
app.include_router(shop.router, prefix="/api")
app.include_router(cart.router, prefix="/api")
app.include_router(misc.router, prefix="/api")
app.include_router(drops.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(botconsole.router, prefix="/api")
app.include_router(games.router, prefix="/api", dependencies=[Depends(_games_off_guard)])
app.include_router(blackjack.router, prefix="/api", dependencies=[Depends(_games_off_guard)])
app.include_router(mines.router, prefix="/api", dependencies=[Depends(_games_off_guard)])
app.include_router(predictions.router, prefix="/api")
app.include_router(dm.router, prefix="/api")
app.include_router(kickhook.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/_origin_check")
def _origin_check(request: Request):
    """Diagnostika origin locku: chodí přes Cloudflare tajný header? Vrací jen booleany (ne secret)."""
    h = request.headers.get("x-origin-verify")
    return {"present": h is not None,
            "match": bool(WEBOS_ORIGIN_SECRET) and h == WEBOS_ORIGIN_SECRET,
            "lock_enabled": bool(WEBOS_ORIGIN_SECRET)}


@app.get("/api/monitor/healthz")
def monitor_healthz():
    """Hlubsi healthcheck pro externi monitoring: app + DB + dulezite provozni prepinace."""
    status = "ok"
    code = 200
    checks = {
        "app": "ok",
        "maintenance": "on" if maintenance.is_on() else "off",
        "alerts": "configured" if alerts.enabled() else "off",
    }
    conn = None
    try:
        conn = get_conn()
        conn.execute("SELECT 1").fetchone()
        checks["db"] = "ok"
    except Exception as exc:
        status = "fail"
        code = 503
        checks["db"] = "fail"
        checks["error"] = type(exc).__name__
    finally:
        if conn is not None:
            conn.close()
    return JSONResponse(status_code=code, content={
        "status": status,
        "time": now_iso(),
        "checks": checks,
    })


# Nahrané obrázky (trvalý disk) – mount PŘED catch-all "/", jinak by ho "/" pohltil
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Frontend (SPA) – musí být až za API routery
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

# Oznámení o startu/deployi (no-op bez nastaveného Discord webhooku).
alerts.send("🟢 ZURYS shop běží (start / deploy)", key="startup", cooldown=0)
