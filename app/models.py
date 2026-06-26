"""Pydantic schémata pro vstupní data (request body)."""
import ipaddress
import re
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_\-]+=*$")


def _validate_push_endpoint(v: str) -> str:
    """SSRF guard: https:// only, no private/loopback IP literals."""
    parsed = urlparse(v)
    if parsed.scheme != "https":
        raise ValueError("endpoint must use https://")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("endpoint has no host")
    if host.lower() in ("localhost",) or host.lower().endswith(".local"):
        raise ValueError("endpoint: private host not allowed")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass  # hostname, not an IP literal — accept
    else:
        if not addr.is_global:
            raise ValueError("endpoint: private/loopback IP not allowed")
    return v


def _normalize_email(value: str) -> str:
    value = (value or "").strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("Neplatný e-mail.")
    return value


# --- Autentizace ---
class RegisterIn(BaseModel):
    email: str
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return _normalize_email(v)


class LoginIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return _normalize_email(v)


class KickConnectIn(BaseModel):
    """Demo režim připojení Kick účtu – jen uživatelské jméno."""
    username: str = Field(min_length=2, max_length=32)


class TradeUrlIn(BaseModel):
    """Steam trade odkaz diváka (na ruční odeslání vyhraných skinů). Prázdný = smazat."""
    url: str = Field(default="", max_length=300)


class BanIn(BaseModel):
    banned: bool
    reason: Optional[str] = ""


class FingerprintIn(BaseModel):
    webdriver: bool = False
    fp: Optional[str] = ""


class RuleIn(BaseModel):
    enabled: Optional[bool] = None
    threshold: Optional[int] = None


class IpBanIn(BaseModel):
    ip: str = Field(min_length=3, max_length=64)
    reason: Optional[str] = ""
    hours: int = Field(default=1, ge=0, le=8760)   # 0 = trvale, max 1 rok


class IpUnbanIn(BaseModel):
    ip: str = Field(min_length=3, max_length=64)


# --- PvP hry (piškvorky) ---
class GameCreateIn(BaseModel):
    stake: int = Field(ge=1, le=10_000_000)   # libovolná sázka (kladná)


class GameMoveIn(BaseModel):
    cell: int = Field(ge=0)                    # index políčka 0..(BOARD*BOARD-1)


class DuelCreateIn(BaseModel):
    type: str = Field(pattern="^(coinflip|dice)$")   # rps přidám později (vlna 2)
    stake: int = Field(ge=1, le=10_000_000)


# --- Predikce (sázení bodů na výsledek, CS2) ---
class PredictionCreateIn(BaseModel):
    question: str = Field(min_length=3, max_length=200)
    options: List[str]                          # 2–4 možnosti
    game: str = Field(default="CS2", max_length=40)
    lock_seconds: int = Field(default=180, ge=0, le=3600)   # auto-lock za N s (0 = ručně, default 3 min)

    @field_validator("options")
    @classmethod
    def _opts(cls, v):
        cleaned = [str(o).strip() for o in (v or []) if str(o).strip()]
        if len(cleaned) < 2:
            raise ValueError("Zadej alespoň 2 možnosti.")
        if len(cleaned) > 4:
            raise ValueError("Možnosti mohou být nejvýše 4.")
        if any(len(o) > 60 for o in cleaned):
            raise ValueError("Možnost je příliš dlouhá (nejvýše 60 znaků).")
        return cleaned


class PredictionBetIn(BaseModel):
    option_id: int
    amount: int = Field(ge=1, le=10_000_000)


class PredictionResolveIn(BaseModel):
    option_id: int


# --- Kick bot (SedlakBOT) ---
class BotSendIn(BaseModel):
    content: str = Field(min_length=1, max_length=480)


class BotToggleIn(BaseModel):
    enabled: bool


class SimulateChatIn(BaseModel):
    """Demo/test: simulace zprávy v chatu od daného Kick nicku (→ odměna za aktivitu)."""
    kick_username: str = Field(min_length=2, max_length=64)


# --- Ekonomika (pasivní výdělek) ---
class EconomyIn(BaseModel):
    eco_pts_per_min: Optional[int] = Field(default=None, ge=0, le=1000)
    eco_sub_mult: Optional[int] = Field(default=None, ge=1, le=100)
    eco_vip_mult: Optional[int] = Field(default=None, ge=1, le=100)
    eco_chat_pts: Optional[int] = Field(default=None, ge=0, le=1000)
    eco_chat_cooldown_s: Optional[int] = Field(default=None, ge=1, le=3600)
    eco_daily_cap: Optional[int] = Field(default=None, ge=0, le=1000000)
    eco_games_cap: Optional[int] = Field(default=None, ge=0, le=10000000)
    eco_wager_cap: Optional[int] = Field(default=None, ge=0, le=10000000)
    eco_watch_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    eco_chat_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    eco_sub_pts: Optional[int] = Field(default=None, ge=0, le=1000000)
    eco_resub_pts: Optional[int] = Field(default=None, ge=0, le=1000000)
    eco_giftsub_pts: Optional[int] = Field(default=None, ge=0, le=1000000)
    eco_follow_pts: Optional[int] = Field(default=None, ge=0, le=1000000)


class LiveModeIn(BaseModel):
    """Režim detekce živého streamu: auto (Kick API) / on (vždy) / off (nikdy)."""
    mode: str = Field(pattern="^(auto|on|off)$")


# --- Import uživatelů ze staré platformy (zurys.store / Firebase) ---
class LegacyUserIn(BaseModel):
    nick: str
    points: int = 0
    is_sub: Optional[int] = 0
    is_vip: Optional[int] = 0


class LegacyImportIn(BaseModel):
    users: List[LegacyUserIn]


# --- Dropy (závod o kód) ---
class DropCreateIn(BaseModel):
    code: Optional[str] = None            # když None, vygeneruje se
    points: int = Field(ge=1)
    max_winners: int = Field(default=1, ge=1, le=1000)


class AutoDropIn(BaseModel):
    """Nastavení auto-drop scheduleru (posílají se jen měněná pole).

    Rozsahy „od–do" (*_max): web losuje náhodnou hodnotu mezi základem a *_max,
    ať diváci nemůžou drop načasovat. *_max == základ → fixní hodnota.
    """
    autodrop_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    autodrop_interval_min: Optional[int] = Field(default=None, ge=1, le=1440)
    autodrop_interval_max: Optional[int] = Field(default=None, ge=1, le=1440)
    autodrop_points: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    autodrop_points_max: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    autodrop_winners: Optional[int] = Field(default=None, ge=1, le=1000)
    autodrop_winners_max: Optional[int] = Field(default=None, ge=1, le=1000)
    autodrop_only_live: Optional[int] = Field(default=None, ge=0, le=1)


class QuestClaimIn(BaseModel):
    """Vyzvednutí odměny za splněný úkol."""
    key: str = Field(..., min_length=1, max_length=32)


class BattlePassClaimIn(BaseModel):
    """Vyzvednutí odměny za odemčený tier farmářského Battle Passu (premium = sub-only řada)."""
    tier: int = Field(..., ge=1, le=100)
    premium: bool = False


class LevelPassClaimIn(BaseModel):
    """Vyzvednutí milníku Level Passu (exkluzivní kosmetika za dosaženou úroveň)."""
    level: int = Field(..., ge=1, le=100)


class LoginCalClaimIn(BaseModel):
    """Vyzvednutí milníkového bonusu z login kalendáře (počet aktivních dní)."""
    milestone: int = Field(..., ge=1, le=31)


class GardenPlantIn(BaseModel):
    """Zasazení plodiny na záhon v zahrádce."""
    plot: int = Field(..., ge=0, le=20)
    crop: str = Field(..., min_length=1, max_length=24)


class GardenPlantAllIn(BaseModel):
    """Zasadí plodinu na všechny prázdné záhony."""
    crop: str = Field(..., min_length=1, max_length=24)


class GardenHarvestIn(BaseModel):
    """Sklizeň záhonu v zahrádce."""
    plot: int = Field(..., ge=0, le=20)


class DecorBuyIn(BaseModel):
    """Koupě dekorace zahrádky."""
    key: str = Field(..., min_length=1, max_length=24)


class CommunityGoalIn(BaseModel):
    """Nastavení komunitního chat cíle (posílají se jen měněná pole)."""
    enabled: Optional[int] = Field(default=None, ge=0, le=1)
    target: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    reward: Optional[int] = Field(default=None, ge=0, le=1_000_000)


class SubGoalIn(BaseModel):
    """Nastavení komunitního SUB cíle (posílají se jen měněná pole).
    target = KROK subů na tier, reward = odměna za tier, tier_max = strop tierů (0 = NEKONEČNO)."""
    enabled: Optional[int] = Field(default=None, ge=0, le=1)
    target: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    reward: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    tier_max: Optional[int] = Field(default=None, ge=0, le=1000)    # 0 = nekonečný žebříček


class ModApplyIn(BaseModel):
    """Přihláška na moderátora (vyplní přihlášený divák)."""
    age: str = Field(default="", max_length=10)
    discord: str = Field(min_length=2, max_length=64)
    timezone: str = Field(default="", max_length=64)
    experience: str = Field(default="", max_length=1500)
    hours_week: str = Field(default="", max_length=40)
    availability: str = Field(default="", max_length=200)
    motivation: str = Field(min_length=10, max_length=2000)
    watch_time: str = Field(default="", max_length=100)
    scenario_spam: str = Field(default="", max_length=2000)
    scenario_reward: str = Field(default="", max_length=2000)
    scenario_banevasion: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=1000)


class ModAppDecideIn(BaseModel):
    """Rozhodnutí admina o přihlášce na moda."""
    action: str = Field(pattern="^(accept|reject)$")
    set_mod: bool = False   # při accept rovnou nastavit roli 'mod'


class CosmeticIn(BaseModel):
    """Koupě / nasazení kosmetiky – klíč položky z katalogu."""
    key: str = Field(min_length=1, max_length=40)


class SelfExcludeIn(BaseModel):
    """Sebevyloučení ze sázek (Tipsport-style). duration: 1d | 7d | 30d | perm."""
    duration: str = Field(min_length=2, max_length=4)


class TimeoutIn(BaseModel):
    """Timeout (dočasný blok webu + Kick chatu). duration: 5m|15m|1h|6h|24h|7d|off."""
    duration: str = Field(min_length=2, max_length=3)


class DmIn(BaseModel):
    """Soukromá zpráva (PM) – tělo."""
    body: str = Field(min_length=1, max_length=2000)


class FairSeedIn(BaseModel):
    """Provably fair – nový client seed (prázdný = vygeneruje se náhodný)."""
    client_seed: str = Field(default="", max_length=64)


class ShopDiscountIn(BaseModel):
    """Happy-hour sleva na shop (admin). pct 0 = vypnuto; live_only 1 = jen když je live.
    sub_2x 1 = během happy hour dvojnásobné body za subs/gift subs."""
    pct: int = Field(ge=0, le=90)
    live_only: int = Field(default=0, ge=0, le=1)
    sub_2x: int = Field(default=0, ge=0, le=1)
    minutes: int = Field(default=0, ge=0, le=1440)   # časovač: 0 = bez limitu, jinak auto-vypnutí za N min (max 24 h)


class MinesStartIn(BaseModel):
    """Start Mines: sázka (1–5000) + počet bomb (3–24) v mřížce 5×5."""
    bet: int = Field(ge=1, le=5000)
    mines: int = Field(default=3, ge=3, le=24)


class MinesRevealIn(BaseModel):
    """Odkrytí pole (0–24) v aktivní hře Mines."""
    tile: int = Field(ge=0, le=24)


class MinesBanIn(BaseModel):
    """Admin: zabaň/odbaň uživatele JEN ve hře Mines (zbytek webu mu zůstává)."""
    username: str = Field(min_length=1, max_length=64)
    banned: bool = True


class ProfileBioIn(BaseModel):
    """Bio na profilu + vypíchnutá oblíbená hra (showcase)."""
    bio: str = Field(default="", max_length=160)
    fav_game: str = Field(default="", max_length=24)


class WagerLimitIn(BaseModel):
    """Denní limit sázek (responsible gaming). 0 = bez limitu. Snížit lze hned, zvýšit až zítra."""
    limit: int = Field(default=0, ge=0, le=100_000_000)


class BanClusterIn(BaseModel):
    """Hromadný ban clusteru účtů (alt farma). Staff/admin se přeskočí."""
    user_ids: list[int] = Field(default_factory=list)
    reason: str = Field(default="", max_length=200)


class DropClaimIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    hp: Optional[str] = ""        # honeypot – boti vyplní, lidé ne
    dwell: Optional[int] = 0      # ms od zobrazení banneru (anti-instant-bot)
    t0: Optional[int] = 0         # epoch ms při zobrazení formy (form timing)


# --- Nákup / košík ---
class PurchaseIn(BaseModel):
    product_id: int
    t0: Optional[int] = 0          # epoch ms při zobrazení formy


class CartItem(BaseModel):
    product_id: int
    qty: int = Field(default=1, ge=1, le=99)


class CartCheckoutIn(BaseModel):
    items: List[CartItem]
    t0: Optional[int] = 0          # epoch ms při zobrazení košíku


# --- Redeem ---
class RedeemIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    t0: Optional[int] = 0          # epoch ms při zobrazení formy


# --- Exchange: poslání sedláků kamarádovi ---
class GiftIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    amount: int = Field(ge=1, le=10_000_000)
    note: str = Field(default="", max_length=120)   # nepovinný důvod od odesílatele


# --- Admin: produkty ---
class SkinLookupIn(BaseModel):
    """Vyhledání obrázku CS2 skinu na Steam marketu podle názvu."""
    name: str = Field(min_length=2, max_length=120)


class SkinSearchIn(BaseModel):
    """Našeptávač skinů z lokálního katalogu (vrací víc shod s obrázky)."""
    query: str = Field(min_length=2, max_length=120)


class ImageUploadIn(BaseModel):
    """Nahrání obrázku odměny z PC – data URL (data:image/...;base64,...)."""
    data: str = Field(min_length=20, max_length=10_000_000)


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    image_url: Optional[str] = ""
    cost_points: int = Field(ge=0)
    category: Optional[str] = ""
    type: str = "instant"
    period: Optional[str] = ""          # giveaway perioda (daily/weekly/monthly/yearly/random)
    subs_only: bool = False
    vip_only: bool = False
    stock: int = -1
    description: Optional[str] = ""
    active: bool = True
    hot: bool = False                  # 🔥 zvýraznit – pin nahoru v shopu (nad nejnovější) + HOT odznak
    ends_at: Optional[str] = None      # ISO datum/čas „k dispozici do" (prázdné = bez limitu)
    max_per_person_pct: int = Field(default=0, ge=0, le=100000)  # tombola: max POČET ticketů na osobu (0 = neomezeno)


# --- Admin: uživatelé ---
class UserRoleIn(BaseModel):
    role: str


class UserFlagsIn(BaseModel):
    """Odznaky SUB / VIP / OG (nezávislé na roli – můžou být i víc naráz). Posílají se jen měněné."""
    is_sub: Optional[bool] = None
    is_vip: Optional[bool] = None
    is_og: Optional[bool] = None


class UserPointsIn(BaseModel):
    change: int
    reason: Optional[str] = "Úprava adminem"


# --- Admin: objednávky ---
class UserAdminMetaIn(BaseModel):
    watchlisted: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=1000)


class OrderStatusIn(BaseModel):
    status: str


class BroadcastIn(BaseModel):
    """Admin broadcast: in-app notifikace (zvoneček) segmentu uživatelů."""
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(default="", max_length=300)
    icon: str = Field(default="📣", max_length=8)
    link: str = Field(default="", max_length=80)
    segment: str = Field(default="all")        # all | active | subs


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=40, max_length=200)
    auth: str = Field(min_length=16, max_length=100)

    @field_validator("p256dh", "auth")
    @classmethod
    def _b64url(cls, v: str) -> str:
        if not _B64URL_RE.match(v):
            raise ValueError("must be base64url")
        return v


class PushSubIn(BaseModel):
    """Web Push subscription z prohlížeče (PushSubscription.toJSON())."""
    endpoint: str = Field(min_length=20, max_length=600)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v: str) -> str:
        return _validate_push_endpoint(v)


class ManualOrderIn(BaseModel):
    """Ruční ticket/objednávka v adminu (např. kompenzace za bug). NEúčtuje žádné body –
    jen založí záznam k vyřízení. Uživatele hledá podle nicku (kick_username/username)."""
    username: str = Field(min_length=2, max_length=64)
    product_name: str = Field(min_length=1, max_length=120)
    product_id: Optional[int] = None
    points_spent: Optional[int] = Field(default=0, ge=0, le=100_000_000)
    count: int = Field(default=1, ge=1, le=50)          # kolik ticketů (objednávek) vytvořit
    note: Optional[str] = Field(default=None, max_length=200)


class ManualOrderLineIn(BaseModel):
    """Jeden řádek hromadného ticketu – volnější (validace per-řádek až v endpointu,
    ať jeden špatný řádek neshodí celou dávku)."""
    username: str = ""
    product_name: str = ""
    product_id: Optional[int] = None
    points_spent: Optional[int] = 0
    count: Optional[int] = 1
    note: Optional[str] = None


class ManualOrderBulkIn(BaseModel):
    """Hromadné vytvoření ticketů (víc lidí naráz)."""
    items: list[ManualOrderLineIn] = []


# --- Admin: redeem kódy ---
class CodeGenIn(BaseModel):
    code: Optional[str] = None            # když None, vygeneruje se náhodně
    points_value: int = 0
    product_id: Optional[int] = None
    max_uses: int = Field(default=1, ge=1)
    expires_at: Optional[str] = None      # ISO datum, volitelně
    count: int = Field(default=1, ge=1, le=100)  # kolik kódů najednou


# --- Admin: patch notes / novinky (changelog) ---
class PatchNoteIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(default="", max_length=2000)
    tag: str = Field(default="new", pattern="^(new|improve|fix)$")   # 🆕 nové / 🛠️ vylepšení / 🐛 oprava
    published: bool = True


# --- Admin: partnerské/sponzorské odkazy (klikni za body) ---
class PartnerLinkIn(BaseModel):
    """Sponzorský/partnerský odkaz v Bonusech: klik → jednorázová odměna (1× za uživatele)."""
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=4, max_length=500)
    reward: int = Field(default=100, ge=0, le=1_000_000)
    icon: Optional[str] = Field(default="🤝", max_length=8)
    enabled: bool = True
    mode: str = Field(default="once", pattern="^(once|flash)$")   # 1× navždy / náhodný flash
    sort_order: int = Field(default=0, ge=0, le=999)


class PartnerFlashConfigIn(BaseModel):
    """Konfigurace Flash bonusu (náhodná obnova 'flash' odkazů + bot do chatu)."""
    pflash_enabled: Optional[int] = None
    pflash_interval_min: Optional[int] = None
    pflash_interval_max: Optional[int] = None
    pflash_window_min: Optional[int] = None
    pflash_only_live: Optional[int] = None


class RoomJoinIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)


class RoomBetIn(BaseModel):
    amount: int = Field(ge=1, le=1_000_000)


class RoomChatIn(BaseModel):
    msg: str = Field(min_length=1, max_length=200)


class CrewCreateIn(BaseModel):
    name: str = Field(min_length=3, max_length=32)
    tag: str = Field(min_length=2, max_length=4)


class CrewJoinIn(BaseModel):
    code: str = Field(min_length=1, max_length=24)


class CrewChatIn(BaseModel):
    msg: str = Field(min_length=1, max_length=200)


class CrewMemberIn(BaseModel):
    user_id: int


class CrewRoleIn(BaseModel):
    user_id: int
    role: str = Field(pattern="^(officer|member)$")


class CrewEmblemIn(BaseModel):
    emblem: str = Field(min_length=1, max_length=8)


class CrewMotdIn(BaseModel):
    text: str = Field(default="", max_length=200)


class CrewPrivateIn(BaseModel):
    private: bool


class GamesRakeIn(BaseModel):
    """Rake (% z banku pro house) na hrách/duelech. 0 = férové bez poplatku."""
    rake_pct: int = Field(ge=0, le=50)


class LiveHappyIn(BaseModel):
    """Happy Hour při startu streamu (posílají se jen měněná pole)."""
    livehappy_enabled: Optional[int] = None
    livehappy_mult: Optional[float] = Field(default=None, ge=1, le=10)
    livehappy_minutes: Optional[int] = Field(default=None, ge=1, le=720)


# --- Admin: úklid testovacích pohybů bodů ---
class PointsLogPurgeIn(BaseModel):
    """Smaže KONKRÉTNÍ řádky points_logu podle ID (úklid testovacích/omylem vytvořených pohybů).
    Pojistka confirm_reason: když je vyplněná, smažou se JEN řádky přesně s tímhle důvodem –
    aby nešlo omylem smáznout reálný ekonomický pohyb. Vše se loguje do admin auditu (reverze)."""
    ids: list[int] = Field(min_length=1, max_length=50)
    confirm_reason: Optional[str] = Field(default=None, max_length=40)
