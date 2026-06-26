/* ============================================================
   WebOS – věrnostní bodový shop pro streamera (frontend SPA)
   Vanilla JS, žádné závislosti.
============================================================ */
"use strict";

const API = "/api";

const state = {
  user: null,
  cart: loadCart(),
  kickMode: null,
  demoAdmin: null,
};

const shopState = { type: "all", subs: false, vip: false, afford: false, sort: null, page: 1, items: [], total: 0, hasMore: false };
const adminState = { tab: "products", orderFilter: "all", userQuery: "", userSort: "points", products: [],
  auditAction: "", auditAdmin: "", auditOffset: 0, auditLimit: 50,
  loginsQuery: "", loginsIp: "", loginsOffset: 0, loginsLimit: 60,
  secTab: "anticheat", pfQuery: "", pfFlow: "", pfMin: 0, pfReason: "", pfOffset: 0, pfLimit: 60 };

/* ---------------- Utility ---------------- */
function $(sel, root = document) { return root.querySelector(sel); }
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function fmtPts(n) { return Number(n).toLocaleString("cs-CZ") + " sedláků"; }
function userTier(rank) {   // titul podle POZICE na leaderboardu (rank); mimo TOP 100 = bez titulu
  if (!rank || rank < 1) return "";
  if (rank <= 3) return "Král";
  if (rank <= 10) return "Zeman";
  if (rank <= 30) return "Rychtář";
  if (rank <= 50) return "Hospodář";
  if (rank <= 100) return "Nádeník";
  return "";
}
const RARITY = {
  milspec: ["MIL-SPEC", "#4b69ff"], restricted: ["RESTRICTED", "#8847ff"],
  classified: ["CLASSIFIED", "#d32ee6"], covert: ["COVERT", "#eb4b4b"], contraband: ["CONTRABAND", "#e4ae39"],
};
function rarityInfo(r) { return RARITY[r] || ["", "#6b7689"]; }
function countdown(iso) {
  if (!iso) return null;
  let s = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (s <= 0) return "KONEC";
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); const sec = s - m * 60;
  if (d > 0) return `${d}D ${h}H ${m}M`;
  if (h > 0) return `${h}H ${m}M ${sec}S`;
  return `${m}M ${sec}S`;
}
function requireTypedConfirm(message, phrase = "POTVRDIT") {
  const value = prompt(`${message}\n\nPro potvrzení napiš: ${phrase}`, "");
  return value === phrase;
}
function hashStr(s) { let h = 0; for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; } return Math.abs(h); }
// Stabilní otisk zařízení (anti-alt-farm + anti-bot u dropů). Deterministický → stejné zařízení = stejný fp.
function deviceFingerprint() {
  try {
    const p = [navigator.userAgent, navigator.language, navigator.platform || "",
      screen.width + "x" + screen.height + "x" + (screen.colorDepth || ""),
      (Intl.DateTimeFormat().resolvedOptions().timeZone) || "",
      navigator.hardwareConcurrency || "", navigator.deviceMemory || ""];
    try {
      const c = document.createElement("canvas"), x = c.getContext("2d");
      x.textBaseline = "top"; x.font = "14px Arial"; x.fillStyle = "#069"; x.fillText("zurys✨fp", 2, 2);
      p.push(c.toDataURL().slice(-48));
    } catch (e) {}
    return "fp" + hashStr(p.join("|")).toString(36);
  } catch (e) { return ""; }
}

function timeAgo(iso) {
  const d = new Date(iso);
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 45) return "právě teď";
  const m = Math.floor(s / 60); if (m < 60) return `před ${m} min`;
  const h = Math.floor(m / 60); if (h < 24) return `před ${h} h`;
  const days = Math.floor(h / 24); if (days < 7) return `před ${days} dny`;
  return d.toLocaleDateString("cs-CZ");
}

const CAT_EMOJI = {
  "Na streamu": "🎥", "Hudba": "🎵", "Komunita": "💬", "Discord": "🎧", "Sranda": "😂",
  "Gameplay": "🎮", "Fyzické": "📦", "Status": "⭐", "Emote": "😎", "Merch": "👕",
  "Tombola": "🎟️", "Směnárna": "💱", "Nože": "🔪", "Hardware": "🖱️", "Zbraně": "🔫",
};
function emojiFor(p) { if (p.type === "raffle") return "🎟️"; return CAT_EMOJI[p.category] || "🎁"; }
function gradFor(p) { const hue = hashStr(p.name || "x") % 360; return `linear-gradient(135deg, hsl(${hue} 60% 22%), hsl(${(hue + 50) % 360} 55% 13%))`; }

const TYPE_LABEL = { instant: "Instantní", short: "Krátká", long: "Delší", yearly: "Roční", raffle: "Tombola" };
const PERIOD_LABEL = { daily: "☀️ Denní", weekly: "📅 Týdenní", monthly: "🌙 Měsíční", yearly: "🎆 Roční", random: "🎲 Random" };
const PERIOD_OPTIONS = [["", "— žádná —"], ["daily", "☀️ Denní"], ["weekly", "📅 Týdenní"], ["monthly", "🌙 Měsíční"], ["yearly", "🎆 Roční"], ["random", "🎲 Random"]];

function avatarHTML(name, url, cls = "", frameCls = "") {
  const fc = frameCls ? " " + frameCls : "";
  if (url) return `<div class="avatar ${cls}${fc}"><img src="${esc(url)}" alt=""></div>`;
  const initials = (name || "?").trim().slice(0, 2).toUpperCase();
  const hue = hashStr(name || "x") % 360;
  return `<div class="avatar ${cls}${fc}" style="background:linear-gradient(135deg,hsl(${hue} 62% 46%),hsl(${(hue + 40) % 360} 62% 34%))">${esc(initials)}</div>`;
}
/* nasazená kosmetika z payloadu (cos: {name, frame, banner} = CSS třídy) */
function cosF(o) { return (o && o.cos && o.cos.frame) || ""; }
function cosN(o) { return (o && o.cos && o.cos.name) || ""; }
function cosB(o) { return (o && o.cos && o.cos.banner) || ""; }
function uLink(name) {
  const n = (name == null ? "" : String(name)).trim();
  return n ? `<a class="prof-link" href="#/u/${encodeURIComponent(n)}">${esc(n)}</a>` : esc(name || "?");
}
function roleBadge(role) {
  const map = { admin: ["badge-admin", "🛡️", "Admin"], broadcaster: ["badge-admin", "👑", "Broadcaster"], mod: ["badge-vip-role", "🔨", "Moderátor"], vip: ["badge-vip-role", "💎", "VIP"], sub: ["badge-sub-role", "💜", "Sub"], user: ["badge-user-role", "👤", "Divák"] };
  const [cls, icon, label] = map[role] || map.user;
  // Admin = výrazně (ikona + text ADMIN + glow), ať na něj dávají bacha. Ostatní = jen emote + tooltip.
  if (role === "admin") {
    return `<span class="badge badge-role badge-admin-loud" data-tip="Admin" aria-label="Admin">${icon} ADMIN</span>`;
  }
  return `<span class="badge badge-role badge-emote ${cls}" data-tip="${label}" aria-label="${label}">${icon}</span>`;
}
// Odznáčky SUB / VIP / OG (Kick status – display only, nezávislé na roli). Můžou být i víc naráz.
function subVipBadges(u) {
  let s = "";
  if (u && u.is_og) s += `<span class="badge badge-role badge-emote badge-admin" data-tip="OG" aria-label="OG">🏅</span> `;
  if (u && u.is_sub) s += `<span class="badge badge-role badge-emote badge-sub-role" data-tip="Sub" aria-label="Sub">💜</span> `;
  if (u && u.is_vip) s += `<span class="badge badge-role badge-emote badge-vip-role" data-tip="VIP" aria-label="VIP">💎</span> `;
  return s;
}
// Pro leaderboard: staff dostane svůj badge + sub/vip; běžný divák jen sub/vip (jinak „Divák").
function lbBadges(r) {
  if (["admin", "broadcaster", "mod"].includes(r.role)) return roleBadge(r.role) + " " + subVipBadges(r);
  return subVipBadges(r) || roleBadge("user");
}

// Oprávnění (zrcadlo serverové matice v config.py – server to stejně vynucuje)
const ADMIN_SECTIONS = {
  overview: [], stats: ["broadcaster"], products: ["mod", "broadcaster"], users: ["mod", "broadcaster"], subs: [], orders: ["mod", "broadcaster"],
  raffles: ["broadcaster"], codes: ["broadcaster"], drops: ["broadcaster"], games: ["mod", "broadcaster"], bot: ["broadcaster"],
  predictions: ["mod", "broadcaster"], economy: ["broadcaster"], news: ["broadcaster"], security: [],
  modnabor: ["broadcaster"], gifts: ["broadcaster"],
};
function isStaff(u) { return !!u && ["admin", "broadcaster", "mod"].includes(u.role); }
function canDM(u) { return !!u && ["admin", "broadcaster"].includes(u.role); }   // PM jen broadcaster+admin (mod NE)
function canSection(u, sec) { return !!u && (u.role === "admin" || (ADMIN_SECTIONS[sec] || []).includes(u.role)); }

function thumbStyle(p) {
  if (p.image_url) return `style="background-image:url('${esc(p.image_url)}')"`;
  return `style="background:${gradFor(p)}"`;
}
function thumbInner(p) { return p.image_url ? "" : `<span class="emoji">${emojiFor(p)}</span>`; }

/* ---------------- API klient ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 503 && res.headers.get("X-Maintenance") && !window._maintReloaded) {
    window._maintReloaded = true;   // pojistka proti smyčce – po reloadu už server servíruje statickou údržbu
    location.reload();
    throw new Error("Probíhá údržba.");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* prázdná odpověď */ }
  if (!res.ok) {
    let msg = "Něco se pokazilo.";
    if (data && data.detail) {
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail) && data.detail[0]) msg = data.detail[0].msg || msg;
    }
    throw new Error(msg);
  }
  return data;
}

/* ---------------- Toasty ---------------- */
function toast(msg, type = "info") {
  const root = $("#toastRoot");
  const ico = type === "success" ? "✅" : type === "error" ? "⚠️" : "ℹ️";
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span class="ico">${ico}</span><span>${esc(msg)}</span>`;
  root.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateX(20px)"; el.style.transition = ".25s"; setTimeout(() => el.remove(), 260); }, 3600);
}

/* ---------------- Košík (localStorage) ---------------- */
function loadCart() { try { return JSON.parse(localStorage.getItem("webos_cart")) || []; } catch (e) { return []; } }
function saveCart() { localStorage.setItem("webos_cart", JSON.stringify(state.cart)); }
function cartCount() { return state.cart.reduce((s, i) => s + i.qty, 0); }
function cartTotal() { return state.cart.reduce((s, i) => s + i.cost * i.qty, 0); }
function addToCart(p) {
  const ex = state.cart.find((i) => i.id === p.id);
  if (ex) ex.qty += 1;
  else state.cart.push({ id: p.id, name: p.name, cost: p.cost_points, type: p.type, subs_only: p.subs_only, vip_only: p.vip_only, qty: 1 });
  saveCart(); renderHeader();
}
function changeQty(id, delta) {
  const it = state.cart.find((i) => i.id === id); if (!it) return;
  it.qty += delta; if (it.qty < 1) it.qty = 1; saveCart();
}
function removeFromCart(id) { state.cart = state.cart.filter((i) => i.id !== id); saveCart(); renderHeader(); }
function clearCart() { state.cart = []; saveCart(); renderHeader(); }

/* ---------------- Router ---------------- */
const NAV = [
  ["shop", "Shop"], ["leaderboard", "Žebříček"], ["exchange", "Směnárna"], ["faq", "FAQ"], ["redeem", "Kód"],
];
function parseRoute() { const h = location.hash.replace(/^#\/?/, "").split("?")[0]; const [name, param] = h.split("/"); return { name: name || "shop", param }; }
function currentRoute() { return parseRoute().name; }
function navigate(path) { const t = "#/" + path; if (location.hash === t) render(); else location.hash = t; }
function openDrawer() { $("#mobilenav").classList.add("open"); }
function closeDrawer() { $("#mobilenav").classList.remove("open"); }

function render() {
  renderHeader();
  closeDrawer();
  window._pageT0 = Date.now();   // form-timing anti-bot: timestamp loadu stránky
  if (dropTimer) { clearInterval(dropTimer); dropTimer = null; }
  if (cardTimer) { clearInterval(cardTimer); cardTimer = null; }
  if (gameTimer) { clearInterval(gameTimer); gameTimer = null; currentGameId = null; }
  if (gameClockTimer) { clearInterval(gameClockTimer); gameClockTimer = null; gameClockBase = null; }
  if (duelTimer) { clearInterval(duelTimer); duelTimer = null; }
  if (dmThreadTimer) { clearInterval(dmThreadTimer); dmThreadTimer = null; }
  stopPredPoll();
  const r = parseRoute();
  const pages = {
    shop: pageShop, leaderboard: pageLeaderboard, exchange: pageExchange, redeem: pageRedeem,
    faq: pageFaq, pravidla: pageRules, cart: pageCart, profile: pageProfile, admin: pageAdmin,
    predikce: pagePredikce, games: pageGames, novinky: pageNews, bonusy: pageBonusy, ukoly: pageUkoly,
    kosmetika: pageCosmetics, bj: pageBjRoom, zpravy: pageMessages, fair: pageFair, mines: pageMines,
    connect: pageConnect, login: pageConnect, register: pageConnect, u: pageUserProfile,
    "mod-nabor": pageModApply, staty: pageGameStats, "sin-slavy": pageHallOfFame, zahrada: pageGarden,
  };
  (pages[r.name] || pageShop)(r.param);
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

/* ---------------- Horní navigace (ZURYS) ---------------- */
let _lastBalance = null;
function animateBalance(target) {       // gamifikace: napočítej zůstatek old→new (jen při změně)
  const el = document.querySelector(".pts-num");
  if (!el) { _lastBalance = target; return; }
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const from = _lastBalance;
  _lastBalance = target;
  if (reduce || from === null || from === target) { el.textContent = Number(target).toLocaleString("cs-CZ"); return; }
  el.classList.remove("bal-up", "bal-down");
  el.classList.add(target > from ? "bal-up" : "bal-down");
  const delta = target - from;
  // [7] „+X / −X sedláků" floating popup u zůstatku
  const pill = el.closest(".pts-pill");
  if (pill) {
    const pop = document.createElement("span");
    pop.className = "pts-pop " + (delta > 0 ? "up" : "down");
    pop.textContent = (delta > 0 ? "+" : "−") + Math.abs(delta).toLocaleString("cs-CZ");
    pill.appendChild(pop);
    setTimeout(() => pop.remove(), 1400);
  }
  // [4] sedlák maskot oslaví při zisku (skok)
  if (delta > 0) {
    document.querySelectorAll(".hero-sedlak, .page-mascot").forEach((m) => {
      m.classList.remove("cheer"); void m.offsetWidth; m.classList.add("cheer");
      setTimeout(() => m.classList.remove("cheer"), 900);
    });
  }
  const dur = 700, t0 = performance.now();
  const tick = (now) => {
    const p = Math.min(1, (now - t0) / dur);
    const v = Math.round(from + (target - from) * (1 - Math.pow(1 - p, 3)));
    el.textContent = v.toLocaleString("cs-CZ");
    if (p < 1) requestAnimationFrame(tick);
    else setTimeout(() => el.classList.remove("bal-up", "bal-down"), 250);
  };
  requestAnimationFrame(tick);
}
let _lastLevel = null;
function checkLevelUp(level) {        // gamifikace 1: oslava při level-upu
  const prev = _lastLevel;
  _lastLevel = level;
  if (prev === null || !level || level <= prev) return;
  try { confettiBurst(); } catch (e) {}
  document.querySelectorAll(".hero-sedlak, .page-mascot").forEach((m) => {
    m.classList.remove("cheer"); void m.offsetWidth; m.classList.add("cheer");
    setTimeout(() => m.classList.remove("cheer"), 900);
  });
  toast(`🆙 LEVEL UP! Jsi Level ${level}! 🌾`, "success");
}
function renderHeader() {
  const route = currentRoute();
  const u = state.user;
  document.body.classList.toggle("logged-in", !!u);   /* na mobilu uvolní místo v topbaru (skryje wordmark) */
  const items = [["shop", "Shop"], ["bonusy", "Bonusy"], ["ukoly", "Úkoly"], ["zahrada", "Zahrádka"], ["leaderboard", "Žebříček"], ["exchange", "Směnárna"], ["games", "Hry"], ["predikce", "Predikce"]];
  const navDot = (k) => ((k === "bonusy" && u && bonusReady) || (k === "ukoly" && u && questReady)) ? `<span class="nav-dot" title="Máš nevyzvednutou odměnu!"></span>` : "";
  const navLinks = items.map(([k, l]) => `<a href="#/${k}" class="nav-link ${route === k ? "active" : ""}">${l}${navDot(k)}</a>`).join("")
    + (isStaff(u) ? `<a href="#/admin" class="nav-link ${route === "admin" ? "active" : ""}">${u.role === "admin" ? "Admin" : "Panel"}</a>` : "");

  const cartBtn = `<button class="icon-btn" data-action="nav" data-href="cart" title="Košík">🛒${cartCount() ? `<span class="cart-badge">${cartCount()}</span>` : ""}</button>`;
  const msgBtn = u ? `<button class="icon-btn" data-action="nav" data-href="zpravy" title="Zprávy">✉️${u.dm_unread ? `<span class="cart-badge">${u.dm_unread}</span>` : ""}</button>` : "";
  const notifBtn = u ? `<button class="icon-btn" data-action="open-notifs" title="Notifikace">🔔${u.notif_unread ? `<span class="cart-badge">${u.notif_unread}</span>` : ""}</button>` : "";
  let right;
  if (u) {
    right = `<div class="pts-pill" title="Tvůj zůstatek"><span class="coin"></span><b class="pts-num">${Number(u.points).toLocaleString("cs-CZ")}</b><span class="lbl">sedláků</span></div>${u.level ? `<div class="lvl-pill"><b class="lvl-num">Lv ${u.level}</b><span class="lvl-bar"><i class="lvl-fill" style="width:${u.level_pct || 0}%"></i></span><div class="lvl-tip"><div class="lt-h">⭐ Level ${u.level} → ${u.level + 1}</div><div class="lt-bar"><i style="width:${u.level_pct || 0}%"></i></div><div class="lt-r"><span>${Number(u.level_into || 0).toLocaleString("cs-CZ")} / ${Number(u.level_span || 0).toLocaleString("cs-CZ")} XP</span><b>${u.level_pct || 0}%</b></div><div class="lt-m">Chybí <b>${Number(Math.max(0, (u.level_span || 0) - (u.level_into || 0))).toLocaleString("cs-CZ")}</b> XP do levelu ${u.level + 1}</div></div></div>` : ""}${cartBtn}${msgBtn}${notifBtn}
      <a href="#/profile" class="user-chip" title="Můj profil">${avatarHTML(u.username, u.avatar_url, "", cosF(u))}<div style="display:flex;flex-direction:column;line-height:1.15"><span class="uc-name ${cosN(u)}">${esc(u.username)}</span><span class="uc-tier">${userTier(u.rank) ? "★ " + userTier(u.rank) : ""}</span></div></a>
      <button class="btn btn-ghost btn-sm logout-top" data-action="logout" title="Odhlásit">Odhlásit</button>`;
  } else {
    right = `${cartBtn}<button class="btn btn-kick btn-sm" data-action="connect">🟢 <span class="kick-long">Připojit přes Kick</span><span class="kick-short">Připojit</span></button>`;
  }

  $("#topbar").innerHTML = `
    <a class="logo" href="#/shop"><span class="mark">Z</span><span class="brand">ZURYS</span></a>
    <a class="stream-dot stream-dot--unknown" id="streamDot" target="_blank" rel="noopener noreferrer" title="Stav streamu…"><span class="sd-pip"></span><span class="sd-txt">…</span></a>
    <nav class="nav">${navLinks}</nav>
    <div class="top-right">
      ${right}
      <button class="icon-btn hamburger" data-action="toggle-mobile" title="Menu">☰</button>
    </div>`;

  $("#mobilenav").innerHTML =
    items.map(([k, l]) => `<a href="#/${k}" class="${route === k ? "active" : ""}">${l}${navDot(k)}</a>`).join("")
    + `<a href="#/cart" class="${route === "cart" ? "active" : ""}">🛒 Košík${cartCount() ? ` (${cartCount()})` : ""}</a>`
    + (u
      ? `<a href="#/zpravy" class="${route === "zpravy" ? "active" : ""}">✉️ Zprávy${u.dm_unread ? ` (${u.dm_unread})` : ""}</a><a href="#" data-action="open-notifs">🔔 Notifikace${u.notif_unread ? ` (${u.notif_unread})` : ""}</a><a href="#/profile" class="${route === "profile" ? "active" : ""}">👤 Profil</a>${isStaff(u) ? `<a href="#/admin" class="${route === "admin" ? "active" : ""}">🛠️ ${u.role === "admin" ? "Admin" : "Panel"}</a>` : ""}<a href="#" data-action="logout">🚪 Odhlásit</a>`
      : `<a href="#" data-action="connect">🟢 Připojit přes Kick</a>`);

  refreshStreamDot();
  if (!window._streamDotTimer) window._streamDotTimer = setInterval(refreshStreamDot, 60000);
  if (u) animateBalance(Number(u.points));   // gamifikace: napočítej zůstatek při změně
  if (u) checkLevelUp(Number(u.level || 0));  // gamifikace: oslava level-upu
}

/* ---------------- Živá tečka: stav streamu (online/offline) ---------------- */
async function refreshStreamDot() {
  const el = document.getElementById("streamDot");
  if (!el) return;
  try {
    const s = await api("/stream/status");
    const txt = el.querySelector(".sd-txt");
    if (s && s.live) {
      el.className = "stream-dot stream-dot--live";
      if (txt) txt.textContent = "LIVE";
      el.title = "Stream právě běží — klikni a podívej se! 🔴";
      el.href = s.channel ? `https://kick.com/${s.channel}` : "#";
    } else {
      el.className = "stream-dot stream-dot--off";
      if (txt) txt.textContent = "OFFLINE";
      el.title = "Stream je teď offline";
      el.removeAttribute("href");
    }
  } catch (e) { /* ticho – tečka zůstane v posledním stavu */ }
}

/* ============================================================
   STRÁNKY
============================================================ */

/* ---------- SHOP (Drop Arena) ---------- */
function pageShop() {
  const view = $("#view");
  view.innerHTML = `
    <div class="ticker"><div class="ticker-track" id="tickerTrack"></div></div>
    <div id="dropBanner"></div>
    <div class="da-head shop-hero"><img src="/sedlak-cut.png" class="hero-sedlak" alt="Sedlák" />
      <div><h1>Zurys <span class="accent">Shop</span></h1>
      <p>Utrať nasbírané sedláky za prémiové skiny a odměny — instantní odměny, limitky i tomboly. 🌾</p></div></div>
    <div id="happyBanner"></div>
    <div id="soldFeed"></div>
    <div id="shopHero"></div>
    <div id="shopMilestone"></div>
    <div class="da-filters" id="filters"></div>
    <div class="da-grid" id="prodGrid">${skeletonCards(8)}</div>
    <div style="text-align:center;margin-top:26px" id="loadMoreWrap"></div>`;
  renderFilters();
  loadActivity();
  loadDropBanner();
  loadSoldFeed();
  loadMilestone();
  dropTimer = setInterval(() => { if (!document.hidden) { loadDropBanner(); loadSoldFeed(); } }, 10000);
  cardTimer = setInterval(updateCardTimers, 1000);
  shopState.page = 1; shopState.items = [];
  loadProducts(true);
}

async function loadActivity() {
  const t = $("#tickerTrack"); if (!t) return;
  try {
    const items = await api("/shop/activity?limit=24");
    if (!items.length) { t.innerHTML = `<span class="ticker-item"><span class="dot"></span><span class="what">Buď první, kdo něco vyhraje! ⚡</span></span>`; return; }
    const one = items.map((a) => `<span class="ticker-item"><span class="dot"></span><span class="who">${uLink(a.username)}</span><span class="what">${esc(a.text)}</span></span>`).join("");
    t.innerHTML = one + one;  // 2× pro plynulé scrollování
  } catch (e) { t.innerHTML = ""; }
}

async function loadCommunityGoal() {
  const box = document.getElementById("chatGoal"); if (!box) return;
  try {
    const g = await api("/community-goal");
    if (!g.enabled) { box.innerHTML = ""; return; }
    const pct = g.pct || 0;
    box.innerHTML = `<div class="cgoal${g.done ? " done" : ""}">
      <div class="cgoal-top">
        <span class="cgoal-title">💬 Dnešní chat cíl${g.done ? " — SPLNĚNO! 🎉" : ""}</span>
        <span class="cgoal-rew">Odměna <b>+${fmtPts(g.reward)}</b> všem aktivním 🌾</span>
      </div>
      <div class="cgoal-bar"><span style="width:${pct}%"></span></div>
      <div class="cgoal-sub">${g.progress} / ${g.target} · ${g.done ? "rozdáno všem, kdo dnes psali v chatu! 🎁" : "pište v chatu a naplňte to společně! 🚀"}</div>
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}

async function loadSubGoal() {
  const box = document.getElementById("subGoal"); if (!box) return;
  try {
    const g = await api("/sub-goal");
    if (!g.enabled) { box.innerHTML = ""; return; }
    const pct = g.pct || 0;
    const tier = g.tier != null ? g.tier : 0;
    const tierTxt = g.maxed ? " — MAX TIER 🏆" : (g.tier != null ? ` · Tier ${tier + 1}` : "");
    box.innerHTML = `<div class="cgoal cgoal--sub${g.maxed ? " done" : ""}">
      <div class="cgoal-top">
        <span class="cgoal-title">🟣 SUB cíl${tierTxt}</span>
        <span class="cgoal-rew">Odměna <b>+${fmtPts(g.reward)}</b> gifterům 🎁</span>
      </div>
      <div class="cgoal-bar"><span style="width:${pct}%"></span></div>
      <div class="cgoal-sub">${g.progress} / ${g.target} subů · ${g.maxed ? "max tier dosažen! 🏆" : "giftni sub a posuň tier — odměnu berou všichni gifteři! 🟣"}</div>
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}

let dropTimer = null;
let cardTimer = null;
let gameTimer = null, gameState = null, finishedGameId = null, lastBoardKey = "", currentGameId = null, gameClockTimer = null, gameClockBase = null;
let duelTimer = null, _seenDuels = null;
let dmThreadTimer = null, _dmThreadMode = null, _dmThreadUid = null, _dmLastId = 0;
async function loadDropBanner() {
  const box = $("#dropBanner"); if (!box) return;
  // nepřepisuj, když uživatel zrovna píše kód
  if (document.activeElement && document.activeElement.id === "dropCode") return;
  try {
    const d = await api("/drops/active");
    if (!d.active) { window._dropShownAt = 0; box.innerHTML = ""; return; }
    const winners = d.winners.length
      ? d.winners.map((w) => `<span class="drop-winner">#${w.position} ${esc(w.username)}</span>`).join("")
      : `<span class="faint">zatím nikdo – buď první! ⚡</span>`;
    const mine = state.user && d.winners.find((w) => w.username === state.user.username);
    let action;
    if (!state.user) action = `<button class="btn btn-kick" data-action="connect">🟢 Připoj se a hraj</button>`;
    else if (mine) action = `<span class="drop-winner" style="font-size:14px;padding:8px 14px">✅ Chytil jsi #${mine.position}!</span>`;
    else if (d.spots_left <= 0) action = `<button class="btn" disabled>Rozebráno ⚡</button>`;
    else { if (!window._dropShownAt) window._dropShownAt = Date.now(); action = `<form class="drop-form" data-submit="claim-drop">
        <input class="input" id="dropCode" placeholder="KÓD Z CHATU" autocomplete="off" style="text-transform:uppercase">
        <button class="btn btn-kick" type="submit">Chytit! ⚡</button></form>`; }
    box.innerHTML = `
      <div class="drop-banner">
        <div class="drop-top"><span class="drop-live">🔴 LIVE DROP</span><span class="drop-info"><b>${fmtPts(d.points)}</b> pro nejrychlejší · zbývá <b>${d.spots_left}/${d.max_winners}</b></span></div>
        <div class="drop-action">${action}</div>
        <div class="drop-winners">${winners}</div>
      </div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function doClaimDrop() {
  const code = $("#dropCode").value.trim();
  if (!code) { toast("Zadej kód z chatu.", "error"); return; }
  try {
    const r = await api("/drops/claim", { method: "POST", body: { code, t0: window._pageT0 || 0, dwell: Date.now() - (window._dropShownAt || window._pageT0 || 0), hp: "" } });
    state.user.points = r.balance;
    toast(r.message, "success");
    renderHeader(); loadDropBanner();
  } catch (e) { toast(e.message, "error"); loadDropBanner(); }
}

/* Hero + vodorovné řady (výchozí pohled bez filtru) */
async function loadBrowse() {
  const box = $("#shopBrowse"); if (!box) return;
  try {
    const data = await api("/shop/products?type=all&page=1&page_size=48");
    const items = data.items;
    shopState.items = items;
    if (!items.length) { box.innerHTML = `<div class="empty"><div class="big">🗃️</div>Zatím žádné odměny.</div>`; return; }
    const pool = items.filter((p) => p.in_stock);
    const featured = (pool.length ? pool : items).slice().sort((a, b) => b.cost_points - a.cost_points)[0];

    let html = heroHTML(featured);
    html += `<section class="row"><div class="row-head"><h2>⚡ Poslední nákupy</h2></div><div class="recent-track" id="recentTrack"><span class="faint">Načítám…</span></div></section>`;
    const groups = [["instant", "⚡ Instantní"], ["short", "⏱️ Krátké"], ["long", "🎯 Delší"], ["yearly", "📅 Roční"], ["raffle", "🎟️ Tomboly"]];
    for (const [type, label] of groups) {
      const list = items.filter((p) => p.type === type);
      if (list.length) html += rowHTML(label, type, list);
    }
    const exch = items.filter((p) => p.category === "Směnárna");
    if (exch.length) html += rowHTML("💱 Směnárna", "exch", exch);

    box.innerHTML = html;
    loadRecentRow();
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function heroHTML(p) {
  if (!p) return "";
  const flags = [];
  if (p.subs_only) flags.push(`<span class="badge badge-sub">👑 Jen sub</span>`);
  if (p.vip_only) flags.push(`<span class="badge badge-vip">💎 Jen VIP</span>`);
  const bg = p.image_url
    ? `background-image:url('${esc(p.image_url)}');background-size:cover;background-repeat:no-repeat;background-position:center`
    : `background:${gradFor(p)}`;
  return `
    <div class="hero">
      <div class="hero-bg" style="${bg}"></div>
      ${p.image_url ? "" : `<div class="hero-emoji">${emojiFor(p)}</div>`}
      <div class="hero-content">
        <div class="hero-tag">★ Doporučená odměna</div>
        <div class="hero-badges"><span class="badge badge-cat">${esc(p.category || TYPE_LABEL[p.type])}</span>${flags.join("")}</div>
        <h1>${esc(p.name)}</h1>
        <div class="hero-actions">
          <span class="hero-price">${fmtPts(p.cost_points)}</span>
          <button class="btn btn-primary" data-action="open-product" data-id="${p.id}">Zobrazit detail</button>
        </div>
      </div>
    </div>`;
}

function rowHTML(label, key, list) {
  return `
    <section class="row">
      <div class="row-head"><h2>${label}</h2><span class="count">${list.length}</span>
        <div class="row-arrows">
          <button data-action="row-scroll" data-target="row-${key}" data-dir="-1" title="Zpět">‹</button>
          <button data-action="row-scroll" data-target="row-${key}" data-dir="1" title="Dál">›</button>
        </div>
      </div>
      <div class="row-track" id="row-${key}">${list.map(productCardHTML).join("")}</div>
    </section>`;
}

function skeletonCards(n) {
  return Array.from({ length: n }).map(() => `<div class="skeleton" style="height:250px"></div>`).join("");
}

function renderFilters() {
  const f = [["all", "Vše"], ["instant", "Instantní"], ["raffle", "Tombola"], ["ending", "Končí brzy"]];
  const chips = f.map(([k, l]) => `<button class="chip ${shopState.type === k ? "active" : ""}" data-action="filter-type" data-type="${k}">${l}</button>`).join("");
  $("#filters").innerHTML = chips +
    `<span class="spacer"></span>` +
    `<button class="chip ${shopState.afford ? "active" : ""}" data-action="toggle-afford">🌾 Na co mám</button>` +
    `<button class="chip ${shopState.sort ? "active" : ""}" data-action="sort-price">${shopState.sort === "price_desc" ? "⬇" : "⬆"} Cena</button>` +
    `<button class="chip ${shopState.subs ? "active" : ""}" data-action="toggle-subs">👑 Jen subs</button>`;
}

async function loadProducts(reset) {
  const grid = $("#prodGrid"); if (!grid) return;
  try {
    const params = { subs_only: shopState.subs, page: shopState.page, page_size: 8 };
    if (shopState.type === "ending") params.ending = true; else params.type = shopState.type || "all";
    const data = await api("/shop/products?" + new URLSearchParams(params).toString());
    if (reset) shopState.items = data.items; else shopState.items.push(...data.items);
    shopState.total = data.total; shopState.hasMore = data.has_more;
    shopState.discount = data.discount_pct || 0;
    renderHappyBanner();
    renderGrid();
  } catch (e) { toast(e.message, "error"); }
}

function renderHappyBanner() {
  const el = $("#happyBanner"); if (!el) return;
  const d = shopState.discount || 0;
  el.innerHTML = d > 0 ? `<div class="happy-banner">🔴 <b>HAPPY HOUR!</b> Všechno <b>−${d} %</b> právě teď — utrácej dokud sleva běží! 🌾</div>` : "";
}
async function loadSoldFeed() {
  const el = $("#soldFeed"); if (!el) return;
  try {
    const r = await api("/shop/recent?limit=10");
    if (!r.length) { el.innerHTML = ""; return; }
    el.innerHTML = `<div class="sold-feed"><span class="sold-live">🟢 PRÁVĚ KOUPILI</span><div class="sold-track">${r.map((s) => `<span class="sold-item">${uLink(s.username)} <span class="faint">${s.product_type === "raffle" ? "tiket " : ""}${esc(s.product_name)}</span></span>`).join(`<span class="sold-sep">·</span>`)}</div></div>`;
  } catch (e) { el.innerHTML = ""; }
}
async function loadMilestone() {
  const el = $("#shopMilestone"); if (!el) return;
  if (!state.user) { el.innerHTML = ""; return; }
  try {
    const m = await api("/shop/milestone");
    if (m.top) { el.innerHTML = `<div class="ms-bar"><div class="ms-top">🏆 Utraceno <b>${Number(m.spent).toLocaleString("cs-CZ")} 🌾</b> — <b style="color:var(--accent)">Magnát 🥇</b> odemčen!</div></div>`; return; }
    const span = (m.next_at - m.prev_at) || 1;
    const pct = Math.max(3, Math.min(100, Math.round((m.spent - m.prev_at) * 100 / span)));
    el.innerHTML = `<div class="ms-bar">
        <div class="ms-top">🏆 Utraceno celkem <b>${Number(m.spent).toLocaleString("cs-CZ")} 🌾</b> <span class="faint">— další odměna: <b style="color:var(--text)">${esc(m.next_reward)}</b></span></div>
        <div class="ms-track"><div class="ms-fill" style="width:${pct}%"></div></div>
        <div class="ms-foot faint">ještě ${Number(m.next_at - m.spent).toLocaleString("cs-CZ")} 🌾 do odměny</div>
      </div>`;
  } catch (e) { el.innerHTML = ""; }
}

function shopHeroHTML(p) {
  const [rlabel, rhex] = rarityInfo(p.rarity);
  const cd = countdown(p.ends_at);
  const priceTxt = Number(p.cost_points).toLocaleString("cs-CZ");
  const img = p.image_url ? `background-image:url('${esc(p.image_url)}')` : `background:radial-gradient(circle at 42% 40%, ${rhex}66, transparent 70%)`;
  const btn = !state.user
    ? `<button class="da-btn buy" data-action="connect" style="width:auto;padding:10px 22px">Připoj se a hraj</button>`
    : (!p.in_stock ? `<span class="miss">Vyprodáno</span>`
      : (state.user.points < p.cost_points ? `<span class="miss">chybí ${(p.cost_points - state.user.points).toLocaleString("cs-CZ")} 🌾</span>`
        : `<button class="da-btn buy" data-action="buy" data-id="${p.id}" style="width:auto;padding:10px 22px">Vyzvednout ➤</button>`));
  return `<div class="shop-hero" style="--rar:${rhex}">
      <div class="shop-hero-img" data-action="open-product" data-id="${p.id}" style="${img}">${p.image_url ? "" : `<span style="font-size:52px">${emojiFor(p)}</span>`}</div>
      <div class="shop-hero-body">
        <span class="shop-hero-rar" style="color:${rhex}">${rlabel ? "★ " + rlabel + " · " : ""}FEATURED 🔥</span>
        <div class="shop-hero-name" data-action="open-product" data-id="${p.id}">${esc(p.name)}</div>
        ${cd && cd !== "KONEC" ? `<div class="shop-hero-cd">⏳ končí za ${cd}${!p.unlimited && p.stock > 0 ? ` · zbývá ${p.stock} ks` : ""}</div>` : ""}
        <div class="shop-hero-buy"><span class="da-price"><span class="coin"></span>${priceTxt} sedláků${p.cost_orig ? ` <s class="da-orig">${Number(p.cost_orig).toLocaleString("cs-CZ")}</s>` : ""}</span>${btn}</div>
      </div>
    </div>`;
}
function renderGrid() {
  const grid = $("#prodGrid"); if (!grid) return;
  const all = shopState.items.slice();
  const hero = all.find((p) => p.hot && p.image_url && countdown(p.ends_at) !== "KONEC") || null;   // spotlight = hot + obrázek
  let items = hero ? all.filter((p) => p.id !== hero.id) : all;
  if (shopState.sort === "price_asc") items.sort((a, b) => a.cost_points - b.cost_points);
  else if (shopState.sort === "price_desc") items.sort((a, b) => b.cost_points - a.cost_points);
  if (shopState.afford && state.user) items = items.filter((p) => state.user.points >= p.cost_points && p.in_stock);
  const heroEl = $("#shopHero"); if (heroEl) heroEl.innerHTML = hero ? shopHeroHTML(hero) : "";
  if (!items.length) {
    grid.innerHTML = hero ? "" : `<div class="empty" style="grid-column:1/-1"><div class="big">🗃️</div>Žádné odměny pro zvolený filtr.</div>`;
  } else {
    grid.innerHTML = items.map(productCardHTML).join("");
  }
  const wrap = $("#loadMoreWrap");
  if (wrap) wrap.innerHTML = shopState.hasMore
    ? `<button class="btn btn-ghost" data-action="load-more">Načíst další (${shopState.items.length}/${shopState.total})</button>`
    : (shopState.items.length ? `<span class="faint">To je vše · ${shopState.total} položek</span>` : "");
}

function productCardHTML(p) {
  const [rlabel, rhex] = rarityInfo(p.rarity);
  const isRaffle = p.type === "raffle";
  const cd = countdown(p.ends_at);
  const ended = !!p.ends_at && cd === "KONEC";
  const badges = [];
  if (p.hot) badges.push(`<span class="da-b b-hot">HOT</span>`);
  if (p.subs_only) badges.push(`<span class="da-b b-sub">SUB</span>`);
  if (cd && !ended) badges.push(`<span class="da-b b-end">KONČÍ</span>`);
  if (!isRaffle && !p.unlimited && p.stock > 0 && p.stock <= 5) badges.push(`<span class="da-b b-scar">zbývá ${p.stock}</span>`);
  const bg = p.image_url
    ? `background-image:url('${esc(p.image_url)}');background-size:cover;background-repeat:no-repeat;background-position:center`
    : `background: radial-gradient(circle at 50% 38%, ${rhex}33, transparent 68%), linear-gradient(180deg, var(--surface-2), var(--bg-2))`;
  const periodTxt = PERIOD_LABEL[p.period] || "";
  const typeLine = `${isRaffle ? ("TOMBOLA" + (periodTxt ? " · " + periodTxt : "")) : "OKAMŽITÁ ODMĚNA"}${rlabel ? " · " + rlabel : ""}`;

  let progress = "";
  if (isRaffle && !p.unlimited && p.stock > 0) {
    const sold = p.tickets_sold || 0;
    const pct = Math.min(100, Math.round(sold / p.stock * 100));
    const capPer = p.max_per_person_pct || 0;
    const capLine = capPer > 0
      ? `<div class="faint" style="font-size:11px;margin-top:4px">🎟️ max ${capPer} / osoba</div>`
      : "";
    progress = `<div class="da-progress"><div class="bar"><div class="fill" style="width:${pct}%"></div></div><div class="nums"><span>V tombole</span><span>${sold}/${p.stock}</span></div>${capLine}</div>`;
  }

  const subLocked = p.subs_only && state.user && !state.user.is_sub && state.user.role !== "sub" && state.user.role !== "admin";
  const cant = state.user && !ended && p.in_stock && !subLocked && state.user.points < p.cost_points;  // přihlášený, koupitelné, ale chybí body

  let btn;
  const priceTxt = Number(p.cost_points).toLocaleString("cs-CZ") + " sedláků";
  if (ended) btn = `<button class="da-btn waiting" disabled>${isRaffle ? "⏳ Čeká na vylosování" : "⏳ Akce skončila"}</button>`;
  else if (!state.user) btn = `<button class="da-btn buy" data-action="connect">Připoj se a hraj</button>`;
  else if (subLocked) btn = `<button class="da-btn locked" disabled>🔒 Pouze pro subs</button>`;
  else if (!p.in_stock) btn = `<button class="da-btn waiting" disabled>Vyprodáno</button>`;
  else if (state.user.points < p.cost_points) {
    const gpct = Math.max(3, Math.min(100, Math.round(state.user.points / p.cost_points * 100)));
    btn = `<div class="da-goal" title="Tvůj postup k této odměně">
      <div class="da-goal-bar"><div class="da-goal-fill" style="width:${gpct}%"></div></div>
      <div class="da-goal-txt"><span>máš <b>${gpct} %</b></span><span class="da-goal-miss">chybí ${(p.cost_points - state.user.points).toLocaleString("cs-CZ")} 🌾</span></div>
    </div>`;
  }
  else btn = `<button class="da-btn buy" data-action="buy-confirm" data-id="${p.id}">${isRaffle ? "Koupit tiket" : "Vyzvednout"} — ${priceTxt}</button>`;

  return `
    <div class="da-card ${ended ? "sold-out" : ""}${cant ? " cant" : ""}" style="--rar:${rhex}">
      <div class="da-rbar"></div>
      <div class="da-badges">${badges.join("")}</div>
      ${cd ? `<div class="da-timer" data-ends="${esc(p.ends_at)}">${ended ? "UKONČENO" : cd}</div>` : ""}
      <div class="da-img" data-action="open-product" data-id="${p.id}" style="${bg}">
        ${p.image_url ? "" : `<span class="da-emoji">${emojiFor(p)}</span>`}
        ${rlabel ? `<span class="da-rarity" style="color:${rhex}">${rlabel}</span>` : ""}
      </div>
      <div class="da-body">
        <div class="da-name" data-action="open-product" data-id="${p.id}">${esc(p.name)}</div>
        <div class="da-type">${typeLine}</div>
        <div class="da-price"><span class="coin"></span>${priceTxt}${p.cost_orig ? ` <s class="da-orig">${Number(p.cost_orig).toLocaleString("cs-CZ")}</s>` : ""}${isRaffle ? ` <small>/ tiket</small>` : ""}</div>
        ${progress}
        ${btn}
      </div>
    </div>`;
}

function updateCardTimers() {
  document.querySelectorAll(".da-timer[data-ends]").forEach((el) => {
    const cd = countdown(el.dataset.ends);
    el.textContent = cd === "KONEC" ? "UKONČENO" : (cd || "");
  });
}

/* ============================================================
   PROVABLY FAIR – ověřitelná náhoda (verifikátor v prohlížeči)
============================================================ */
async function fairDigest(serverSeed, clientSeed, nonce) {        // 1:1 s app/fairness.py
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(serverSeed), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(`${clientSeed}:${nonce}`));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function fairWeightedIndex(digestHex, weights) {
  const roll = parseInt(digestHex.slice(0, 8), 16) / 0x100000000;  // 2^32
  const total = weights.reduce((a, b) => a + b, 0);
  let r = roll * total, acc = 0;
  for (let i = 0; i < weights.length; i++) { acc += weights[i]; if (r < acc) return i; }
  return weights.length - 1;
}
let _fairData = null;
function pageFair() {
  if (!state.user) { navigate("connect"); return; }
  $("#view").innerHTML = `<div class="page-head"><h1>🔐 Provably fair</h1><p class="muted">Ověř si, že hry nejsou zmanipulované. Výsledek = <code>HMAC(server&nbsp;seed, client&nbsp;seed:nonce)</code>. Hash server seedu zveřejníme PŘEDEM — po rotaci ti seed odhalíme a ty si vše přepočítáš sám.</p></div><div id="fairBox">${skeletonCards(1)}</div>`;
  loadFair();
}
async function loadFair(revealed) {
  try {
    const d = await api("/fair/me"); _fairData = d;
    const box = $("#fairBox"); if (!box) return;
    const wheelRows = d.recent.filter((r) => r.game === "wheel");
    box.innerHTML = `
      <div class="panel">
        <div class="fair-row"><span class="fair-k">Server commit (SHA-256, zveřejněno předem)</span><code class="fair-v">${esc(d.server_hash)}</code></div>
        <div class="fair-row"><span class="fair-k">Tvůj client seed</span><input class="input" id="fairClient" value="${esc(d.client_seed)}" style="max-width:280px"></div>
        <div class="fair-row"><span class="fair-k">Nonce (her s tímto seedem)</span><b>${d.nonce}</b></div>
        <button class="btn btn-primary" data-action="fair-rotate" style="margin-top:6px">🔄 Změnit seed + odhalit starý</button>
        <div class="muted" style="font-size:12.5px;margin-top:8px">Rotace nasadí nový tajný seed (nový commit) a ODHALÍ starý — s ním ověříš hry, co jsi odehrál.</div>
      </div>
      ${revealed ? `<div class="panel" style="border-color:#e1c341"><b>🔓 Odhalený starý server seed:</b><br><code class="fair-v">${esc(revealed)}</code><div class="muted" style="font-size:12.5px;margin-top:6px">SHA-256 z něj = původní commit. Hry dole ověř tlačítkem.</div></div>` : ""}
      <div class="panel">
        <div class="section-title" style="margin-top:0">🎡 Posledních ${wheelRows.length} zatočení kola</div>
        <div class="field"><label>Server seed na ověření (vlož odhalený)</label><input class="input" id="fairVerifySeed" placeholder="odhalený server seed…" value="${esc(revealed || "")}"></div>
        <button class="btn btn-ghost btn-sm" data-action="fair-verify" style="margin:8px 0 14px">✅ Ověřit všechna</button>
        <div id="fairRows">${wheelRows.map((r, i) => `<div class="fair-log-row"><span class="faint">nonce #${r.nonce}</span> · client <code>${esc(r.client_seed)}</code> → <b>${Number(d.wheel_amounts[r.result]).toLocaleString("cs-CZ")} sedláků</b> <span class="fair-check" id="fchk${i}"></span></div>`).join("") || `<div class="muted">Zatím jsi netočil kolem. Roztoč ho na Bonusech a vrať se to ověřit. 🎡</div>`}</div>
      </div>`;
  } catch (e) { const b = $("#fairBox"); if (b) b.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function fairRotate() {
  const cs = ((document.getElementById("fairClient") || {}).value || "").trim();
  try {
    const r = await api("/fair/rotate", { method: "POST", body: { client_seed: cs } });
    toast("Seed změněn — starý odhalen 🔓", "success");
    loadFair(r.revealed_server_seed);
  } catch (e) { toast(e.message, "error"); }
}
async function fairVerify() {
  if (!_fairData) return;
  const seed = ((document.getElementById("fairVerifySeed") || {}).value || "").trim();
  if (!seed) { toast("Vlož odhalený server seed", "error"); return; }
  const rows = _fairData.recent.filter((r) => r.game === "wheel");
  let okAll = 0;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const idx = fairWeightedIndex(await fairDigest(seed, r.client_seed, r.nonce), _fairData.wheel_weights);
    const ok = idx === r.result;
    if (ok) okAll++;
    const el = document.getElementById("fchk" + i);
    if (el) el.innerHTML = ok ? `<span style="color:var(--success);font-weight:700">✓ sedí</span>` : `<span style="color:#ff6b6b;font-weight:700">✗ NESEDÍ</span>`;
  }
  toast(`Ověřeno: ${okAll}/${rows.length} sedí ✓`, okAll === rows.length ? "success" : "error");
}

function gambleBlockBanner() {
  const u = state.user;
  const until = u && u.gamble_block_until;
  if (!until) return "";
  const when = until === "permanent" ? "natrvalo" : "do " + new Date(until).toLocaleString("cs-CZ");
  return `<div class="se-banner">
      <div class="se-banner-ico">🔒</div>
      <div class="se-banner-txt">
        <div class="se-banner-title">Máš zákaz sázení</div>
        <div class="se-banner-sub">Aktivní sebevyloučení <b>${when}</b>. Duely, piškvorky, blackjack i predikce jsou zamčené — nejde zrušit dřív. Body ti zůstávají. 🌾</div>
      </div>
    </div>`;
}

function pagePredikce() {
  const view = $("#view");
  view.innerHTML = `
    <div class="da-head"><h1>Pred<span class="accent">ikce</span> <span class="live-dot" title="Sázky se aktualizují živě">🔴 LIVE</span></h1><p>Vsaď sedláky na výsledek (CS2 🔫). Výherci si rozdělí celý bank. Sázky se aktualizují živě — nemusíš obnovovat stránku.</p></div>
    ${gambleBlockBanner()}
    <div id="predList">${skeletonCards(2)}</div>`;
  loadPredictions();
  startPredPoll("public");
}
async function loadPredictions() {
  const box = $("#predList"); if (!box) return;
  try {
    const d = await api("/predictions");
    if (!d.active.length && !d.recent.length) {
      box.innerHTML = `<div class="empty"><div class="big">🎯</div>Zatím žádné predikce. Sleduj stream — brzy něco přijde!</div>`;
      return;
    }
    let html = d.active.length
      ? d.active.map(predCardHTML).join("")
      : `<div class="empty" style="padding:30px"><div class="big">🎯</div>Teď neběží žádná predikce.</div>`;
    if (d.recent.length) {
      html += `<div class="section-title" style="margin-top:26px">📜 Poslední výsledky</div>` + d.recent.map(predRecentHTML).join("");
    }
    box.innerHTML = html;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function predBarHTML(pct) {
  return `<div style="height:8px;border-radius:6px;background:rgba(255,255,255,.08);overflow:hidden;margin:8px 0 5px"><div class="pred-fill" style="height:100%;width:${pct}%;background:var(--accent-2);transition:width .5s ease"></div></div>`;
}
function predRoleTag(role) {
  return role === "broadcaster" ? " 🎙️" : role === "admin" ? " 🛡️" : role === "mod" ? " 🛠️" : "";
}
function predBy(p) {
  if (!p.creator) return "";
  const when = p.created_at ? ` · ${timeAgo(p.created_at)}` : "";
  return ` · 👤 vytvořil <b>${esc(p.creator.username)}</b>${predRoleTag(p.creator.role)}${when}`;
}
function predCardHTML(p) {
  const locked = p.status === "locked";
  const mine = p.my_bet;
  const myOpt = mine ? mine.option_id : null;
  const optBox = (o) => {
    const isMine = myOpt === o.id;
    const canBet = state.user && !locked && !(mine && myOpt !== o.id);
    return `<div class="pred-opt" data-opt="${o.id}" style="border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:12px;background:rgba(255,255,255,.02)${isMine ? ";outline:2px solid var(--accent-2)" : ""}">
      <div class="row-between"><b>${esc(o.label)}</b><span class="pred-mult" style="color:var(--accent-2);font-weight:800">${o.mult ? "×" + o.mult : "—"}</span></div>
      ${predBarHTML(o.share_pct)}
      <div class="row-between faint" style="font-size:12px"><span class="pred-pool">${o.share_pct}% · ${fmtPts(o.pool)}</span><span class="pred-bettors">${o.bettors} 👤</span></div>
      ${isMine ? `<div style="color:var(--accent-2);font-size:12.5px;font-weight:700;margin-top:8px">✓ Tvá sázka: ${fmtPts(mine.amount)}</div>` : ""}
      ${canBet ? `<button class="btn btn-primary btn-sm btn-block" style="margin-top:10px" data-action="pred-bet" data-pid="${p.id}" data-oid="${o.id}">${isMine ? "➕ Přidat" : "Vsadit"}</button>` : ""}
    </div>`;
  };
  const badge = locked ? `<span class="badge badge-admin">🔒 Uzamčeno</span>` : `<span class="badge badge-sub">🟢 Otevřeno</span>`;
  return `<div class="panel pred-card" data-pred="${p.id}" data-status="${p.status}" data-myopt="${mine ? mine.option_id : 0}" data-myamt="${mine ? mine.amount : 0}" style="margin-bottom:16px">
    <div class="row-between" style="margin-bottom:3px"><b style="font-size:17px">🎯 ${esc(p.question)}</b>${badge}</div>
    <div class="faint" style="font-size:12.5px;margin-bottom:12px">${esc(p.game)} · celkový bank <b class="pred-bank">${fmtPts(p.total_pool)}</b>${predBy(p)}</div>
    ${!locked && p.lock_at ? `<div class="pred-countdown" data-lockat="${esc(p.lock_at)}" style="font-size:14px;font-weight:800;color:var(--accent-2);margin:-4px 0 12px">⏳ …</div>` : ""}
    ${state.user && !locked
      ? `<div class="field" style="margin-bottom:12px"><label>Sázka (sedláci) — pak klikni na možnost</label><input class="input" id="predAmt-${p.id}" type="number" min="1" placeholder="např. 100" style="max-width:220px"></div>`
      : (!state.user
        ? `<div class="faint" style="margin-bottom:10px">Pro sázení se <a href="#" data-action="connect">připoj přes Kick</a>.</div>`
        : `<div class="faint" style="margin-bottom:10px">🔒 Sázky uzavřené — čeká se na výsledek.</div>`)}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px">${p.options.map(optBox).join("")}</div>
  </div>`;
}
function predRecentHTML(p) {
  const cancelled = p.status === "cancelled";
  const winner = p.options.find((o) => o.is_winner);
  const mine = p.my_bet;
  let outcome = "";
  if (mine) {
    if (cancelled) outcome = `<span class="faint">vráceno ${fmtPts(mine.amount)}</span>`;
    else if (mine.payout > 0) outcome = `<span class="pos">+${fmtPts(mine.payout)} 🎉</span>`;
    else outcome = `<span class="neg">−${fmtPts(mine.amount)}</span>`;
  }
  return `<div class="panel" style="margin-bottom:10px;padding:13px 16px">
    <div class="row-between"><b>🎯 ${esc(p.question)}</b>${outcome}</div>
    <div class="faint" style="font-size:13px;margin-top:4px">${cancelled ? "❌ Zrušeno (vklady vráceny)" : `✅ Výsledek: <b style="color:var(--accent-2)">${winner ? esc(winner.label) : "?"}</b> · bank ${fmtPts(p.total_pool)}`}${predBy(p)}</div>
  </div>`;
}
async function predBet(pid, oid) {
  const inp = $("#predAmt-" + pid);
  const amount = parseInt(inp && inp.value, 10);
  if (!amount || amount < 1) { toast("Zadej částku sázky.", "error"); if (inp) inp.focus(); return; }
  if (state.user && amount > state.user.points) { toast("Tolik bodů nemáš.", "error"); return; }
  try {
    const r = await api(`/predictions/${pid}/bet`, { method: "POST", body: { option_id: oid, amount } });
    if (state.user) state.user.points = r.balance;
    toast("Vsazeno! 🎯", "success");
    renderHeader();
    loadPredictions();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Predikce: živá aktualizace (polling + patch DOM, ať se nemaže rozepsaná sázka) --- */
let _predTimer = null, _predMode = null, _predCdTimer = null;
function startPredPoll(mode) {
  stopPredPoll(); _predMode = mode; _predTimer = setInterval(pollPredictions, 7000);
  _predCdTimer = setInterval(tickPredCd, 1000); tickPredCd();   // odpočet do zavření sázek (1 s)
}
function stopPredPoll() {
  if (_predTimer) clearInterval(_predTimer); _predTimer = null; _predMode = null;
  if (_predCdTimer) clearInterval(_predCdTimer); _predCdTimer = null;
}
// Živý odpočet „sázky se zavřou za M:SS" (čte data-lockat z karet, běží i mezi 7s polly).
function tickPredCd() {
  document.querySelectorAll(".pred-countdown[data-lockat]").forEach((el) => {
    const t = Date.parse(el.dataset.lockat || ""); if (!t) { el.textContent = ""; return; }
    const d = Math.round((t - Date.now()) / 1000);
    if (d <= 0) { el.textContent = "🔒 Sázky se zavírají…"; return; }
    const m = Math.floor(d / 60), s = d % 60;
    el.textContent = `⏳ Sázky se zavřou za ${m}:${String(s).padStart(2, "0")}`;
  });
}
function _setText(el, v) { if (el && el.textContent !== v) el.textContent = v; }
function _setBump(el, v) { if (el && el.textContent !== v) { el.textContent = v; el.classList.remove("pred-bump"); void el.offsetWidth; el.classList.add("pred-bump"); } }
async function pollPredictions() {
  if (document.hidden || !_predMode) return;
  if (_predMode === "public") {
    if (!document.getElementById("predList")) return stopPredPoll();
    try { const d = await api("/predictions"); if (!patchPredPublic(d)) loadPredictions(); } catch (e) {}
  } else if (_predMode === "admin") {
    if (!document.querySelector(".apred-card")) return stopPredPoll();
    try { const list = await api("/predictions/admin/all"); if (!patchPredAdmin(list)) adminPredictions(); } catch (e) {}
  }
}
function patchPredPublic(d) {
  const box = document.getElementById("predList"); if (!box) return false;
  const active = d.active || [];
  const cards = [...box.querySelectorAll(".pred-card")];
  if (cards.length !== active.length) return false;                 // přibyla/ubyla predikce → plný reload
  for (let i = 0; i < active.length; i++) if (+cards[i].dataset.pred !== active[i].id) return false;
  for (const p of active) {
    const card = box.querySelector(`.pred-card[data-pred="${p.id}"]`);
    if (!card || card.dataset.status !== p.status) return false;     // open↔locked → plný reload (mění se tlačítka)
    const myOpt = p.my_bet ? p.my_bet.option_id : 0, myAmt = p.my_bet ? p.my_bet.amount : 0;
    if (+card.dataset.myopt !== myOpt || +card.dataset.myamt !== myAmt) return false;  // moje sázka se změnila → reload
    _setBump(card.querySelector(".pred-bank"), fmtPts(p.total_pool));
    for (const o of p.options) {
      const ob = card.querySelector(`.pred-opt[data-opt="${o.id}"]`); if (!ob) continue;
      _setText(ob.querySelector(".pred-mult"), o.mult ? "×" + o.mult : "—");
      const fill = ob.querySelector(".pred-fill"); if (fill) fill.style.width = o.share_pct + "%";
      _setBump(ob.querySelector(".pred-pool"), `${o.share_pct}% · ${fmtPts(o.pool)}`);
      _setBump(ob.querySelector(".pred-bettors"), `${o.bettors} 👤`);
    }
  }
  return true;
}
function patchPredAdmin(list) {
  const box = document.getElementById("adminContent"); if (!box) return false;
  const cards = [...box.querySelectorAll(".apred-card")];
  if (!cards.length || cards.length !== list.length) return false;
  for (const p of list) {
    const card = box.querySelector(`.apred-card[data-pred="${p.id}"]`);
    if (!card || card.dataset.status !== p.status) return false;
  }
  for (const p of list) {
    const card = box.querySelector(`.apred-card[data-pred="${p.id}"]`);
    _setBump(card.querySelector(".apred-bank"), fmtPts(p.total_pool));
    for (const o of p.options) {
      const pill = card.querySelector(`[data-opt="${o.id}"]`); if (!pill) continue;
      _setText(pill.querySelector(".apred-pool"), fmtPts(o.pool));
      _setBump(pill.querySelector(".apred-bettors"), String(o.bettors));
    }
  }
  return true;
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && _predMode) pollPredictions(); });

function stockBadge(p) {
  if (p.unlimited) return `<span class="pc-stock faint">∞ skladem</span>`;
  if (p.stock <= 0) return `<span class="pc-stock neg">Vyprodáno</span>`;
  if (p.stock <= 3) return `<span class="pc-stock" style="color:var(--warning)">⚠ poslední ${p.stock}</span>`;
  return `<span class="pc-stock muted">${p.stock} ks</span>`;
}

async function loadRecentRow() {
  const track = $("#recentTrack"); if (!track) return;
  try {
    const items = await api("/shop/recent?limit=14");
    if (!items.length) { track.innerHTML = `<span class="faint">Zatím žádné nákupy. Buď první! 🚀</span>`; return; }
    track.innerHTML = items.map((r) => `
      <div class="recent-card">
        ${avatarHTML(r.username, r.avatar_url)}
        <div class="meta">
          <div class="who">${uLink(r.username)}</div>
          <div class="what">${r.product_type === "raffle" ? "🎟️ " : ""}${esc(r.product_name)}</div>
        </div>
        <span class="when">${timeAgo(r.created_at)}</span>
      </div>`).join("");
  } catch (e) { track.innerHTML = `<span class="faint">—</span>`; }
}

/* ---------- PRODUCT MODAL ---------- */
function canBuy(p) {
  if (p.ends_at && countdown(p.ends_at) === "KONEC") return { ok: false, reason: "ended" };
  if (!state.user) return { ok: false, reason: "login" };
  const u = state.user, r = u.role;
  if (r !== "admin") {
    if (p.subs_only && !u.is_sub && r !== "sub") return { ok: false, reason: "sub" };
    if (p.vip_only && !u.is_vip && r !== "vip") return { ok: false, reason: "vip" };
  }
  if (!p.in_stock) return { ok: false, reason: "stock" };
  if (state.user.points < p.cost_points) return { ok: false, reason: "points" };
  return { ok: true };
}

async function openProduct(id) {
  let p;
  try { p = await api("/shop/products/" + id); } catch (e) { toast(e.message, "error"); return; }
  const isRaffle = p.type === "raffle";
  const flags = [];
  if (p.subs_only) flags.push(`<span class="badge badge-sub">👑 Jen sub</span>`);
  if (p.vip_only) flags.push(`<span class="badge badge-vip">💎 Jen VIP</span>`);

  const c = canBuy(p);
  let actionBtn = "";
  const buyLabel = isRaffle ? `Koupit tiket za ${fmtPts(p.cost_points)}` : `Koupit za ${fmtPts(p.cost_points)}`;
  if (c.ok) {
    actionBtn = `<button class="btn btn-primary" data-action="buy-confirm" data-id="${p.id}">${buyLabel}</button>
                 <button class="btn btn-ghost" data-action="add-cart" data-id="${p.id}">➕ Do košíku</button>`;
  } else if (c.reason === "login") {
    actionBtn = `<button class="btn btn-kick" data-action="connect">🟢 Pro nákup připoj svůj Kick účet</button>`;
  } else if (c.reason === "ended") {
    actionBtn = `<button class="btn" disabled>⏳ ${isRaffle ? "Čeká na vylosování" : "Akce skončila"}</button>`;
  } else if (c.reason === "sub") {
    actionBtn = `<button class="btn" disabled>👑 Jen pro suby</button>`;
  } else if (c.reason === "vip") {
    actionBtn = `<button class="btn" disabled>💎 Jen pro VIP</button>`;
  } else if (c.reason === "stock") {
    actionBtn = `<button class="btn" disabled>Vyprodáno</button>`;
  } else if (c.reason === "points") {
    actionBtn = `<button class="btn" disabled>Nemáš dost bodů (chybí ${fmtPts(p.cost_points - state.user.points)})</button>
                 <button class="btn btn-ghost" data-action="add-cart" data-id="${p.id}">➕ Do košíku</button>`;
  }

  const hero = p.image_url
    ? `<div class="modal-hero" style="background-image:url('${esc(p.image_url)}');background-size:cover;background-repeat:no-repeat;background-position:center"></div>`
    : `<div class="modal-hero" style="background:${gradFor(p)}">${emojiFor(p)}</div>`;

  openModal(`
    ${hero}
    <div class="modal-body">
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <span class="badge badge-cat">${esc(p.category || "Odměna")}</span>
        <span class="badge badge-cat">${TYPE_LABEL[p.type] || p.type}</span>
        ${flags.join("")}
      </div>
      <h2>${esc(p.name)}</h2>
      ${p.description ? `<p class="muted" style="margin-top:10px;white-space:pre-wrap">${esc(p.description)}</p>` : ""}
      <div class="row-between" style="margin-top:12px">
        <span class="price"><b style="font-size:24px">${Number(p.cost_points).toLocaleString("cs-CZ")}</b><span>bodů</span></span>
        <span>${p.unlimited ? `<span class="faint">∞ skladem</span>` : p.stock > 0 ? `<span class="muted">${p.stock} ks skladem</span>` : `<span class="neg">Vyprodáno</span>`}</span>
      </div>
      <div class="modal-actions">${actionBtn}</div>
      ${isRaffle ? `<div id="raffleBox" class="raffle-list"><div class="faint">Načítám účastníky tomboly…</div></div>` : ""}
    </div>`, isRaffle ? "modal-lg" : "");

  if (isRaffle) loadRaffleBox(p.id);
}

async function loadRaffleBox(id) {
  const box = $("#raffleBox"); if (!box) return;
  try {
    const d = await api(`/shop/raffle/${id}/entries`);
    const meName = state.user && state.user.username ? state.user.username.toLowerCase() : null;
    const mine = meName ? d.participants.find((p) => (p.username || "").toLowerCase() === meName) : null;
    const myT = mine ? mine.tickets : 0;
    const odds = d.total_tickets > 0 ? (myT / d.total_tickets * 100) : 0;
    let html = "";
    if (state.user) {
      html += myT > 0
        ? `<div class="raffle-odds"><span>🎯 Tvoje šance</span><b>${myT} z ${d.total_tickets} = ${odds.toFixed(1)} %</b></div>`
        : `<div class="raffle-odds faint">🎟️ Zatím nemáš tiket — kup si šanci na výhru.</div>`;
    }
    html += `<div class="section-title">🎟️ Kdo nakoupil tikety <span class="faint">(${d.total_tickets} celkem)</span></div>`;
    if (d.winner) html += `<div class="panel gold" id="raffleWinner" style="margin-bottom:12px">🏆 Výherce: <b>${esc(d.winner.username)}</b></div>`;
    if (!d.participants.length) html += `<div class="faint">Zatím nikdo. Buď první! 🎯</div>`;
    else html += d.participants.map((u) => `
      <div class="raffle-row">${avatarHTML(u.username, u.avatar_url)}<span>${esc(u.username)}</span><span class="tickets">${u.tickets}× 🎟️</span></div>`).join("");
    box.innerHTML = html;
    // živý reveal: jen když je výherce, jsi účastník a tenhle los jsi ještě neviděl
    if (d.winner && mine) {
      const seenKey = "raffle_seen_" + id + "_" + (d.winner.created_at || "");
      if (!localStorage.getItem(seenKey)) { localStorage.setItem(seenKey, "1"); raffleReveal(d); }
    }
  } catch (e) { box.innerHTML = `<div class="faint">Nepodařilo se načíst účastníky.</div>`; }
}
function raffleReveal(d) {
  const names = (d.participants || []).map((p) => p.username).filter(Boolean);
  if (!names.length || !d.winner) return;
  const ov = document.createElement("div");
  ov.className = "raffle-reveal";
  ov.innerHTML = `<div class="rr-card"><div class="rr-title" id="rrTitle">🎲 Losování…</div><div class="rr-name" id="rrName">—</div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", () => ov.remove());
  const nameEl = ov.querySelector("#rrName"), titleEl = ov.querySelector("#rrTitle");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const won = state.user && d.winner.username.toLowerCase() === state.user.username.toLowerCase();
  const finish = () => {
    nameEl.textContent = d.winner.username;
    ov.querySelector(".rr-card").classList.add("rr-done");
    titleEl.textContent = won ? "🎉 VYHRÁL JSI!" : "🏆 Výherce!";
    if (won) { try { confettiBurst(); } catch (e) {} }
    setTimeout(() => ov.classList.add("rr-fade"), won ? 2800 : 1700);
    setTimeout(() => ov.remove(), won ? 3400 : 2300);
  };
  if (reduce) { finish(); return; }
  let i = 0, ticks = 0;
  const total = 26 + Math.floor(Math.random() * 8);
  const iv = setInterval(() => {
    nameEl.textContent = names[i % names.length]; i++; ticks++;
    if (ticks >= total) { clearInterval(iv); finish(); }
  }, 75);
}

async function buyProduct(id) {
  try {
    const r = await api("/shop/purchase", { method: "POST", body: { product_id: id } });
    state.user.points = r.balance;
    closeModal();
    toast(r.message, "success");
    try { confettiBurst(); } catch (e) {}      // 🎉 gamifikace: oslava při koupi
    render();
  } catch (e) { toast(e.message, "error"); }
}

// Reveal-then-confirm: ukáže CO kupuješ + DOPAD na zůstatek PŘED potvrzením (lepší UX i pojistka).
// Potvrzovací tlačítko nese data-action="buy" → volá původní buyProduct (nezměněný).
async function confirmBuyModal(id) {
  let p;
  try { p = await api("/shop/products/" + id); } catch (e) { toast(e.message, "error"); return; }
  const c = canBuy(p);
  if (!c.ok) { openProduct(id); return; }   // nejde koupit → otevři detail (ukáže důvod)
  const [rlabel, rhex] = rarityInfo(p.rarity);
  const isRaffle = p.type === "raffle";
  const after = state.user.points - p.cost_points;
  const thumb = p.image_url
    ? `<div class="cf-img" style="background-image:url('${esc(p.image_url)}')"></div>`
    : `<div class="cf-img" style="background:${gradFor(p)}"><span>${emojiFor(p)}</span></div>`;
  openModal(`
    <div class="modal-body cf-buy" style="--rar:${rhex}">
      <div class="cf-row">
        ${thumb}
        <div class="cf-meta">
          ${rlabel ? `<span class="cf-rar" style="color:${rhex}">${esc(rlabel)}</span>` : ""}
          <div class="cf-name">${esc(p.name)}</div>
          <div class="cf-cost">−${Number(p.cost_points).toLocaleString("cs-CZ")} 🌾</div>
        </div>
      </div>
      <div class="cf-after">
        <span>Zůstatek po koupi</span>
        <b>${Number(after).toLocaleString("cs-CZ")} 🌾</b>
      </div>
      <div class="modal-actions">
        <button class="btn btn-primary btn-block" data-action="buy" data-id="${p.id}">${isRaffle ? "✓ Koupit tiket" : "✓ Potvrdit vyzvednutí"}</button>
        <button class="btn btn-ghost" data-action="close-modal">Zpět</button>
      </div>
    </div>`);
}

/* ---------- LEADERBOARD ---------- */
// Liga (titul) podle POZICE na leaderboardu (rank). [maxRank, emoji, label, cls, mult]; rank > 100 = bez titulu.
const TIERS = [[3, "👑", "KRÁL", "unreal", 10], [10, "⚜️", "ZEMAN", "elite", 5], [30, "🏘️", "RYCHTÁŘ", "gold", 3], [50, "🐂", "HOSPODÁŘ", "silver", 2], [100, "🌱", "NÁDENÍK", "bronze", 2]];
function tierByRank(rank) { return (rank && rank >= 1) ? (TIERS.find(([max]) => rank <= max) || null) : null; }
function tierChip(rank) {
  const t = tierByRank(rank);
  if (!t) return "";   // mimo TOP 100 → bez titulu
  const [, e, label, cls] = t;
  return `<span class="tier-chip tier-${cls}">${e} ${label}</span>`;   // feudální erb (emoji) pro každý tier
}
const TIER_COLOR = { unreal: "#f0c040", elite: "#a78bfa", gold: "#79b8e6", silver: "#e0a878", bronze: "#9ccc65" };
function tierBand(cls) {
  const t = TIERS.find((x) => x[3] === cls); if (!t) return "";
  return `<div class="lb-band" style="color:${TIER_COLOR[cls] || "#8b93a3"}">${t[1]} ${t[2]} <span class="lb-band-line"></span></div>`;
}
function lbSectioned(rows, isMe) {       // seskupí řádky leaderboardu do feudálních lig se záhlavím
  let html = "", last = null;
  for (const r of rows) {
    const t = tierByRank(r.rank); const cls = t ? t[3] : "_none";
    if (cls !== last) { if (t) html += tierBand(cls); last = cls; }
    html += lbRow(r, isMe(r));
  }
  return `<div class="lb-list lb-sectioned">${html}</div>`;
}

function lvlBadge(level) { return level ? `<span class="lvl-badge" title="Úroveň ${level} (nafarmeno)">⭐ ${level}</span>` : ""; }
function profLevelHTML(p) {
  if (!p || !p.level) return "";
  const into = Number(p.level_into || 0).toLocaleString("cs-CZ"), span = Number(p.level_span || 0).toLocaleString("cs-CZ");
  return `<div class="prof-level" title="Úroveň z nafarmeného (earned_total)"><span class="pl-badge">⭐ Úroveň ${p.level}</span><span class="pl-bar"><i style="width:${p.level_pct || 0}%"></i></span><span class="faint pl-xp">${into} / ${span} XP do dalšího</span></div>`;
}
function podiumCard(r, isMe) {
  const medal = r.rank === 1 ? "🥇" : r.rank === 2 ? "🥈" : "🥉";
  const crown = r.rank === 1 ? `<div class="pod-crown" aria-hidden="true"><i></i><b></b><em></em></div>` : "";
  const rays = r.rank === 1 ? `<div class="pod-rays"></div>` : "";
  const sparks = r.rank === 1 ? `<i class="pod-spark s1">✨</i><i class="pod-spark s2">⭐</i><i class="pod-spark s3">✨</i><i class="pod-spark s4">⭐</i>` : "";
  const meTag = isMe ? `<span class="me-tag">TY</span>` : "";
  return `
    <div class="podium-card podium-${r.rank}${isMe ? " me" : ""}" style="--d:${0.05 + r.rank * 0.08}s">
      ${rays}${sparks}<span class="pod-medal">${medal}</span>
      <div class="pod-av-wrap">${crown}${avatarHTML(r.username, r.avatar_url, "pod-av", cosF(r))}</div>
      <a class="pod-name prof-link ${cosN(r)}" href="#/u/${encodeURIComponent(r.username)}">${esc(r.username)}${meTag}</a>
      <div class="pod-badges">${lvlBadge(r.level)}${lbBadges(r)}</div>
      <div class="pod-pts"><span class="pod-num" data-count="${r.points}">${Number(r.points).toLocaleString("cs-CZ")}</span><span>sedláků</span></div>
      ${tierChip(r.rank)}
      <div class="pod-base"><span class="pod-baseshine"></span><span class="pod-basenum">${r.rank}</span></div>
    </div>`;
}

function deltaTag(d) {
  if (d == null) return `<span class="lb-delta flat">·</span>`;       // bez snapshotu = beztečka
  if (d > 0) return `<span class="lb-delta up">▲${d}</span>`;
  if (d < 0) return `<span class="lb-delta down">▼${-d}</span>`;
  return `<span class="lb-delta flat">—</span>`;
}
function lbRow(r, isMe) {
  const meTag = isMe ? `<span class="me-tag">TY</span>` : "";
  const fire = r.climber ? `<span class="lb-fire">🔥 STOUPÁ TÝDNE</span>` : "";
  return `
    <div class="lb-row${isMe ? " me" : ""}${r.climber ? " climber" : ""}" style="--d:${Math.min(r.rank, 24) * 0.025}s">
      <span class="lb-rank">${r.rank}</span>${avatarHTML(r.username, r.avatar_url, "", cosF(r))}
      <div class="lb-id"><a class="uname prof-link ${cosN(r)}" href="#/u/${encodeURIComponent(r.username)}">${esc(r.username)}${meTag}</a> ${prestigeBadge(r.prestige)}<span class="lb-sub">${lvlBadge(r.level)}${lbBadges(r)}${tierChip(r.rank)}${fire}</span></div>
      ${deltaTag(r.delta)}
      <span class="pts">${fmtPts(r.points)}</span>
    </div>`;
}

function animateCounts(root) {
  const els = (root || document).querySelectorAll("[data-count]");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  els.forEach((el) => {
    const target = parseInt(el.dataset.count, 10) || 0;
    if (reduce || target <= 0) { el.textContent = target.toLocaleString("cs-CZ"); return; }
    const dur = 1000, t0 = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))).toLocaleString("cs-CZ");
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}
async function pageLeaderboard() {
  const view = $("#view");
  view.innerHTML = `<div class="page-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap"><div class="ph-mascotgroup"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div><h1>🏆 Žebříček</h1><p class="muted">Nejlepší sběrači sedláků.</p></div></div><a class="btn btn-ghost btn-sm" href="#/sin-slavy">📣 Síň slávy</a></div><div id="lb">${skeletonCards(1)}</div>`;
  try {
    const rows = await api("/leaderboard?limit=100");
    const myName = state.user && state.user.username;
    const isMe = (r) => !!myName && r.username === myName;
    const myRow = rows.find(isMe);
    const top = rows.slice(0, 3);
    const rest = rows.slice(3);
    const order = top.length === 3 ? [1, 0, 2] : [0, 1, 2]; // zlato doprostřed jen při plném pódiu
    const podium = top.length ? `<div class="podium">${order.map((i) => { const r = top[i]; return r ? podiumCard(r, isMe(r)) : ""; }).join("")}</div>` : "";
    const myIdx = rows.findIndex(isMe);
    let predskok = "";
    if (myRow && myIdx > 0) {
      const above = rows[myIdx - 1];
      const need = Math.max(1, above.points - myRow.points + 1);
      predskok = `+${need.toLocaleString("cs-CZ")} sedláků a přeskočíš <b>@${esc(above.username)}</b>`;
    }
    const myDelta = (myRow && myRow.delta != null && myRow.delta !== 0)
      ? ` · <span class="${myRow.delta > 0 ? "lb-delta up" : "lb-delta down"}">${myRow.delta > 0 ? "▲" + myRow.delta : "▼" + (-myRow.delta)} dnes</span>` : "";
    const myBar = myRow
      ? `<div class="lb-mybar"><div class="lb-mybar-av">${avatarHTML(myRow.username, myRow.avatar_url, "", cosF(myRow))}</div><div class="lb-mybar-body"><div class="lb-mybar-top">📍 Tvoje pozice: <b>#${myRow.rank}</b></div>${(predskok || myDelta) ? `<div class="lb-mybar-sub">${predskok}${myDelta}</div>` : ""}</div>${tierChip(myRow.rank)}</div>`
      : (myName ? `<div class="lb-mybar" style="justify-content:center">Zatím nejsi v TOP ${rows.length} – sbírej sedláky! 🌾</div>` : "");
    const list = rest.length ? lbSectioned(rest, isMe) : "";
    const legend = rows.length ? `<div class="lb-legend">${TIERS.slice().reverse().map((t, i, a) => `<span style="color:${TIER_COLOR[t[3]] || "#8b93a3"}">${t[1]} ${t[2][0] + t[2].slice(1).toLowerCase()}</span>${i < a.length - 1 ? '<span class="lb-leg-sep">›</span>' : ""}`).join("")}</div>` : "";
    $("#lb").innerHTML = (rows.length ? `<div class="lb-meta">🌾 TOP ${rows.length} diváků</div>` : "") + podium + myBar + list + legend + (rows.length ? "" : `<div class="empty">Zatím žádní uživatelé.</div>`) + `<div id="seasonEarners"></div><div id="weeklyEarners"></div><div id="chatLeaders"></div>`;
    animateCounts($("#lb"));
    loadSeasonEarners();
    loadWeeklyEarners();
    loadChatLeaders();
  } catch (e) { $("#lb").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function loadWeeklyEarners() {
  const box = document.getElementById("weeklyEarners"); if (!box) return;
  try {
    const d = await api("/leaderboard/weekly");
    const rows = (d && d.rows) || [];
    if (!rows.length) { box.innerHTML = ""; return; }
    const myName = state.user && state.user.username;
    box.innerHTML = `<div class="section-title" style="margin-top:28px">📅 Tento týden — nejvíc nasbíráno <span class="faint" style="font-weight:400;font-size:13px">— kdo tento týden vydělal nejvíc sedláků (zůstatky se NEresetují) 🌾</span></div>
      <div class="lb-list">${rows.slice(0, 15).map((r, i) => {
        const rk = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : (i + 1);
        const mine = !!myName && r.username === myName;
        return `<div class="lb-row${mine ? " me" : ""}"><span class="lb-rank">${rk}</span>${avatarHTML(r.username, r.avatar_url, "", cosF(r))}<div class="lb-id"><a class="uname prof-link ${cosN(r)}" href="#/u/${encodeURIComponent(r.username)}">${esc(r.username)}</a> ${roleBadge(r.role)}</div><span class="pts" style="color:#46d369">+${fmtPts(r.gained)}</span></div>`;
      }).join("")}</div>`;
  } catch (e) { box.innerHTML = ""; }
}

async function loadSeasonEarners() {
  const box = document.getElementById("seasonEarners"); if (!box) return;
  try {
    const d = await api("/leaderboard/season");
    const rows = (d && d.rows) || [];
    if (!rows.length) { box.innerHTML = ""; return; }
    const myName = state.user && state.user.username;
    box.innerHTML = `<div class="section-title" style="margin-top:28px">🏆 Sezóna ${esc(d.season || "")} — TOP sběrači <span class="faint" style="font-weight:400;font-size:13px">— kdo tento měsíc vydělal nejvíc sedláků · reset 1. dne 🌾</span></div>
      <div class="lb-list">${rows.slice(0, 15).map((r, i) => {
        const rk = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : (i + 1);
        const mine = !!myName && r.username === myName;
        return `<div class="lb-row${mine ? " me" : ""}"><span class="lb-rank">${rk}</span>${avatarHTML(r.username, r.avatar_url, "", cosF(r))}<div class="lb-id"><a class="uname prof-link ${cosN(r)}" href="#/u/${encodeURIComponent(r.username)}">${esc(r.username)}</a> ${roleBadge(r.role)}</div><span class="pts" style="color:var(--accent)">+${fmtPts(r.gained)}</span></div>`;
      }).join("")}</div>`;
  } catch (e) { box.innerHTML = ""; }
}

async function loadChatLeaders() {
  const box = document.getElementById("chatLeaders"); if (!box) return;
  try {
    let rows = await api("/top-chatters?period=day"), label = "dne";
    if (!rows.length) { rows = await api("/top-chatters?period=week"); label = "týdne"; }
    if (!rows.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="section-title" style="margin-top:28px">🗣️ Top Chatteři ${label} <span class="faint" style="font-weight:400;font-size:13px">— nejaktivnější v chatu · TOP 3 dne berou bonus 🌾</span></div>
      <div class="lb-list">${rows.map((r, i) => {
        const rk = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : (i + 1);
        return `<div class="lb-row"><span class="lb-rank">${rk}</span>${avatarHTML(r.username, r.avatar_url)}<div class="lb-id">${uLink(r.username)}</div><span class="pts">${r.msgs} 💬</span></div>`;
      }).join("")}</div>`;
  } catch (e) { box.innerHTML = ""; }
}

/* ============================================================
   SOUKROMÉ ZPRÁVY (PM) – staff zakládá, user odpovídá
============================================================ */
function dmBubbles(messages, viewerIsStaff) {
  if (!messages.length) return `<div class="empty">Zatím žádné zprávy.</div>`;
  return `<div class="dm-thread">${messages.map((m) => {
    const mine = (m.from_staff === viewerIsStaff);
    const who = m.from_staff ? `${esc(m.from_name)} ${roleBadge(m.from_role)}` : esc(m.from_name);
    return `<div class="dm-msg ${mine ? "mine" : "other"}"><div class="dm-who">${who}</div><div class="dm-bubble">${esc(m.body)}</div><div class="dm-time">${timeAgo(m.created_at)}</div></div>`;
  }).join("")}</div>`;
}
function dmComposer(mode, uid) {
  return `<div class="dm-composer"><textarea class="input" id="dmInput" rows="2" maxlength="2000" placeholder="Napiš zprávu… (max 2000)"></textarea><button class="btn btn-primary" data-action="dm-send" data-mode="${mode}"${uid != null ? ` data-id="${uid}"` : ""}>Odeslat ➤</button></div>`;
}
async function dmSend(mode, uid) {
  const inp = document.getElementById("dmInput"); if (!inp) return;
  const body = inp.value.trim(); if (!body) { toast("Prázdná zpráva", "error"); return; }
  try {
    if (mode === "reply") await api("/dm/reply", { method: "POST", body: { body } });
    else await api(`/dm/send/${uid}`, { method: "POST", body: { body } });
    inp.value = "";
    if (mode === "reply") dmUserThread(); else dmStaffThread(parseInt(uid, 10));
  } catch (e) { toast(e.message, "error"); }
}
function startDmThreadPoll() {
  if (dmThreadTimer) clearInterval(dmThreadTimer);
  dmThreadTimer = setInterval(() => { if (!document.hidden) pollDmThread(); }, 4000);
}
async function pollDmThread() {
  const thread = document.querySelector(".dm-thread"); if (!thread) return;   // odešel jinam
  try {
    const d = _dmThreadMode === "staff" ? await api(`/dm/admin/thread/${_dmThreadUid}`) : await api("/dm/thread");
    const fresh = d.messages.filter((m) => m.id > _dmLastId);
    if (!fresh.length) return;
    const viewerIsStaff = _dmThreadMode === "staff";
    const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 90;
    fresh.forEach((m) => {                                  // jen DOPLŇ nové (composer + scroll zůstává)
      const mine = (m.from_staff === viewerIsStaff);
      const who = m.from_staff ? `${esc(m.from_name)} ${roleBadge(m.from_role)}` : esc(m.from_name);
      const div = document.createElement("div");
      div.className = `dm-msg ${mine ? "mine" : "other"}`;
      div.innerHTML = `<div class="dm-who">${who}</div><div class="dm-bubble">${esc(m.body)}</div><div class="dm-time">${timeAgo(m.created_at)}</div>`;
      thread.appendChild(div);
    });
    _dmLastId = d.messages[d.messages.length - 1].id;
    if (nearBottom) thread.scrollTop = thread.scrollHeight;
  } catch (e) { }
}
async function pollDmBadge() {
  if (!state.user || document.hidden) return;
  try {
    const r = await api("/dm/unread");
    if (r.count !== state.user.dm_unread) { state.user.dm_unread = r.count; renderHeader(); }
  } catch (e) { }
}
function pageMessages(param) {
  if (!state.user) { navigate("connect"); return; }
  if (canDM(state.user)) return param ? dmStaffThread(parseInt(param, 10)) : dmStaffInbox();
  return dmUserThread();
}
async function dmUserThread() {
  $("#view").innerHTML = `<div class="page-head"><h1>✉️ Zprávy</h1><p class="muted">Soukromé zprávy od týmu ZURYS.</p></div><div id="dmBox">${skeletonCards(1)}</div>`;
  try {
    const d = await api("/dm/thread");
    refreshMe();
    const box = $("#dmBox"); if (!box) return;
    if (!d.messages.length) { box.innerHTML = `<div class="empty"><div class="big">📭</div>Zatím ti nikdo nenapsal. Až vyhraješ skin nebo tě tým osloví, objeví se to tady.</div>`; return; }
    box.innerHTML = `<div class="panel">${dmBubbles(d.messages, false)}${d.can_reply ? dmComposer("reply") : ""}</div>`;
    const t = document.querySelector(".dm-thread"); if (t) t.scrollTop = t.scrollHeight;
    _dmThreadMode = "user"; _dmThreadUid = null;
    _dmLastId = d.messages.length ? d.messages[d.messages.length - 1].id : 0;
    startDmThreadPoll();
  } catch (e) { const b = $("#dmBox"); if (b) b.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function dmStaffInbox() {
  $("#view").innerHTML = `<div class="page-head"><h1>💬 Zprávy</h1><p class="muted">Vlákna s uživateli. Nové vlákno založíš z profilu uživatele.</p></div><div id="dmBox">${skeletonCards(1)}</div>`;
  try {
    const rows = await api("/dm/admin/threads");
    refreshMe();
    const box = $("#dmBox"); if (!box) return;
    if (!rows.length) { box.innerHTML = `<div class="empty"><div class="big">📭</div>Zatím žádná vlákna. Otevři něčí profil → „✉️ Napsat zprávu”.</div>`; return; }
    box.innerHTML = `<div class="dm-inbox">${rows.map((r) => `<a class="dm-trow" href="#/zpravy/${r.user_id}">${avatarHTML(r.username, r.avatar_url)}<div class="dm-trow-body"><div class="dm-trow-top"><b>${esc(r.username)}</b> ${roleBadge(r.role)}${r.unread ? `<span class="dm-badge">${r.unread} nové</span>` : ""}</div><div class="dm-trow-last">${r.last_from_staff ? "Ty: " : ""}${esc((r.last_body || "").slice(0, 90))}</div></div><span class="faint" style="white-space:nowrap">${timeAgo(r.last_at)}</span></a>`).join("")}</div>`;
  } catch (e) { const b = $("#dmBox"); if (b) b.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function dmStaffThread(uid) {
  $("#view").innerHTML = `<div class="page-head"><a href="#/zpravy" class="btn btn-ghost btn-sm">← Zprávy</a></div><div id="dmBox">${skeletonCards(1)}</div>`;
  try {
    const d = await api(`/dm/admin/thread/${uid}`);
    refreshMe();
    const box = $("#dmBox"); if (!box) return;
    box.innerHTML = `<div class="panel"><div class="dm-head">${avatarHTML(d.user.username, d.user.avatar_url)} <a class="prof-link" href="#/u/${encodeURIComponent(d.user.username)}"><b>${esc(d.user.username)}</b></a> ${roleBadge(d.user.role)}</div>${dmBubbles(d.messages, true)}${dmComposer("staff", uid)}</div>`;
    const t = document.querySelector(".dm-thread"); if (t) t.scrollTop = t.scrollHeight;
    _dmThreadMode = "staff"; _dmThreadUid = uid;
    _dmLastId = d.messages.length ? d.messages[d.messages.length - 1].id : 0;
    startDmThreadPoll();
  } catch (e) { const b = $("#dmBox"); if (b) b.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function pageUserProfile(nick) {
  const view = $("#view");
  const name = decodeURIComponent(nick || "");
  if (!name) { navigate("leaderboard"); return; }
  view.innerHTML = `<div class="page-head"><a href="#/leaderboard" class="btn btn-ghost btn-sm">← Žebříček</a></div><div id="up">${skeletonCards(1)}</div>`;
  try {
    const p = await api("/profile/public?nick=" + encodeURIComponent(name));
    const league = userTier(p.rank);
    const wr = p.games_played ? Math.round(p.win_rate * 100) : 0;
    const since = p.created_at ? new Date(p.created_at).toLocaleDateString("cs-CZ") : "—";
    $("#up").innerHTML = `
      <div class="profile-hero">
        ${avatarHTML(p.username, p.avatar_url, "prof-av", cosF(p))}
        <div class="ph-info">
          <h1><span class="${cosN(p)}">${esc(p.username)}</span> ${roleBadge(p.role)} ${prestigeBadge(p.prestige)}</h1>
          <div class="faint">${league ? "★ " + esc(league) + " · " : ""}#${p.rank} v leaderboardu · člen od ${since}</div>
          ${profLevelHTML(p)}
          <div class="ph-badges">${subVipBadges(p) || ""}</div>
        </div>
      </div>
      ${profileBioHTML(p, false)}
      ${canDM(state.user) && state.user.id !== p.id ? `<a href="#/zpravy/${p.id}" class="btn btn-primary" style="margin:16px 0 2px;display:inline-block">✉️ Napsat zprávu</a>` : ""}
      <div class="stat-grid" style="margin-top:18px">
        ${statBox(fmtPts(p.points), "Sedláků teď", "accent")}
        ${statBox(fmtPts(p.earned_total), "Celkem vyděláno")}
        ${statBox(fmtPts(p.spent_total), "Celkem utraceno")}
        ${statBox(fmtPts(p.biggest_win), "Největší zisk")}
      </div>
      <div class="stat-grid" style="margin-top:12px">
        ${statBox(fmtPts(p.farm_xp_total || 0), "Poctive farm XP", "accent")}
        ${statBox(fmtPts(p.farm_gross_total || 0), "Farm gross")}
        ${statBox(fmtPts(p.gambling_net_total || 0), "Gambling net", (p.gambling_net_total || 0) >= 0 ? "" : "warn")}
        ${statBox(fmtPts(p.garden_net_total || 0), "Zahradka net", (p.garden_net_total || 0) > 0 ? "accent" : "")}
      </div>
      <div class="stat-grid" style="margin-top:12px">
        ${statBox(`${p.games_won}/${p.games_played}`, "Duely (výher/her)")}
        ${statBox(p.games_played ? wr + "%" : "—", "Win-rate duelů")}
        ${statBox(p.raffle_wins, "Výher v tombolách 🏆")}
        ${statBox(p.daily_streak || 0, "Denní streak 🔥")}
      </div>
      ${(p.garden_decor && p.garden_decor.length) ? `<div class="section-title" style="margin:20px 0 8px">🏡 Zahrádka <span class="faint" style="font-weight:400;font-size:13px">· ${p.garden_plots} záhonů</span></div><div style="display:flex;gap:8px;flex-wrap:wrap;font-size:26px;line-height:1">${p.garden_decor.map((i) => `<span>${i}</span>`).join("")}</div>` : ""}
      ${showcaseSectionHTML(p.showcase)}${badgesSectionHTML(p.badges)}`;
  } catch (e) { $("#up").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function pageCosmetics() {
  if (!state.user) { navigate("connect"); return; }
  $("#view").innerHTML = `<div class="page-head"><h1>🎨 Kosmetika</h1><p class="muted">Vyšperkuj si jméno a avatar — ukáže se to všude, kde tě ostatní vidí (leaderboard, profil, menu). 💎</p></div><div id="cosWrap">${skeletonCards(1)}</div>`;
  loadCosmetics();
}
async function loadCosmetics() {
  const box = $("#cosWrap"); if (!box) return;
  try {
    const data = await api("/cosmetics");
    const u = state.user;
    const me = (u && u.username) || "Ty";
    const rarMap = { milspec: "MIL-SPEC", restricted: "RESTRICTED", classified: "CLASSIFIED", covert: "COVERT", contraband: "CONTRABAND", legendary: "LEGENDARY" };
    const card = (it) => {
      let preview;
      if (it.type === "name") preview = `<div class="cos-prev"><span class="cos-name-sample ${it.cls}">${esc(me)}</span></div>`;
      else if (it.type === "frame") preview = `<div class="cos-prev">${avatarHTML(me, u && u.avatar_url, "cos-prev-av", it.cls)}</div>`;
      else preview = `<div class="cos-prev"><div class="cos-banner-prev ${it.cls}"></div></div>`;
      const sub = it.sub ? ` <span class="badge badge-role badge-sub-role">SUB</span>` : "";
      const rar = `<span class="cos-rar cos-rar-${it.rarity}">${rarMap[it.rarity] || ""}</span>`;
      let btn;
      if (it.equipped) btn = `<button class="btn btn-success btn-sm btn-block" data-action="cos-equip" data-key="${it.key}">✓ Nasazeno · sundat</button>`;
      else if (it.owned) btn = `<button class="btn btn-primary btn-sm btn-block" data-action="cos-equip" data-key="${it.key}">Nasadit</button>`;
      else if (it.grant_only) btn = `<button class="btn btn-ghost btn-sm btn-block" disabled title="Nedá se koupit – musíš si ho zasloužit">🏆 Jen pro šampiony</button>`;
      else btn = `<button class="btn btn-ghost btn-sm btn-block" data-action="cos-buy" data-key="${it.key}"><span class="coin"></span> ${Number(it.cost).toLocaleString("cs-CZ")}</button>`;
      return `<div class="cos-card${it.equipped ? " equipped" : ""}">${preview}
        <div class="cos-meta"><b>${esc(it.name)}</b>${sub}</div>
        <div class="cos-tags">${rar}${it.owned && !it.equipped ? ` <span class="cos-owned">✓ máš</span>` : ""}</div>
        ${btn}</div>`;
    };
    const groups = [["name", "🎨 Barvy nicku"], ["frame", "🖼️ Rámečky avataru"]];
    box.innerHTML = `<div class="cos-bal">Tvůj zůstatek: <b><span class="coin"></span> ${Number(data.balance).toLocaleString("cs-CZ")}</b> sedláků · kupuješ za sedláky, vlastníš navždy</div>`
      + groups.map(([type, label]) => {
        const items = data.items.filter((i) => i.type === type);
        if (!items.length) return "";
        return `<div class="section-title" style="margin-top:22px">${label}</div><div class="cos-grid">${items.map(card).join("")}</div>`;
      }).join("");
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function buyCosmetic(key) {
  try {
    const r = await api("/cosmetics/buy", { method: "POST", body: { key } });
    if (state.user) state.user.points = r.balance;
    toast(r.message, "success");
    renderHeader();
    loadCosmetics();
  } catch (e) { toast(e.message, "error"); }
}
async function equipCosmetic(key) {
  try {
    const r = await api("/cosmetics/equip", { method: "POST", body: { key } });
    if (state.user) {
      state.user.cos = state.user.cos || { name: "", frame: "", banner: "" };
      state.user.cos[r.type] = r.cls || "";
    }
    toast(r.equipped_key ? "Nasazeno ✓" : "Sundáno", "info");
    renderHeader();
    loadCosmetics();
  } catch (e) { toast(e.message, "error"); }
}
function showcaseSectionHTML(items) {
  if (!items || !items.length) return "";
  const cards = items.map((it) => {
    const [rlabel, rhex] = rarityInfo(it.rarity);
    const bg = it.image_url
      ? `background-image:url('${esc(it.image_url)}');background-size:cover;background-repeat:no-repeat;background-position:center`
      : `background:radial-gradient(circle at 50% 38%, ${rhex}33, transparent 68%)`;
    return `<div style="background:var(--surface-2,#171922);border:1px solid ${rhex}55;border-radius:12px;padding:8px;text-align:center">
      <div style="position:relative;height:84px;border-radius:8px;${bg}">${it.won ? `<span style="position:absolute;top:4px;right:4px;font-size:15px" title="Vyhráno v tombole">🎟️</span>` : ""}</div>
      <div style="font-size:12.5px;font-weight:700;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(it.name)}">${esc(it.name)}</div>
      ${rlabel ? `<div style="font-size:10.5px;font-weight:800;letter-spacing:.04em;color:${rhex}">${rlabel}</div>` : ""}
    </div>`;
  }).join("");
  return `<div class="panel" style="margin-top:18px">
    <div class="section-title" style="margin-top:0">🏆 Vitrína <span class="faint" style="font-weight:400;font-size:13px">— skiny a odměny</span></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:10px">${cards}</div>
  </div>`;
}
function badgesSectionHTML(badges) {
  if (!badges || !badges.length) return "";
  const earned = badges.filter((b) => b.earned).length;
  const roman = (t) => ["", "I", "II", "III", "IV"][t] || "";
  const cells = badges.map((b) => {
    const tl = (b.max_tier > 1 && b.tier > 0) ? " " + roman(b.tier) : "";
    return `<div class="badge ${b.earned ? "earned" : "locked"}" title="${esc(b.desc)}">
      <span class="badge-emoji">${b.emoji}</span>
      <span class="badge-name">${esc(b.name)}${tl}</span>
    </div>`;
  }).join("");
  return `<div class="section-title" style="margin-top:22px">🏅 Odznaky <span class="faint" style="font-weight:400">${earned}/${badges.length}</span></div>
    <div class="badge-grid">${cells}</div>`;
}

/* ---------- EXCHANGE ---------- */
async function pageExchange() {
  const view = $("#view");
  view.innerHTML = `
    <div class="page-head with-mascot"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div class="ph-text"><h1>💱 Směnárna</h1><p class="muted">Pošli sedláky kamarádům nebo uplatni promo kód od streamera.</p></div></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:18px;margin-bottom:24px">${giftCardHTML()}${redeemCardHTML()}</div>
    <div id="exWrap">${skeletonCards(2)}</div>`;
  try {
    const items = await api("/shop/exchange");
    if (!items.length) { $("#exWrap").innerHTML = ""; return; }
    $("#exWrap").innerHTML = `<div class="section-title">🛒 Směnárna</div><div class="exchange-grid">${items.map((p) => {
      const c = canBuy(p);
      let btn;
      if (c.ok) btn = `<button class="btn btn-primary btn-block" data-action="buy" data-id="${p.id}">Směnit za ${fmtPts(p.cost_points)}</button>`;
      else if (c.reason === "login") btn = `<button class="btn btn-kick btn-block" data-action="connect">🟢 Připoj se přes Kick</button>`;
      else if (c.reason === "points") btn = `<button class="btn btn-block" disabled>Nemáš dost bodů</button>`;
      else btn = `<button class="btn btn-block" disabled>Nedostupné</button>`;
      return `<div class="card ex-card">
        <div class="ex-top"><div class="ex-emoji">${emojiFor(p)}</div><div><div style="font-weight:700">${esc(p.name)}</div><div class="faint" style="font-size:13px">${esc(p.category)}</div></div></div>
        <div class="price"><b style="font-size:20px">${Number(p.cost_points).toLocaleString("cs-CZ")}</b><span>bodů</span></div>
        ${btn}</div>`;
    }).join("")}</div>`;
  } catch (e) { $("#exWrap").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function giftCardHTML() {
  if (!state.user) return `<div class="panel"><div class="section-title" style="margin-top:0">🎁 Poslat sedláky kamarádovi</div><p class="muted" style="font-size:13.5px;margin:6px 0 0">Pro darování se <a href="#" data-action="connect" style="color:var(--accent)">připoj přes Kick</a>.</p></div>`;
  return `<div class="panel">
    <div class="section-title" style="margin-top:0">🎁 Poslat sedláky kamarádovi</div>
    <p class="faint" style="font-size:12.5px;margin:4px 0 12px">Pošli část svých sedláků jinému divákovi. Každý dar <b>schvaluje admin</b> — body se ti zatím zablokují a při zamítnutí vrátí. 🛡️</p>
    <form data-submit="gift">
      <div class="field"><label>Komu (Kick nick)</label><input class="input" id="giftUser" placeholder="např. kamarad123" autocomplete="off"></div>
      <div class="field"><label>Kolik sedláků</label><input class="input" id="giftAmount" type="number" min="1" placeholder="např. 500"></div>
      <div class="field"><label>Důvod <span class="faint" style="font-weight:400">(nepovinné — admin to uvidí)</span></label><input class="input" id="giftNote" maxlength="120" placeholder="např. dík za pomoc, prohraná sázka…" autocomplete="off"></div>
      <button class="btn btn-primary btn-block" type="submit">🎁 Poslat sedláky</button>
    </form>
  </div>`;
}
function redeemCardHTML() {
  if (!state.user) return `<div class="panel" id="redeemCard"><div class="section-title" style="margin-top:0">🎫 Uplatnit promo kód</div><p class="muted" style="font-size:13.5px;margin:6px 0 0">Pro uplatnění se <a href="#" data-action="connect" style="color:var(--accent)">připoj přes Kick</a>.</p></div>`;
  return `<div class="panel" id="redeemCard">
    <div class="section-title" style="margin-top:0">🎫 Uplatnit promo kód</div>
    <p class="faint" style="font-size:12.5px;margin-bottom:14px">🎉 Zadej kód od streamera a získej sedláky zdarma!</p>
    <form data-submit="redeem">
      <div class="field"><label>Kód poukazu</label><input class="input" id="redeemCode" placeholder="např. ZURYS1000" autocomplete="off" style="text-transform:uppercase"></div>
      <button class="btn btn-accent btn-block" type="submit">🎫 Uplatnit kód</button>
    </form>
    <div id="redeemResult" style="margin-top:14px"></div>
  </div>`;
}
async function doGift() {
  const username = ($("#giftUser")?.value || "").trim().replace(/^@/, "");
  const amount = parseInt($("#giftAmount")?.value || "0", 10);
  const note = ($("#giftNote")?.value || "").trim();
  if (username.length < 2) { toast("Zadej příjemce (Kick nick).", "error"); return; }
  if (!amount || amount < 1) { toast("Zadej kolik sedláků poslat.", "error"); return; }
  if (state.user && amount > state.user.points) { toast("Tolik sedláků nemáš.", "error"); return; }
  if (!confirm(`Poslat žádost o dar ${amount} sedláků uživateli ${username}?\n\nBody se ti zablokují, než to admin schválí. Při zamítnutí se vrátí.`)) return;
  try {
    const r = await api("/exchange/gift", { method: "POST", body: { username, amount, note } });
    if (state.user) state.user.points = r.balance;
    toast(r.message, r.pending ? "info" : "success");
    renderHeader();
    pageExchange();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- REDEEM (sjednoceno do Exchange) ---------- */
// „Uplatnit kód" žije na stránce Exchange. Route #/redeem sem naviguje
// a rovnou skočí na kartu + zvýrazní ji, ať to lidi snadno najdou.
function pageRedeem() {
  pageExchange();
  requestAnimationFrame(() => {
    const card = document.getElementById("redeemCard");
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("flash-highlight"); void card.offsetWidth; card.classList.add("flash-highlight");
    const inp = document.getElementById("redeemCode");
    if (inp) setTimeout(() => { try { inp.focus(); } catch (e) {} }, 420);
  });
}
async function doRedeem() {
  const code = $("#redeemCode").value.trim();
  if (!code) { toast("Zadej kód.", "error"); return; }
  if (!state.user) { toast("Nejdřív se připoj přes Kick.", "error"); openConnect(); return; }
  try {
    const r = await api("/redeem", { method: "POST", body: { code, t0: window._pageT0 || 0 } });
    state.user.points = r.balance;
    toast(r.message, "success");
    renderHeader();
    const ok = $("#redeemResult"); if (ok) ok.innerHTML = `<div class="panel ok">✅ ${esc(r.message)}<br><span class="muted">Nový zůstatek: <b>${fmtPts(r.balance)}</b></span></div>`;
    const inp = $("#redeemCode"); if (inp) inp.value = "";
  } catch (e) {
    toast(e.message, "error");
    const bad = $("#redeemResult"); if (bad) bad.innerHTML = `<div class="panel bad">⚠️ ${esc(e.message)}</div>`;
  }
}

/* ---------- FAQ ---------- */
const FAQ = [
  ["Jak získám body?", "Body jsou věrnostní měna – dostáváš je od streamera (admina) za sledování, aktivitu nebo uplatnění kódů. <b>Body se nedají koupit za peníze.</b>"],
  ["Co si můžu za body koupit?", "Najdeš to v sekci <a href='#/shop' style='color:var(--accent-2)'>Shop</a> – od pozdravů na streamu přes Discord role až po fyzické odměny a tomboly."],
  ["Co znamená „Jen sub” a „Jen VIP”?", "Některé odměny jsou jen pro předplatitele (sub) nebo VIP diváky. Koupit je může pouze uživatel s danou rolí."],
  ["Jak funguje košík?", "Do košíku můžeš přidat víc odměn a koupit je najednou – body se odečtou za celý součet a vytvoří se objednávky."],
  ["Jak funguje tombola?", "U tomboly si kupuješ tikety. Čím víc tiketů, tím větší šance. Streamer pak vylosuje výherce z účastníků."],
  ["Co se stane po nákupu?", "Vytvoří se objednávka se stavem „čeká na vyřízení”. Streamer ji následně vyřídí (např. ti předá odměnu)."],
  ["Jak uplatním kód?", "V sekci <a href='#/redeem' style='color:var(--accent-2)'>Směnárna → Uplatnit kód</a> zadej kód. Platný a nepoužitý kód ti připíše body nebo odemkne odměnu."],
];
function pageFaq() {
  const view = $("#view");
  view.innerHTML = `<div class="page-head"><h1>❓ Časté dotazy</h1><p class="muted">Vše, co potřebuješ vědět o bodech a odměnách.</p></div>
    <div class="accordion">${FAQ.map((q, i) => `
      <div class="acc-item" data-acc="${i}">
        <button class="acc-q" data-action="acc-toggle" data-i="${i}">${esc(q[0])}<span class="arrow">▾</span></button>
        <div class="acc-a"><div class="acc-a-inner">${q[1]}</div></div>
      </div>`).join("")}</div>`;
}

/* ---------- PRAVIDLA ---------- */
function pageRules() {
  const view = $("#view");
  const ul = 'style="margin:8px 0 2px;padding-left:20px;line-height:1.7"';
  view.innerHTML = `
    <div class="page-head"><h1>📜 Pravidla</h1><p class="muted">Férová pravidla ZURYS Drop Areny. Účastí a nákupem s nimi souhlasíš.</p></div>
    <div class="panel">
      <div class="section-title" style="margin-top:0">🪙 Body (sedláci)</div>
      <ul ${ul}>
        <li>Sedláci jsou <b>jen odměna za aktivitu</b> (sledování streamu, chat, denní bonus, dropy). <b>Nedají se koupit za peníze</b> a <b>nemají peněžní hodnotu</b>.</li>
        <li>Sedláci patří k tvému účtu a <b>nevyplácí se v penězích</b>. Můžeš je <b>darovat jinému divákovi</b> (Směnárna) — ale ne na účet ze stejné sítě/zařízení (anti-farma).</li>
      </ul>
      <div class="section-title">🎁 Odměny a výplata</div>
      <ul ${ul}>
        <li>CS skiny posílá streamer <b>ručně přes Steam</b> – proto si v profilu nastav <b>platný Steam trade link</b>. Bez něj skin nekoupíš.</li>
        <li>Výplata odměn je <b>na uvážení streamera</b>; u fyzických věcí (např. hardware) platí podmínky daného dne.</li>
        <li>Sklad a dostupnost se mění, některé odměny jsou <b>časově omezené</b>.</li>
      </ul>
      <div class="section-title">🛡️ Fér hra (anticheat)</div>
      <ul ${ul}>
        <li>Zakázané: <b>multiúčty</b>, <b>boti/automatizace</b>, <b>VPN/proxy kvůli obcházení limitů</b> a jakékoli <b>farmení bodů</b> mimo běžnou aktivitu.</li>
        <li>Porušení = <b>ban účtu bez náhrady bodů i odměn</b>. Sdílené IP a podezřelé chování systém vyhodnocuje automaticky.</li>
      </ul>
      <div class="section-title">🔒 Co o tobě ukládáme</div>
      <ul ${ul}>
        <li>Jen nutné k provozu: <b>Kick jméno</b> (přihlášení), <b>e-mail</b> (jen u e-mailové registrace) a <b>IP adresa</b> kvůli bezpečnosti/anticheatu. Data <b>neprodáváme ani nesdílíme</b>.</li>
        <li>Chceš smazat účet nebo data? Napiš streamerovi.</li>
      </ul>
      <div class="section-title">📌 Závěrem</div>
      <ul ${ul}>
        <li>Pravidla se můžou <b>změnit</b> (vylepšení nebo reakce na zneužití). Velké změny streamer oznámí.</li>
        <li>Dotazy? Streamer na <b>kick.com/zurys1337</b>, nebo se podívej do <a href="#/faq">FAQ</a>.</li>
      </ul>
    </div>`;
}

/* ---------- NOVINKY (patch notes / changelog) ---------- */
const NEWS_TAG = {
  new:     { ico: "🆕", label: "Nové",      cls: "nt-new" },
  improve: { ico: "🛠️", label: "Vylepšení", cls: "nt-improve" },
  fix:     { ico: "🐛", label: "Oprava",    cls: "nt-fix" },
};
function newsDate(iso, opts) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString("cs-CZ", opts || { day: "numeric", month: "long", year: "numeric" });
}
function newsItemHTML(n) {
  const t = NEWS_TAG[n.tag] || NEWS_TAG.new;
  return `<div class="news-item">
      <div class="news-rail"><span class="news-dot ${t.cls}">${t.ico}</span></div>
      <div class="news-card">
        <div class="news-top"><span class="news-tag ${t.cls}">${t.label}</span><span class="news-date">${newsDate(n.created_at)}</span></div>
        <div class="news-title">${esc(n.title)}</div>
        ${n.body ? `<div class="news-text">${esc(n.body)}</div>` : ""}
      </div>
    </div>`;
}
async function openNewsPanel() {
  const root = $("#newsPanelRoot"); if (!root) return;
  root.innerHTML = `<div class="news-panel-backdrop" data-action="close-news"></div>
    <aside class="news-panel" role="dialog" aria-label="Novinky">
      <div class="news-panel-head">
        <div class="news-panel-title">📣 Novinky</div>
        <button class="modal-close" data-action="close-news" title="Zavřít">✕</button>
      </div>
      <div class="news-panel-body" id="newsPanelBody"><div class="skeleton" style="height:80px;margin-bottom:10px"></div><div class="skeleton" style="height:80px"></div></div>
    </aside>`;
  root.classList.add("open");
  try {
    const r = await api("/news");
    const notes = r.notes || [];
    const body = $("#newsPanelBody"); if (!body) return;
    body.innerHTML = notes.length
      ? `<div class="news-tl">${notes.map(newsItemHTML).join("")}</div>`
      : `<div class="empty"><div class="big">📣</div>Zatím žádné novinky.</div>`;
  } catch (e) {
    const body = $("#newsPanelBody"); if (body) body.innerHTML = `<div class="empty">Novinky se nepodařilo načíst.</div>`;
  }
}

/* ---- Notifikace (zvoneček) ---- */
async function openNotifs() {
  if (!state.user) { navigate("connect"); return; }
  const root = $("#notifPanelRoot"); if (!root) return;
  root.innerHTML = `<div class="news-panel-backdrop" data-action="close-notifs"></div>
    <aside class="news-panel" role="dialog" aria-label="Notifikace">
      <div class="news-panel-head">
        <div class="news-panel-title">🔔 Notifikace</div>
        <button class="modal-close" data-action="close-notifs" title="Zavřít">✕</button>
      </div>
      <div class="news-panel-body" id="notifPanelBody"><div class="skeleton" style="height:64px;margin-bottom:10px"></div><div class="skeleton" style="height:64px"></div></div>
    </aside>`;
  root.classList.add("open");
  try {
    const r = await api("/notifications");
    const items = r.items || [];
    const body = $("#notifPanelBody"); if (!body) return;
    body.innerHTML = items.length
      ? `<div class="notif-list">${items.map(notifItemHTML).join("")}</div>`
      : `<div class="empty"><div class="big">🔔</div>Zatím žádné notifikace.</div>`;
    if (r.unread) {                                   // označit přečtené + shodit badge
      api("/notifications/read", { method: "POST" }).catch(() => {});
      if (state.user) { state.user.notif_unread = 0; renderHeader(); }
    }
  } catch (e) {
    const body = $("#notifPanelBody"); if (body) body.innerHTML = `<div class="empty">Notifikace se nepodařilo načíst.</div>`;
  }
}
function closeNotifs() { const root = $("#notifPanelRoot"); if (root) { root.classList.remove("open"); root.innerHTML = ""; } }
function notifItemHTML(n) {
  const clickable = n.link ? ` data-action="notif-go" data-link="${esc(n.link)}" style="cursor:pointer"` : "";
  return `<div class="notif-item${n.read ? "" : " unread"}"${clickable}>
    <div class="notif-ico">${esc(n.icon || "🔔")}</div>
    <div class="notif-main">
      <div class="notif-title">${esc(n.title)}</div>
      ${n.body ? `<div class="notif-body">${esc(n.body)}</div>` : ""}
      <div class="notif-time">${timeAgo(n.created_at)}</div>
    </div>
  </div>`;
}
async function pollNotifBadge() {
  if (!state.user || document.hidden) return;
  try {
    const r = await api("/notifications/unread");
    if (r.count !== state.user.notif_unread) { state.user.notif_unread = r.count; renderHeader(); }
  } catch (e) { }
}
function closeNewsPanel() {
  const root = $("#newsPanelRoot"); if (!root) return;
  root.classList.remove("open");
  root.innerHTML = "";
}
async function pageNews() {
  const view = $("#view");
  const head = `<div class="page-head"><h1>📣 Novinky</h1><p class="muted">Co je nového na ZURYS Drop Areně — pořád na něčem makáme. 🛠️</p></div>`;
  view.innerHTML = head + `<div class="news-tl"><div class="skeleton" style="height:88px;margin-bottom:12px"></div><div class="skeleton" style="height:88px"></div></div>`;
  let notes = [];
  try { const r = await api("/news"); notes = r.notes || []; }
  catch (e) { view.innerHTML = head + `<div class="empty"><div class="big">📣</div>Novinky se teď nepodařilo načíst.</div>`; return; }
  if (!notes.length) {
    view.innerHTML = head + `<div class="empty"><div class="big">📣</div>Zatím žádné novinky — ale brzy něco přibude!</div>`;
    return;
  }
  const rows = notes.map(newsItemHTML).join("");
  view.innerHTML = head + `<div class="news-tl">${rows}</div>`;
}

/* --- Admin: Novinky (patch notes) --- */
async function adminNews() {
  const box = $("#adminContent");
  let notes = [];
  try { notes = await api("/admin/news"); }
  catch (e) { box.innerHTML = `<div class="panel bad">Novinky se nepodařilo načíst.</div>`; return; }
  const tagOpts = `<option value="new">🆕 Nové</option><option value="improve">🛠️ Vylepšení</option><option value="fix">🐛 Oprava</option>`;
  const form = `<div class="panel" style="margin-bottom:18px">
    <div class="section-title" style="margin-top:0">➕ Přidat novinku</div>
    <form class="form" data-submit="news-create">
      <div class="field-row">
        <div class="field" style="flex:2;min-width:180px"><label>Titulek</label><input class="input" id="nt_title" maxlength="120" placeholder="Co je nového?"></div>
        <div class="field" style="flex:1;min-width:130px"><label>Typ</label><select class="select" id="nt_tag">${tagOpts}</select></div>
      </div>
      <div class="field"><label>Popis (volitelně)</label><textarea class="input" id="nt_body" rows="2" maxlength="2000" placeholder="Krátký popis změny pro diváky…"></textarea></div>
      <button class="btn btn-primary" type="submit">📣 Publikovat novinku</button>
    </form>
  </div>`;
  const broadcast = `<div class="panel" style="margin-bottom:18px">
    <div class="section-title" style="margin-top:0">🔔 Rozeslat oznámení (push)</div>
    <p class="muted" style="font-size:12.5px;margin:0 0 12px">Pošle in-app notifikaci na zvoneček vybranému segmentu. Nevratné — doručí se hned všem.</p>
    <div class="field-row">
      <div class="field" style="flex:0 0 64px"><label>Ikona</label><input class="input" id="bc_icon" maxlength="4" value="📣" style="text-align:center"></div>
      <div class="field" style="flex:2;min-width:170px"><label>Titulek</label><input class="input" id="bc_title" maxlength="120" placeholder="Limitka je živá! 🔥"></div>
      <div class="field" style="flex:1;min-width:150px"><label>Komu</label><select class="select" id="bc_segment"><option value="all">Všem</option><option value="active">Aktivním (14 d)</option><option value="subs">Jen subům</option></select></div>
    </div>
    <div class="field"><label>Text (volitelně)</label><textarea class="input" id="bc_body" rows="2" maxlength="300" placeholder="Krátká zpráva pro diváky…"></textarea></div>
    <div class="field"><label>Odkaz (volitelně)</label><input class="input" id="bc_link" maxlength="80" placeholder="#/shop"></div>
    <button class="btn btn-primary" data-action="broadcast-send">🔔 Rozeslat oznámení</button>
  </div>`;
  const list = notes.length ? `<div class="lb-list">${notes.map((n) => {
    const t = NEWS_TAG[n.tag] || NEWS_TAG.new;
    return `<div class="lb-row">
      <span class="news-dot ${t.cls}" style="flex:0 0 auto">${t.ico}</span>
      <div style="min-width:0;flex:1">
        <div style="font-weight:800">${esc(n.title)}${n.published ? "" : ' <span class="faint">(skryté)</span>'}</div>
        <div class="faint" style="font-size:12.5px">${newsDate(n.created_at, { day: "numeric", month: "numeric", year: "numeric" })}${n.body ? " · " + esc(n.body) : ""}</div>
      </div>
      <button class="btn btn-danger btn-sm" data-action="news-delete" data-id="${n.id}" style="margin-left:auto" title="Smazat">✕</button>
    </div>`;
  }).join("")}</div>` : `<div class="empty"><div class="big">📣</div>Zatím žádné novinky.</div>`;
  box.innerHTML = broadcast + form + `<div class="section-title">Publikované novinky (${notes.length})</div>` + list;
}
async function sendBroadcast() {
  const title = ($("#bc_title").value || "").trim();
  if (title.length < 2) { toast("Zadej titulek oznámení.", "error"); return; }
  const segment = $("#bc_segment").value || "all";
  const segLbl = { all: "VŠEM", active: "aktivním (14 d)", subs: "jen subům" }[segment] || "všem";
  if (!confirm(`Rozeslat „${title}" ${segLbl}? Nejde vzít zpět.`)) return;
  const body = { title, body: ($("#bc_body").value || "").trim(),
                 icon: ($("#bc_icon").value || "📣").trim(), link: ($("#bc_link").value || "").trim(), segment };
  try {
    const r = await api("/admin/news/broadcast", { method: "POST", body });
    toast(`🔔 Posláno ${Number(r.sent).toLocaleString("cs-CZ")} ${r.sent === 1 ? "uživateli" : "uživatelům"}.`, "success");
    $("#bc_title").value = ""; $("#bc_body").value = ""; $("#bc_link").value = "";
  } catch (e) { toast(e.message, "error"); }
}
async function createNote() {
  const title = ($("#nt_title").value || "").trim();
  const body = ($("#nt_body").value || "").trim();
  const tag = $("#nt_tag").value || "new";
  if (title.length < 2) { toast("Zadej titulek novinky.", "error"); return; }
  try {
    await api("/admin/news", { method: "POST", body: { title, body, tag, published: true } });
    toast("Novinka publikována 📣", "success");
    adminNews();
  } catch (e) { toast(e.message, "error"); }
}
async function deleteNote(id) {
  if (!confirm("Smazat tuhle novinku?")) return;
  try { await api(`/admin/news/${id}`, { method: "DELETE" }); toast("Novinka smazána.", "info"); adminNews(); }
  catch (e) { toast(e.message, "error"); }
}

/* ---------- CART ---------- */
function pageCart() {
  const view = $("#view");
  if (!state.cart.length) {
    view.innerHTML = `<div class="page-head"><h1>🛒 Košík</h1></div><div class="empty"><div class="big">🛒</div>Košík je prázdný.<br><a class="btn btn-primary" href="#/shop" style="margin-top:14px">Procházet odměny</a></div>`;
    return;
  }
  const rows = state.cart.map((i) => `
    <div class="lb-row">
      <div class="ex-emoji">${emojiFor(i)}</div>
      <div style="min-width:0"><div style="font-weight:700">${esc(i.name)}</div><div class="faint" style="font-size:13px">${fmtPts(i.cost)} / ks</div></div>
      <div style="display:flex;align-items:center;gap:6px;margin-left:auto">
        <button class="btn btn-ghost btn-sm" data-action="qty" data-id="${i.id}" data-d="-1">−</button>
        <b style="min-width:24px;text-align:center">${i.qty}</b>
        <button class="btn btn-ghost btn-sm" data-action="qty" data-id="${i.id}" data-d="1">+</button>
      </div>
      <div style="font-weight:800;color:var(--accent-2);min-width:80px;text-align:right">${fmtPts(i.cost * i.qty)}</div>
      <button class="btn btn-danger btn-sm" data-action="cart-remove" data-id="${i.id}">✕</button>
    </div>`).join("");

  const total = cartTotal();
  const afford = state.user ? state.user.points >= total : false;
  let checkoutBtn;
  if (!state.user) checkoutBtn = `<button class="btn btn-kick btn-block" data-action="connect">🟢 Pro nákup se připoj přes Kick</button>`;
  else if (!afford) checkoutBtn = `<button class="btn btn-block" disabled>Nemáš dost bodů (máš ${fmtPts(state.user.points)})</button>`;
  else checkoutBtn = `<button class="btn btn-primary btn-block" data-action="checkout">Koupit vše za ${fmtPts(total)}</button>`;

  view.innerHTML = `
    <div class="page-head row-between"><h1>🛒 Košík</h1><button class="btn btn-ghost btn-sm" data-action="cart-clear">Vyprázdnit</button></div>
    <div class="lb-list">${rows}</div>
    <div class="panel" style="margin-top:18px">
      <div class="row-between" style="margin-bottom:14px"><span class="section-title" style="margin:0">Celkem</span><span style="font-size:24px;font-weight:800;color:var(--accent-2)">${fmtPts(total)}</span></div>
      ${checkoutBtn}
      ${state.user ? `<div class="faint" style="margin-top:10px;text-align:center">Tvůj zůstatek: ${fmtPts(state.user.points)}</div>` : ""}
    </div>`;
}
async function doCheckout() {
  try {
    const items = state.cart.map((i) => ({ product_id: i.id, qty: i.qty }));
    const r = await api("/cart/checkout", { method: "POST", body: { items, t0: window._pageT0 || 0 } });
    state.user.points = r.balance;
    clearCart();
    toast(r.message, "success");
    navigate("profile");
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- PROFILE ---------- */
function selfExcludeBlock(u) {
  const until = u.gamble_block_until;
  if (until === "permanent") {
    return `<p class="muted" style="font-size:13px;line-height:1.6">🔒 Máš <b style="color:var(--text)">trvalé sebevyloučení</b> ze všech sázek (duely, piškvorky, blackjack, predikce). Zrušit to může jen admin.</p>`;
  }
  if (until) {
    const d = new Date(until).toLocaleString("cs-CZ");
    return `<p class="muted" style="font-size:13px;line-height:1.6">🔒 Sázení máš uzamčené <b style="color:var(--text)">do ${d}</b>. Nejde to zrušit dřív — můžeš jen prodloužit nebo zpřísnit.</p>
      <div class="se-btns"><button class="btn btn-ghost btn-sm" data-action="self-excl" data-dur="30d">Prodloužit (30 dní)</button><button class="btn btn-danger btn-sm" data-action="self-excl" data-dur="perm">Napořád</button></div>`;
  }
  return `<p class="muted" style="font-size:13px;line-height:1.6">Můžeš se dobrovolně zamknout ze <b style="color:var(--text)">všech sázek</b> (duely, piškvorky, blackjack, predikce) — jako u Tipsportu. <b>Nejde to zrušit dřív</b>, tak si to rozmysli. Body ti zůstanou, jen nebudeš moct sázet.</p>
    <div class="se-btns">
      <button class="btn btn-ghost btn-sm" data-action="self-excl" data-dur="1d">1 den</button>
      <button class="btn btn-ghost btn-sm" data-action="self-excl" data-dur="7d">7 dní</button>
      <button class="btn btn-ghost btn-sm" data-action="self-excl" data-dur="30d">30 dní</button>
      <button class="btn btn-danger btn-sm" data-action="self-excl" data-dur="perm">Napořád</button>
    </div>`;
}

async function selfExclude(dur) {
  const labels = { "1d": "1 den", "7d": "7 dní", "30d": "30 dní", "perm": "NATRVALO" };
  const warn = dur === "perm"
    ? "Opravdu se NATRVALO vyloučit ze všech sázek? Zrušit to půjde jen přes admina."
    : `Opravdu se vyloučit ze sázek na ${labels[dur]}? Nepůjde to zrušit dřív.`;
  if (!confirm(warn)) return;
  try {
    const r = await api("/me/self-exclude", { method: "POST", body: { duration: dur } });
    if (state.user) state.user.gamble_block_until = r.gamble_block_until;
    toast("Sázení uzamčeno 🔒", "success");
    pageProfile();
  } catch (e) { toast(e.message, "error"); }
}

async function showWrapped() {
  if (!state.user) return;
  let d;
  try { d = await api("/profile/public?nick=" + encodeURIComponent(state.user.username)); }
  catch (e) { toast(e.message, "error"); return; }
  const wr = d.win_rate != null ? Math.round(d.win_rate * 100) : 0;
  const stat = (icon, label, val) => `<div class="wr-stat"><div class="wr-ic">${icon}</div><div class="wr-v">${val}</div><div class="wr-l">${label}</div></div>`;
  const ov = document.createElement("div");
  ov.className = "raffle-reveal";       // sdílí ztmavené pozadí
  ov.innerHTML = `<div class="wr-card">
    <div class="wr-head">🎁 Moje čísla na Zurys</div>
    <div class="wr-sub">${esc(d.username)} · v komunitě ${memberSince(d.created_at)}</div>
    <div class="wr-grid">
      ${stat("⭐", "úroveň", "Lvl " + d.level)}
      ${stat("🌾", "nafarmeno", fmtPts(d.earned_total))}
      ${stat("💰", "největší výhra", fmtPts(d.biggest_win))}
      ${stat("🎮", "her odehráno", d.games_played)}
      ${stat("🎯", "win-rate", wr + " %")}
      ${stat("🔪", "vyhrané tomboly", d.raffle_wins)}
      ${stat("🔥", "denní série", d.daily_streak + " dní")}
      ${stat("🏆", "pozice", "#" + d.rank)}
    </div>
    <div class="wr-foot">📸 Screenshot a flexni · zurys.live</div>
  </div>`;
  ov.addEventListener("click", () => ov.remove());
  document.body.appendChild(ov);
}
function pageProfile() {
  if (!state.user) { navigate("connect"); return; }
  const u = state.user;
  const view = $("#view");
  view.innerHTML = `
    <div class="page-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap"><h1>👤 Můj profil</h1><button class="btn btn-accent btn-sm" data-action="my-wrapped">🎁 Moje čísla</button></div>
    <div class="panel">
      <div class="profile-head">
        ${avatarHTML(u.username, u.avatar_url, "", cosF(u))}
        <div><div style="font-size:22px;font-weight:800"><span class="${cosN(u)}">${esc(u.username)}</span> ${roleBadge(u.role)} ${subVipBadges(u)} ${prestigeBadge(u.prestige)}</div><div class="muted">${u.kick_username ? "🟢 " + esc(u.kick_username) : esc(u.email || "")}</div><div class="muted" style="font-size:12.5px;margin-top:3px">🎂 V komunitě <b style="color:var(--text)">${memberSince(u.created_at)}</b>${loyaltyBadge(u.created_at)}</div>${profLevelHTML(u)}</div>
        <div class="profile-points"><div class="v">${fmtPts(u.points)}</div><div class="faint">aktuální zůstatek</div></div>
      </div>
      <div class="prof-look-strip">
        <div class="muted" style="font-size:13px;min-width:0">🎨 <b style="color:var(--text)">Můj vzhled</b> — barvy nicku a rámečky avataru (vidí to všichni)</div>
        <a href="#/kosmetika" class="btn btn-primary btn-sm">Upravit vzhled →</a>
      </div>
      <div id="myBio" style="margin:8px 0 4px">${skeletonCards(1)}</div>
      <div id="myBadges" style="margin:6px 0 18px"></div>
      <div class="sub-tabs">
        <button class="active" data-action="prof-tab" data-tab="orders">📦 Moje objednávky</button>
        <button data-action="prof-tab" data-tab="points">📊 Historie bodů</button>
      </div>
      <div id="profContent">${skeletonCards(1)}</div>
    </div>
    <div class="panel">
      <div class="section-title" style="margin-top:0">🎁 Steam trade link</div>
      <p class="muted" style="margin:2px 0 12px;font-size:13px;line-height:1.55">
        Vlož svůj <b>Steam trade odkaz</b> — podle něj ti streamer pošle vyhrané skiny.
        Najdeš ho na Steamu → <b>Inventory</b> → <b>Trade Offers</b> →
        <b>„Who can send me Trade Offers?”</b> → dole zkopíruj celý odkaz.
      </p>
      <div class="field">
        <label>Tvůj trade link</label>
        <input class="input" id="tradeUrl" autocomplete="off" spellcheck="false"
               placeholder="https://steamcommunity.com/tradeoffer/new/?partner=…&token=…"
               value="${esc(u.steam_trade_url || "")}">
      </div>
      <div class="row-between" style="margin-top:10px;gap:10px;flex-wrap:wrap">
        <span id="tradeMsg" class="muted" style="font-size:13px">${u.steam_trade_url ? "✓ Uloženo" : "Zatím nevyplněno"}</span>
        <button class="btn btn-primary" data-action="save-trade">💾 Uložit trade link</button>
      </div>
    </div>
    <div id="myPrestige"></div>
    <div class="panel">
      <div class="section-title" style="margin-top:0">🔒 Zodpovědné sázení</div>
      ${selfExcludeBlock(u)}
      <div id="myWagerLimit"></div>
    </div>`;
  loadProfTab("orders");
  loadMyBadges();
  loadMyBio();
  loadPrestige();
  loadWagerLimit();
}
async function loadWagerLimit() {
  const box = document.getElementById("myWagerLimit"); if (!box || !state.user) return;
  try {
    const s = await api("/wager-limit");
    const has = s.limit > 0;
    const pct = has ? Math.min(100, Math.round(s.wagered_today * 100 / s.limit)) : 0;
    const bar = has
      ? `<div class="wl-bar"><div class="wl-fill" style="width:${pct}%"></div></div><div class="faint" style="font-size:12.5px;margin-top:5px">Dnes prosázeno <b>${fmtPts(s.wagered_today)}</b> z <b>${fmtPts(s.limit)}</b> · zbývá ${fmtPts(s.remaining || 0)}</div>`
      : `<div class="faint" style="font-size:12.5px">Dnes prosázeno <b>${fmtPts(s.wagered_today)}</b> · žádný denní limit.</div>`;
    const pending = (s.pending !== null && s.pending !== undefined) ? `<div style="font-size:12px;color:#e0a857;margin-top:5px">⏳ Od zítřka: ${s.pending > 0 ? "limit " + fmtPts(s.pending) : "bez limitu"} (zvýšení/zrušení platí až další den)</div>` : "";
    box.innerHTML = `<div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
      <div style="font-weight:800;margin-bottom:6px">💸 Denní limit sázek</div>
      <p class="muted" style="font-size:12.5px;line-height:1.5;margin:0 0 10px">Strop kolik můžeš za den prosázet (Mines, predikce, duely, blackjack…). <b>Snížit jde hned</b>, zvýšit/zrušit až <b>další den</b> — ať to nejde obejít v zápalu hry.</p>
      ${bar}${pending}
      <div class="toolbar" style="margin-top:10px">
        <input class="input input-sm" id="wlInput" type="number" min="0" placeholder="Limit (0 = bez limitu)" value="${s.limit || ""}" style="max-width:190px">
        <button class="btn btn-sm btn-primary" data-action="wl-save">Uložit limit</button>
      </div>
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function saveWagerLimit() {
  const inp = document.getElementById("wlInput");
  const v = Math.max(0, parseInt(inp && inp.value, 10) || 0);
  try {
    const r = await api("/wager-limit", { method: "POST", body: { limit: v } });
    toast(r.applied === "now" ? (v ? `Denní limit ${fmtPts(v)} nastaven 🛡️` : "Limit zrušen") : "Změna se projeví až zítra ⏳", "success");
    loadWagerLimit();
  } catch (e) { toast(e.message, "error"); }
}
const FAV_GAMES = ["", "Mines", "Kolo štěstí", "Piškvorky", "Duely", "Blackjack", "Predikce", "Tomboly"];
let _myProfileBio = { bio: "", fav_game: "" };
function profileBioHTML(p, editable) {
  const bio = (p.bio || "").trim(), fav = (p.fav_game || "").trim();
  if (!bio && !fav && !editable) return "";
  const favChip = fav ? `<span class="bio-fav">🎮 ${esc(fav)}</span>` : "";
  const txt = bio ? `<div class="bio-text">${esc(bio)}</div>` : (editable ? `<div class="bio-text faint">Zatím žádné bio — řekni něco o sobě ✍️</div>` : "");
  return `<div class="profile-bio">${txt}<div class="bio-row">${favChip}${editable ? `<button class="btn btn-ghost btn-sm" data-action="bio-edit">✏️ Upravit bio</button>` : ""}</div></div>`;
}
async function loadMyBio() {
  const box = document.getElementById("myBio"); if (!box || !state.user) return;
  try {
    const p = await api("/profile/public?nick=" + encodeURIComponent(state.user.username));
    _myProfileBio = { bio: p.bio || "", fav_game: p.fav_game || "" };
    box.innerHTML = profileBioHTML(p, true);
  } catch (e) { box.innerHTML = ""; }
}
function editBio() {
  const box = document.getElementById("myBio"); if (!box) return;
  const opts = FAV_GAMES.map((g) => `<option value="${esc(g)}" ${g === _myProfileBio.fav_game ? "selected" : ""}>${g ? esc(g) : "— žádná —"}</option>`).join("");
  box.innerHTML = `<div class="profile-bio bio-edit">
    <textarea class="input" id="bioInput" maxlength="160" rows="2" placeholder="Napiš něco o sobě (max 160 znaků)…">${esc(_myProfileBio.bio)}</textarea>
    <div class="bio-row" style="margin-top:8px">
      <label style="font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px">🎮 Oblíbená hra <select class="input" id="bioFav" style="max-width:160px">${opts}</select></label>
      <button class="btn btn-primary btn-sm" data-action="bio-save">💾 Uložit</button>
      <button class="btn btn-ghost btn-sm" data-action="bio-cancel">Zrušit</button>
    </div>
  </div>`;
}
async function saveBio() {
  const ta = document.getElementById("bioInput"), fav = document.getElementById("bioFav");
  try {
    const r = await api("/profile/bio", { method: "POST", body: { bio: (ta && ta.value) || "", fav_game: (fav && fav.value) || "" } });
    _myProfileBio = { bio: r.bio || "", fav_game: r.fav_game || "" };
    toast("Bio uloženo ✓", "success");
    const box = document.getElementById("myBio"); if (box) box.innerHTML = profileBioHTML({ bio: r.bio, fav_game: r.fav_game }, true);
  } catch (e) { toast(e.message, "error"); }
}
function prestigeBadge(n) {
  n = n || 0;
  if (n <= 0) return "";
  const cls = n >= 6 ? "pr-legend" : n >= 3 ? "pr-gold" : "pr-bronze";
  return `<span class="prestige-badge ${cls}" title="Prestige ${n} – spálené sedláky 🔥">⭐${n}</span>`;
}
async function loadPrestige() {
  const box = document.getElementById("myPrestige"); if (!box || !state.user) return;
  try {
    const s = await api("/prestige");
    const star = prestigeBadge(s.prestige) || `<span class="faint">zatím žádný</span>`;
    const next = s.next_cost != null
      ? `<button class="btn btn-danger" data-action="prestige-buy" data-cost="${s.next_cost}" data-lvl="${s.prestige + 1}">🔥 Koupit Prestige ${s.prestige + 1} za ${fmtPts(s.next_cost)}</button>`
      : `<div class="ok" style="font-weight:800">👑 Máš maximální prestige!</div>`;
    box.innerHTML = `<div class="panel">
      <div class="section-title" style="margin-top:0">🔥 Prestige <span class="faint" style="font-size:12px;font-weight:400">— spal sedláky za permanentní status ⭐ (NEvratné)</span></div>
      <div class="row-between" style="flex-wrap:wrap;gap:12px;margin:4px 0 12px"><div>Tvůj prestige: <b style="font-size:18px">${star}</b></div><div class="faint" style="font-size:13px">Zůstatek: <b>${fmtPts(s.balance)}</b></div></div>
      <p class="muted" style="font-size:13px;line-height:1.55;margin:0 0 12px">Spálíš hromadu sedláků a získáš ⭐ u jména <b>navždy</b>. Body opravdu <b>zmizí z oběhu</b> (proti inflaci). Každý další level je dražší.</p>
      ${next}
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function prestigeBuy(cost, lvl) {
  if (!confirm(`Spálit ${fmtPts(cost)} sedláků za Prestige ${lvl}? 🔥\nJe to NEVRATNÉ — body zmizí z oběhu, dostaneš ⭐ navždy.`)) return;
  try {
    const r = await api("/prestige/buy", { method: "POST" });
    if (state.user) { state.user.points = r.balance; state.user.prestige = r.prestige; renderHeader(); }
    toast(`🔥 Prestige ${r.prestige}! Spáleno ${fmtPts(r.spent)} 🌾`, "success");
    try { confettiBurst(); } catch (e) {}
    loadPrestige();
  } catch (e) { toast(e.message, "error"); }
}
async function loadMyBadges() {
  const box = document.getElementById("myBadges"); if (!box || !state.user) return;
  try {
    const p = await api("/profile/public?nick=" + encodeURIComponent(state.user.username));
    box.innerHTML = badgesSectionHTML(p.badges);   // bez panel-wrapperu (je už uvnitř profil panelu)
  } catch (e) { box.innerHTML = ""; }
}
async function saveTradeUrl() {
  const inp = $("#tradeUrl"); if (!inp) return;
  const msg = $("#tradeMsg");
  try {
    const r = await api("/profile/trade-url", { method: "POST", body: { url: inp.value.trim() } });
    if (state.user) state.user.steam_trade_url = r.steam_trade_url;
    if (msg) { msg.textContent = r.steam_trade_url ? "✓ Uloženo" : "Smazáno"; msg.style.color = ""; }
    toast(r.steam_trade_url ? "Trade link uložen ✓" : "Trade link smazán", "success");
  } catch (e) {
    if (msg) { msg.textContent = "⚠ " + e.message; msg.style.color = "#e07a7a"; }
    toast(e.message, "error");
  }
}

/* ---------- Stránka „🎁 Bonusy" – daily streak + kolo štěstí ---------- */
function pageBonusy() {
  const view = $("#view");
  if (!state.user) {
    view.innerHTML = `
      <div class="page-head with-mascot"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div class="ph-text"><h1>🎁 Denní odměny</h1><p class="muted">Streak bonus a kolo štěstí – sedláci zdarma každý den.</p></div></div>
      <div class="panel" style="text-align:center;padding:36px 20px">
        <div style="font-size:42px;margin-bottom:8px">🎡</div>
        <div class="section-title" style="margin:0 0 6px">Připoj se a získávej denní odměny</div>
        <p class="muted" style="font-size:13.5px;max-width:430px;margin:0 auto 18px">Každý den si vyzvedni streak bonus a zatoč kolem štěstí o sedláky – až 🎰 <b style="color:#e8b923">JACKPOT 3000</b>!</p>
        <button class="btn btn-kick" data-action="connect">🟢 Připojit přes Kick</button>
      </div>`;
    return;
  }
  view.innerHTML = `
    <div class="page-head with-mascot"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div class="ph-text"><h1>🎁 Denní odměny</h1><p class="muted">Vyzvedni si streak a zatoč kolem štěstí – každý den nové sedláky! 🍀</p></div></div>
    <div id="chatGoal" style="margin-bottom:18px"></div>
    <div id="subGoal" style="margin-bottom:18px"></div>
    <div id="bpCard"></div>
    <div id="levelPassCard" style="margin-top:18px"></div>
    <div id="wheelCard" style="margin-top:18px"></div>
    <div id="partnersCard" style="margin-top:18px"></div>`;
  loadBattlePass();
  loadLevelPass();
  loadWheel();
  loadPartnerLinks();
  loadCommunityGoal();
  loadSubGoal();
  dropTimer = setInterval(() => { if (!document.hidden) { loadCommunityGoal(); loadSubGoal(); } }, 12000);
}

/* ---------- Zahrádka (farm-sim) ---------- */
let _gardenSel = null, _gardenTimer = null;
function grdDur(s) { s = Math.max(0, s | 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60; return h ? `${h}h ${m}m` : (m ? `${m}m ${ss}s` : `${ss}s`); }
function pageGarden() {
  const view = $("#view");
  if (!state.user) { view.innerHTML = `<div class="empty"><div class="big">🌱</div>Přihlas se přes Kick a začni pěstovat sedláky!</div>`; return; }
  view.innerHTML = `<div class="page-head with-mascot"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div class="ph-text"><h1>🌱 Zahrádka</h1><p class="muted">Vyber semínko → zasaď na záhon → počkej až doroste → sklidíš sedláky! 🌾</p></div></div>
    <div id="gardenPush">${gardenPushBtnHTML()}</div>
    <div id="gardenBox">${skeletonCards(1)}</div>
    <div id="gardenDecor" style="margin-top:8px"></div>
    <div id="gardenLb" style="margin-top:8px"></div>`;
  loadGarden();
  loadGardenDecor();
  loadGardenLeaderboard();
}
/* Web Push „chrobáci na mobil": tlačítko + subscribe flow. Service worker je /sw.js. */
function _pushSupported() { return ("serviceWorker" in navigator) && ("PushManager" in window) && ("Notification" in window); }
function gardenPushBtnHTML() {
  if (!_pushSupported()) {
    const iOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const standalone = window.matchMedia && window.matchMedia("(display-mode: standalone)").matches;
    if (iOS && !standalone)
      return `<div class="gpush-hint">📱 Chceš upozornění na chrobáky i na iPhone? Dej v Safari <b>Sdílet → Přidat na plochu</b> a otevři Zurys odtud.</div>`;
    return "";
  }
  const on = localStorage.getItem("push_on") === "1" && Notification.permission === "granted";
  return on
    ? `<div class="gpush on">🔔 Upozornění na chrobáky <b>zapnutá</b><button class="btn btn-ghost btn-sm" data-action="garden-push-off" style="margin-left:auto">Vypnout</button></div>`
    : `<button class="btn btn-ghost btn-sm gpush-btn" data-action="garden-push-on">🔔 Upozornit na chrobáky (i na mobil)</button>`;
}
function renderGardenPushBtn() { const el = document.getElementById("gardenPush"); if (el) el.innerHTML = gardenPushBtnHTML(); }
function _urlB64ToU8(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(s); const u8 = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
  return u8;
}
async function enableGardenPush() {
  if (!_pushSupported()) { toast("Tvůj prohlížeč push notifikace nepodporuje. 😕", "error"); return; }
  try {
    const reg = await navigator.serviceWorker.ready;
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { toast("Bez povolení notifikací to nepůjde — povol je v prohlížeči.", "info"); return; }
    const vp = await api("/push/vapid-public");
    if (!vp.enabled || !vp.key) { toast("Push zatím není nastavený na serveru.", "error"); return; }
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      // Re-subscribe pokud byl VAPID klíč rotován (mismatch = stará subscription = 401/403)
      const storedKey = btoa(String.fromCharCode(...new Uint8Array(sub.options.applicationServerKey)))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
      if (storedKey !== vp.key) { await sub.unsubscribe(); sub = null; }
    }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: _urlB64ToU8(vp.key) });
    await api("/push/subscribe", { method: "POST", body: sub.toJSON() });
    localStorage.setItem("push_on", "1");
    toast("🔔 Hotovo! Když ti chrobáci napadnou zahrádku, cinkne ti to i na mobil.", "success");
    renderGardenPushBtn();
  } catch (e) { toast("Push se nepodařilo zapnout: " + (e.message || e), "error"); }
}
async function disableGardenPush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) { try { await api("/push/unsubscribe", { method: "POST", body: sub.toJSON() }); } catch (e) {} await sub.unsubscribe(); }
    localStorage.removeItem("push_on");
    toast("Upozornění na chrobáky vypnutá.", "info");
    renderGardenPushBtn();
  } catch (e) { toast(e.message || String(e), "error"); }
}
async function loadGardenDecor() {
  const box = document.getElementById("gardenDecor"); if (!box) return;
  try {
    const d = await api("/garden/decor");
    const shelf = d.owned_icons.length
      ? `<div class="decor-shelf">${d.owned_icons.map((i) => `<span>${i}</span>`).join("")}</div>`
      : `<p class="muted" style="font-size:12.5px;margin:0 0 10px">Zatím žádné dekorace — kup si je dole a oživ zahrádku! 🌻</p>`;
    const pestInfo = `<p class="muted" style="font-size:12.5px;margin:0 0 10px">Chrobáci: <b>${d.pest_chance || 0}%</b> šance · dekorace snižují o <b>${d.pest_reduction || 0}%</b> · minimum <b>${d.pest_min_chance || 0}%</b>.</p>`;
    const shop = d.items.map((it) => {
      const btn = it.owned ? `<span class="decor-owned">✓ máš</span>`
        : `<button class="bp-claim" data-action="decor-buy" data-key="${it.key}">${fmtPts(it.cost)}</button>`;
      const perks = `${it.pest_reduction ? `<span class="faint" style="font-size:12px">-${it.pest_reduction}% chrobáci</span>` : ""}${it.plots ? `<span class="faint" style="font-size:12px;color:#7ed957">🌱 +${it.plots} záhon</span>` : ""}`;
      return `<div class="decor-card${it.owned ? " owned" : ""}"><div class="decor-ico">${it.icon}</div><b>${esc(it.name)}</b>${perks}${btn}</div>`;
    }).join("");
    box.innerHTML = `<div class="section-title" style="margin:26px 0 8px">🎨 Dekorace zahrádky</div>
      ${pestInfo}
      ${shelf}
      <div class="decor-shop">${shop}</div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function loadGardenLeaderboard() {
  const box = document.getElementById("gardenLb"); if (!box) return;
  try {
    const rows = await api("/garden/leaderboard");
    if (!rows.length) { box.innerHTML = ""; return; }
    const list = rows.map((r) => {
      const medal = r.rank === 1 ? "🥇" : r.rank === 2 ? "🥈" : r.rank === 3 ? "🥉" : `#${r.rank}`;
      const av = r.avatar_url ? `<img src="${r.avatar_url}" alt="" style="width:100%;height:100%;object-fit:cover">` : esc((r.username || "?").charAt(0).toUpperCase());
      return `<div style="display:flex;align-items:center;gap:10px;padding:7px 12px;border-bottom:1px solid rgba(255,255,255,.05)"><span style="width:30px;font-weight:900;text-align:center">${medal}</span><span style="width:28px;height:28px;border-radius:50%;overflow:hidden;display:grid;place-items:center;background:rgba(255,255,255,.08);font-size:12px;font-weight:800;flex:none">${av}</span><span style="flex:1;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.username)}</span><span style="font-weight:800;color:#46d369">${fmtPts(r.total)} 🌾</span></div>`;
    }).join("");
    box.innerHTML = `<div class="section-title" style="margin:26px 0 8px">🏆 Top zahradníci</div><div style="background:rgba(255,255,255,.02);border-radius:12px;overflow:hidden">${list}</div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function buyDecor(key) {
  try {
    const r = await api("/garden/decor/buy", { method: "POST", body: { key } });
    if (state.user) state.user.points = r.balance;
    toast(`Koupeno: ${r.icon} ${r.name}! 🎨`, "success");
    try { confettiBurst(); } catch (e) {}
    renderHeader(); loadGardenDecor();
  } catch (e) { toast(e.message, "error"); }
}
async function loadGarden() {
  const box = document.getElementById("gardenBox"); if (!box) return;
  try {
    const g = await api("/garden");
    const plots = g.plots.map((p) => {
      if (p.empty) return `<div class="grd-plot grd-empty" data-action="grd-plant" data-plot="${p.plot}"><div class="grd-crop">➕</div><div class="grd-lbl">Zasadit</div></div>`;
      if (p.eaten) {   // chrobáci sežrali úrodu – pozdě, jen půlka, nezachráníš
        const half = Math.round(p.reward * 0.5);
        const tail = p.ready
          ? `<button class="grd-harv-half" data-action="grd-harvest" data-plot="${p.plot}" title="Načaté chrobáky – jen půlka">Sklidit +${fmtPts(half)}</button>`
          : `<div class="grd-time" data-left="${p.seconds_left}">${grdDur(p.seconds_left)}</div>`;
        return `<div class="grd-plot grd-pestd"><div class="grd-crop">🐛💀</div><div class="grd-lbl">${esc(p.name)}</div><div class="faint" style="font-size:11px;color:#ef6b6b">sežráno → půlka</div>${tail}</div>`;
      }
      if (p.pest) {    // AKTIVNÍ chrobáci – zachraň než vyprší okno!
        const half = Math.round(p.reward * 0.5);
        const tail = p.ready
          ? `<button class="grd-harv-half" data-action="grd-harvest" data-plot="${p.plot}" title="Sklidit teď bez postřiku = jen půlka">Sklidit +${fmtPts(half)}</button>`
          : `<div class="grd-time" data-left="${p.rescue_left}" style="color:#ff7aa8">⏳ ${grdDur(p.rescue_left)}</div>`;
        return `<div class="grd-plot grd-pestd grd-pest-active"><div class="grd-crop">🐛</div><div class="grd-lbl">${esc(p.name)}</div><button class="grd-rescue-btn" data-action="grd-rescue" data-plot="${p.plot}" title="Zaplať a zachráníš plnou úrodu. Po vypršení času je sežerou (jen půlka, +${fmtPts(half)}).">🐛 Zachraň ${fmtPts(p.rescue_cost)}</button>${tail}</div>`;
      }
      if (p.ready) return `<div class="grd-plot grd-ready"><div class="grd-crop">${p.icon}</div><div class="grd-lbl">${esc(p.name)}</div><button class="bp-claim" data-action="grd-harvest" data-plot="${p.plot}">Sklidit +${fmtPts(p.reward)}</button></div>`;
      const pestRefresh = p.pest_in > 0 ? ` data-refresh-left="${p.pest_in}"` : "";
      return `<div class="grd-plot grd-grow"${pestRefresh}><div class="grd-crop grd-sprout">🌱</div><div class="grd-lbl">${esc(p.name)}</div><div class="grd-time" data-left="${p.seconds_left}">${grdDur(p.seconds_left)}</div></div>`;
    }).join("");
    const shop = g.crops.map((c) => `<button class="grd-seed${_gardenSel === c.key ? " sel" : ""}" data-action="grd-pick" data-crop="${c.key}"><span class="grd-si">${c.icon}</span><b>${esc(c.name)}</b><span class="faint">${c.hours} h · semínko ${fmtPts(c.cost)} · oček. <b>${c.expected_no_rescue >= 0 ? "+" : ""}${fmtPts(c.expected_no_rescue || 0)}</b> / aktivně <b>${c.expected_rescue >= 0 ? "+" : ""}${fmtPts(c.expected_rescue || 0)}</b> · 🎟️ <b style="color:var(--farm-green,#46d369)">+${fmtPts(c.xp || 0)} XP</b></span></button>`).join("");
    const seedNote = `<p class="muted" style="font-size:12px;margin:-4px 0 10px">Semínko stojí <b>${g.seed_pct}%</b> z výnosu${g.sub ? ` — máš <b style="color:var(--accent)">sub slevu (jen ${g.seed_pct_sub}%)</b> 💜` : ` · 💜 sub jen ${g.seed_pct_sub}%`}. Chrobáci: <b>${g.pest_chance || 0}%</b>, záchrana <b>${g.rescue_pct || 0}%</b> výnosu.<br>🎟️ <b>Sklizeň dává XP do levelu i Battle Passu</b> — <b>mimo denní strop</b>, počítá se vždy (i když máš chat vyfarmený)${g.sub ? ", sub ×1.5" : ""}. Chrobáci úrodu i XP půlí.</p>`;
    const readyPlots = g.plots.filter((p) => !p.empty && p.ready);
    const emptyCount = g.plots.filter((p) => p.empty).length;
    const readySum = readyPlots.reduce((s, p) => s + ((p.pest || p.eaten) ? Math.round(p.reward * 0.5) : p.reward), 0);
    const bulk = (readyPlots.length || emptyCount) ? `<div class="grd-bulk" style="display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 0">${readyPlots.length ? `<button class="bp-claim" data-action="grd-harvest-all">⚡ Sklidit vše (${readyPlots.length}× · +${fmtPts(readySum)})</button>` : ""}${emptyCount ? `<button class="grd-seed${_gardenSel ? " sel" : ""}" data-action="grd-plant-all" style="width:auto">🌱 Zasadit vše${_gardenSel ? "" : " (vyber semínko)"}</button>` : ""}</div>` : "";
    box.innerHTML = `<div class="grd-grid">${plots}</div>
      ${bulk}
      <div class="section-title" style="margin:24px 0 8px">🌰 Semínka ${_gardenSel ? `<span class="feeds-pass">vybráno → klikni prázdný záhon</span>` : ""}</div>
      ${seedNote}
      <div class="grd-shop">${shop}</div>`;
    if (_gardenTimer) clearInterval(_gardenTimer);
    _gardenTimer = setInterval(() => {
      let reload = false;
      document.querySelectorAll(".grd-time").forEach((el) => {
        const s = parseInt(el.dataset.left, 10) - 1;
        if (s <= 0) reload = true; else { el.dataset.left = s; el.textContent = grdDur(s); }
      });
      document.querySelectorAll("[data-refresh-left]").forEach((el) => {
        const s = parseInt(el.dataset.refreshLeft, 10) - 1;
        if (s <= 0) reload = true; else el.dataset.refreshLeft = s;
      });
      if (reload) {
        clearInterval(_gardenTimer);
        _gardenTimer = null;
        loadGarden();
      }
    }, 1000);
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function grdPick(crop) { _gardenSel = crop; loadGarden(); }
async function grdPlant(plot) {
  if (!_gardenSel) { toast("Vyber nejdřív semínko dole. 🌰", "error"); return; }
  try {
    const r = await api("/garden/plant", { method: "POST", body: { plot: parseInt(plot, 10), crop: _gardenSel } });
    if (state.user) state.user.points = r.balance;
    toast(`Zasazeno: ${r.name || "plodina"} −${fmtPts(r.cost || 0)} · doroste za ${r.hours || "?"} h.`, "success");
    renderHeader(); loadGarden();
  } catch (e) { toast(e.message, "error"); }
}
async function grdHarvest(plot) {
  try {
    const r = await api("/garden/harvest", { method: "POST", body: { plot: parseInt(plot, 10) } });
    if (state.user) state.user.points = r.balance;
    if (r.pest) { toast(`🐛 Chrobáci ti načali úrodu! Sklizeno jen +${fmtPts(r.reward)} (půlka) — příště je včas zachraň. 🌾`, "info"); }
    else if (r.golden) { toast(`🌟 ZLATÁ SKLIZEŇ! ${r.name} ×3 → +${fmtPts(r.reward)} ✨🌾`, "success"); try { confettiBurst(); } catch (e) {} }
    else { toast(`Sklizeno: ${r.name}! +${fmtPts(r.reward)} 🌾`, "success"); try { confettiBurst(); } catch (e) {} }
    renderHeader(); loadGarden();
  } catch (e) { toast(e.message, "error"); }
}
async function grdRescue(plot) {
  try {
    const r = await api("/garden/rescue", { method: "POST", body: { plot: parseInt(plot, 10) } });
    if (state.user) state.user.points = r.balance;
    toast(`🐛➡️🌱 Postřik! ${r.name} zachráněna (−${fmtPts(r.cost)}) — bude plná sklizeň.`, "success");
    renderHeader(); loadGarden();
  } catch (e) { toast(e.message, "error"); }
}
async function grdHarvestAll() {
  try {
    const r = await api("/garden/harvest-all", { method: "POST", body: {} });
    if (state.user) state.user.points = r.balance;
    const g = r.golden ? ` · 🌟 ${r.golden}× ZLATÁ!` : "";
    toast(`Sklizeno ${r.count}× · +${fmtPts(r.total)} 🌾${g}`, "success");
    try { confettiBurst(); } catch (e) {}
    renderHeader(); loadGarden();
  } catch (e) { toast(e.message, "error"); }
}
async function grdPlantAll() {
  if (!_gardenSel) { toast("Vyber nejdřív semínko dole. 🌰", "error"); return; }
  try {
    const r = await api("/garden/plant-all", { method: "POST", body: { crop: _gardenSel } });
    if (state.user) state.user.points = r.balance;
    toast(`Zasazeno ${r.planted}× ${r.name} −${fmtPts(r.spent)} · doroste za ${r.hours} h.`, "success");
    renderHeader(); loadGarden();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- Farmářský Battle Pass (sezónní dráha) ---------- */
async function loadBattlePass() {
  const box = document.getElementById("bpCard"); if (!box) return;
  try {
    const [bp, daily] = await Promise.all([api("/battlepass"), api("/daily/status").catch(() => null)]);
    let dailyHtml = "";
    if (daily) {
      const rew = (daily.reward_now || 0) * (daily.mult || 1);
      dailyHtml = daily.can_claim
        ? `<button class="btn btn-primary btn-block" data-action="bp-daily" style="margin:0 0 14px">🔥 Vyzvednout denní bonus: +${fmtPts(rew)} (den ${daily.day}/7)</button>`
        : `<div class="bp-daily-done">🔥 Denní bonus dnes vyzvednut (den ${daily.day}/7) · vrať se zítra</div>`;
    }
    const nodes = bp.tiers.map((t) => {
      const cls = t.claimed ? "bp-claimed" : (t.reached ? "bp-ready" : "bp-locked");
      const free = t.claimed ? "✓"
        : (t.reached ? `<button class="bp-claim" data-action="bp-claim" data-tier="${t.tier}">+${fmtPts(t.reward)}</button>`
          : `🔒 ${fmtPts(t.reward)}`);
      let prem;
      if (t.premium_claimed) prem = "✓";
      else if (!bp.is_premium) prem = "🔒";
      else if (t.reached) prem = `<button class="bp-claim bp-claim-prem" data-action="bp-claim-premium" data-tier="${t.tier}">+${fmtPts(t.premium_reward)}</button>`;
      else prem = `🔒 ${fmtPts(t.premium_reward)}`;
      return `<div class="bp-node ${cls}${t.milestone ? " bp-milestone" : ""}" title="Tier ${t.tier}">
        <div class="bp-tier">${t.milestone ? "⭐ " : ""}${t.tier}</div>
        <div class="bp-rew">${free}</div>
        <div class="bp-rew bp-rew-prem" title="💜 Prémium (jen sub): +${fmtPts(t.premium_reward)}">${prem}</div>
      </div>`;
    }).join("");
    const premBanner = bp.is_premium
      ? `<div class="bp-prem bp-prem-on">💜 Prémiová řada aktivní — bereš <b>3× odměny</b>!${bp.claimable_premium ? ` <b style="color:var(--farm-green)">${bp.claimable_premium} k vyzvednutí</b>` : ""}</div>`
      : `<div class="bp-prem bp-prem-off">💜 <b>Subni na Kicku</b> a odemkni <b>prémiovou řadu</b> — 3× větší odměny za každý tier (spodní řada 🔒).</div>`;
    box.innerHTML = `<div class="panel" style="overflow:hidden">
      <div class="row-between" style="flex-wrap:wrap;gap:8px;margin-bottom:6px">
        <div class="section-title" style="margin:0">🎟️ Farmářský Battle Pass</div>
        <span class="faint" style="font-size:12.5px">Sezóna ${esc(bp.season)} · Tier <b style="color:var(--accent)">${bp.tier}</b>/${bp.max_tier}${bp.claimable ? ` · <b style="color:var(--farm-green)">${bp.claimable} k vyzvednutí!</b>` : ""}</span>
      </div>
      <p class="muted" style="font-size:12.5px;margin:0 0 10px">Vše co nafarmíš — <b>sledování, chat, denní bonus, kolo, úkoly</b> — tě posouvá v passu. Reset každý měsíc. <span class="faint">(${fmtPts(bp.into)} / ${fmtPts(bp.tier_xp)} do dalšího tieru)</span></p>
      ${premBanner}
      ${dailyHtml}
      <div class="bp-prog"><i style="width:${bp.pct}%"></i></div>
      <div class="bp-track">${nodes}</div>
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function claimBpTier(tier, premium) {
  try {
    const r = await api("/battlepass/claim", { method: "POST", body: { tier: parseInt(tier, 10), premium: !!premium } });
    if (state.user) state.user.points = r.balance;
    toast(`${premium ? "💜 PRÉMIUM tier" : "🎟️ Tier"} ${r.tier} vyzvednut! +${fmtPts(r.reward)} 🌾`, "success");
    try { confettiBurst(); } catch (e) {}
    renderHeader(); loadBattlePass();
  } catch (e) { toast(e.message, "error"); }
}

async function loadLevelPass() {
  const box = document.getElementById("levelPassCard"); if (!box) return;
  try {
    const lp = await api("/level-pass");
    const nodes = lp.milestones.map((m) => {
      const cls = m.claimed ? "lp-claimed" : (m.reached ? "lp-ready" : "lp-locked");
      const rew = m.rewards[0] || {};
      const preview = rew.type === "name"
        ? `<div class="lp-prev lp-prev-name"><span class="${rew.cls || ""}">Abc</span></div>`
        : `<div class="avatar lp-prev ${rew.cls || ""}"></div>`;
      const action = m.claimed ? `<span class="lp-done">✓ Máš</span>`
        : (m.reached ? `<button class="bp-claim${m.irl ? " bp-claim-prem" : ""}" data-action="lp-claim" data-level="${m.level}">Vyzvednout 🎁</button>`
          : `<span class="lp-lock">🔒 Lvl ${m.level}</span>`);
      return `<div class="lp-node ${cls}">
        ${preview}
        <div class="lp-info"><b>${m.icon} ${esc(m.label)}</b><span class="faint">Úroveň ${m.level}${m.irl ? " · 🔪 reálná cena!" : ""}</span></div>
        ${action}
      </div>`;
    }).join("");
    box.innerHTML = `<div class="panel">
      <div class="row-between" style="flex-wrap:wrap;gap:8px;margin-bottom:6px">
        <div class="section-title" style="margin:0">🏅 Level Pass</div>
        <span class="faint" style="font-size:12.5px">Tvá úroveň <b style="color:var(--accent)">${lp.level}</b>${lp.claimable ? ` · <b style="color:var(--farm-green)">${lp.claimable} k vyzvednutí!</b>` : ""}</span>
      </div>
      <p class="muted" style="font-size:12.5px;margin:0 0 12px">Exkluzivní rámečky, co <b>nejdou koupit</b> — jen za dosaženou úroveň. Úroveň roste hlavně <b>farmením</b> (placené/gift suby dají jen <b>50 % XP</b> — náskok, ne koupený level), takže ji nikdo rychle nepřeskočí. Vrchol = <b>úroveň 100</b> 👑 → trofej + <b>reálná cena</b>! 🔪</p>
      <div class="lp-track">${nodes}</div>
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function claimLevelPass(level) {
  try {
    const r = await api("/level-pass/claim", { method: "POST", body: { level: parseInt(level, 10) } });
    const names = (r.reward_names || []).join(" + ");
    toast(`🏅 Milník ${r.level} (${r.label}) vyzvednut! Získal jsi: ${names} 🎁`, "success");
    if (r.irl) toast("🔪 ÚROVEŇ 100! Streamer dostal echo na Discord — domluví se s tebou na předání reálné ceny! 👑", "success");
    try { confettiBurst(); } catch (e) {}
    loadLevelPass();
  } catch (e) { toast(e.message, "error"); }
}

async function claimBpDaily() {     // denní bonus folded do Battle Passu (reuse /daily/claim)
  try {
    const r = await api("/daily/claim", { method: "POST" });
    if (state.user) state.user.points = r.balance;
    toast(r.message, "success");
    try { confettiBurst(); } catch (e) {}
    renderHeader(); loadBattlePass();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- Kolo štěstí (denní spin) ---------- */
let wheelState = null, wheelBusy = false, bonusReady = false, questReady = false;
async function loadWheel() {
  const box = document.getElementById("wheelCard"); if (!box) return;
  wheelBusy = false;
  try {
    const s = await api("/wheel/status");
    wheelState = s;
    const segs = s.segments || [];
    const n = segs.length || 1;
    // barevné výseče (conic-gradient) – jackpot zlatě, jinak střídavě fialová
    const stops = segs.map((amt, i) => {
      const c = (amt === s.jackpot) ? "#e8b923" : (i % 2 ? "#281f4d" : "#3a2d6e");
      return `${c} ${(i * 360 / n).toFixed(2)}deg ${((i + 1) * 360 / n).toFixed(2)}deg`;
    }).join(",");
    // popisky částek po obvodu (každý natočený do středu své výseče, text srovnaný nahoru)
    const labels = segs.map((amt, i) => {
      const mid = (i + 0.5) * 360 / n;
      const isJp = amt === s.jackpot;
      return `<div class="wheel-lbl${isJp ? " jp" : ""}" style="transform:rotate(${mid}deg)">`
           + `<span style="display:inline-block;transform:rotate(${(-mid).toFixed(2)}deg)">${amt}</span></div>`;
    }).join("");
    const fmtWait = (sec) => sec >= 3600 ? Math.ceil(sec / 3600) + " h" : Math.max(1, Math.ceil(sec / 60)) + " min";
    const btn = s.can_spin
      ? `<button class="btn btn-primary btn-block" data-action="spin-wheel" id="wheelBtn">🎡 Zatočit kolem</button>`
      : `<button class="btn btn-block" disabled>Další zatočení za ${fmtWait(s.next_in_seconds)}</button>`;
    box.innerHTML = `<div class="panel wheel-panel">
      <div class="row-between" style="margin-bottom:10px">
        <div><div class="section-title" style="margin:0">🎡 Kolo štěstí</div>
          <div class="muted" style="font-size:12.5px;margin-top:5px">Zatoč si <b style="color:#e8b923">1× denně</b> o sedláky — jackpot <b style="color:#e8b923">${s.jackpot}</b>! 🍀</div></div>
        <span class="wheel-badge">1× / den</span>
      </div>
      <div class="wheel-wrap">
        <div class="wheel-pointer"></div>
        <div class="wheel" id="wheelSpin" style="background:conic-gradient(${stops})">
          ${labels}
          <div class="wheel-hub">🌾</div>
        </div>
      </div>
      ${btn}
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function doSpinWheel() {
  if (wheelBusy) return;
  const wheelEl = document.getElementById("wheelSpin");
  const btn = document.getElementById("wheelBtn");
  if (!wheelEl) return;
  wheelBusy = true;
  if (btn) { btn.disabled = true; btn.textContent = "Točím… 🎡"; }
  try {
    const r = await api("/wheel/spin", { method: "POST" });
    const n = (wheelState && wheelState.segments) ? wheelState.segments.length : 8;
    const seg = 360 / n;
    const mid = (r.index + 0.5) * seg;
    const jitter = (Math.random() - 0.5) * seg * 0.6;     // ±0,3 výseče (ať netrefí pokaždé střed)
    const R = 360 * 6 - mid - jitter;                      // 6 otáček + dotoč výhru pod ukazatel (nahoru)
    wheelEl.style.transition = "none";                     // reset do 0 (kdyby se točilo bez překreslení)
    wheelEl.style.transform = "rotate(0deg)";
    void wheelEl.offsetWidth;                              // vynucený reflow
    wheelEl.style.transition = "transform 4.6s cubic-bezier(0.16,1,0.3,1)";
    wheelEl.style.transform = "rotate(" + R + "deg)";
    setTimeout(() => {
      if (state.user) state.user.points = r.balance;
      renderHeader();
      toast(r.message, "success");
      try { confettiBurst(); } catch (e) {}
      if (r.jackpot) wheelEl.classList.add("jackpot-win");
      if (btn) btn.outerHTML = `<button class="btn btn-block" disabled>Další zatočení za ~${(wheelState && wheelState.cooldown_h) || 20} h</button>`;
      refreshBonusDot();
      wheelBusy = false;
    }, 4750);
  } catch (e) {
    toast(e.message, "error");
    if (btn) { btn.disabled = false; btn.textContent = "🎡 Zatočit kolem"; }
    wheelBusy = false;
  }
}

/* ---------- Denní / týdenní úkoly (questy) – plní i Battle Pass ---------- */
function pageUkoly() {
  const view = $("#view");
  if (!state.user) {
    view.innerHTML = `
      <div class="page-head with-mascot"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div class="ph-text"><h1>📋 Úkoly</h1><p class="muted">Denní a týdenní úkoly – plň a ber sedláky navíc.</p></div></div>
      <div class="panel" style="text-align:center;padding:36px 20px">
        <div style="font-size:42px;margin-bottom:8px">📋</div>
        <div class="section-title" style="margin:0 0 6px">Připoj se a plň úkoly</div>
        <p class="muted" style="font-size:13.5px;max-width:430px;margin:0 auto 18px">Denní a týdenní úkoly ti dají sedláky navíc — a posouvají tě v <b>Battle Passu</b> 🎟️.</p>
        <button class="btn btn-kick" data-action="connect">🟢 Připojit přes Kick</button>
      </div>`;
    return;
  }
  view.innerHTML = `
    <div class="page-head with-mascot"><img class="page-mascot" src="/sedlak-cut.png" alt=""><div class="ph-text"><h1>📋 Úkoly</h1><p class="muted">Plň denní a týdenní úkoly, ber sedláky navíc – a posouvej se v Battle Passu 🎟️.</p></div></div>
    <div id="questsCard"></div>`;
  loadQuests();
}
async function loadQuests() {
  const box = document.getElementById("questsCard"); if (!box) return;
  try {
    const qs = await api("/quests");
    if (!Array.isArray(qs) || !qs.length) { box.innerHTML = ""; return; }   // úkoly mimo provoz → kartu schovej
    const sec = (period, title, icon) => {
      const list = qs.filter((q) => q.period === period);
      if (!list.length) return "";
      return `<div class="section-title" style="margin-top:16px">${icon} ${title}</div><div class="quest-list">${list.map(questRowHTML).join("")}</div>`;
    };
    box.innerHTML = `<div class="panel">
      <div class="section-title" style="margin-top:0">📋 Úkoly <span class="faint" style="font-weight:400;font-size:13px">– plň a ber sedláky navíc</span> <span style="font-size:12px;font-weight:700;color:var(--accent)">→ 🎟️ plní Pass</span></div>
      ${sec("daily", "Denní úkoly", "☀️")}${sec("weekly", "Týdenní úkoly", "📅")}
    </div>`;
  } catch (e) { box.innerHTML = ""; }
}
function questRowHTML(q) {
  const pct = Math.min(100, Math.round(q.progress / q.target * 100));
  const btn = q.claimed
    ? `<button class="btn btn-sm" disabled>✓ Hotovo</button>`
    : (q.completed
      ? `<button class="btn btn-accent btn-sm" data-action="claim-quest" data-key="${q.key}">Vyzvednout +${fmtPts(q.reward)}</button>`
      : `<span class="quest-prog-txt faint">${q.progress}/${q.target}</span>`);
  return `<div class="quest${q.claimed ? " done" : q.completed ? " ready" : ""}">
    <div class="quest-main">
      <div class="quest-name">${esc(q.name)} <span class="faint" style="font-weight:400">+${fmtPts(q.reward)} 🌾</span></div>
      <div class="quest-desc faint">${esc(q.desc)}</div>
      <div class="quest-bar"><span style="width:${pct}%"></span></div>
    </div>
    <div class="quest-cta">${btn}</div>
  </div>`;
}
async function claimQuest(key) {
  try {
    const r = await api("/quests/claim", { method: "POST", body: { key } });
    if (state.user) state.user.points = r.balance;
    toast(r.message || "Odměna vyzvednuta! 🌾", "success");
    try { confettiBurst(); } catch (e) {}
    renderHeader(); loadQuests(); refreshBonusDot();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- Partnerské/sponzorské odkazy (1× navždy / ⚡ flash okna) ---------- */
let _partnerTimer = null;
async function loadPartnerLinks() {
  const box = document.getElementById("partnersCard"); if (!box) return;
  if (_partnerTimer) { clearInterval(_partnerTimer); _partnerTimer = null; }
  try {
    const data = await api("/partner-links");
    const links = (data && data.links) || [];
    if (!links.length) { box.innerHTML = ""; return; }
    const flashActive = !!(data && data.flash_active);
    const endsAt = (data && data.flash_ends_at) ? new Date(data.flash_ends_at).getTime() : 0;
    const openLink = (l) => `<a class="btn btn-sm" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer nofollow">Otevřít ↗</a>`;
    const takeBtn = (l, txt) => `<button class="btn btn-accent btn-sm" data-action="claim-partner" data-id="${l.id}" data-url="${esc(l.url)}">${txt}</button>`;
    const row = (l) => {
      let cta, sub;
      if (l.mode === "flash") {
        if (l.claimable) { cta = takeBtn(l, `⚡ Vzít +${fmtPts(l.reward)} ↗`); sub = "⚡ FLASH běží — klikni rychle, než okno zmizí!"; }
        else if (flashActive && l.claimed) { cta = openLink(l); sub = "✓ Z tohoto flash kola už máš. Počkej na další ⚡"; }
        else { cta = openLink(l); sub = "⚡ Flash bonus — sleduj chat a klikni, až naběhne!"; }
      } else {
        if (l.claimable) { cta = takeBtn(l, `Otevřít a vzít +${fmtPts(l.reward)} ↗`); sub = "Klikni, podívej se na našeho partnera a vezmi si sedláky 🤝"; }
        else { cta = openLink(l); sub = "✓ Odměnu už máš — odkaz můžeš otevřít znovu."; }
      }
      const cls = l.claimable ? " ready" : (l.claimed ? " done" : "");
      return `<div class="quest${cls}">
        <div class="quest-main">
          <div class="quest-name">${esc(l.icon || "🤝")} ${esc(l.label)} <span class="faint" style="font-weight:400">+${fmtPts(l.reward)} 🌾${l.mode === "flash" ? " ⚡" : ""}</span></div>
          <div class="quest-desc faint">${sub}</div>
        </div>
        <div class="quest-cta">${cta}</div>
      </div>`;
    };
    const banner = flashActive
      ? `<div style="background:rgba(232,185,35,.14);border:1px solid #e8b923;border-radius:10px;padding:8px 12px;margin-bottom:10px;font-weight:700;color:#e8b923">⚡ FLASH BONUS běží! <span id="flashCountdown" style="font-weight:500;color:var(--text)"></span></div>`
      : "";
    box.innerHTML = `<div class="panel">
      <div class="section-title" style="margin-top:0">🤝 Partneři <span class="faint" style="font-weight:400;font-size:13px">– podívej se na naše sponzory a získej sedláky</span></div>
      ${banner}
      <div class="quest-list">${links.map(row).join("")}</div>
    </div>`;
    if (flashActive && endsAt) {
      const tick = () => {
        const left = endsAt - Date.now();
        if (left <= 0) { if (_partnerTimer) { clearInterval(_partnerTimer); _partnerTimer = null; } loadPartnerLinks(); refreshBonusDot(); return; }
        const el = document.getElementById("flashCountdown");
        if (el) el.textContent = "zbývá " + Math.floor(left / 60000) + ":" + String(Math.floor((left % 60000) / 1000)).padStart(2, "0");
      };
      tick(); _partnerTimer = setInterval(tick, 1000);
    }
  } catch (e) { box.innerHTML = ""; }
}
async function claimPartnerLink(id, url) {
  if (url) { try { window.open(url, "_blank", "noopener"); } catch (e) {} }   // otevři sponzora (user gesture)
  try {
    const r = await api("/partner-links/" + id + "/claim", { method: "POST" });
    if (state.user) state.user.points = r.balance;
    toast(r.message || "Odměna vyzvednuta! 🌾", "success");
    renderHeader(); loadPartnerLinks(); refreshBonusDot();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- Tečka „máš co vyzvednout" na záložce Bonusy (daily / spin / quest) ---------- */
async function refreshBonusDot() {
  if (!state.user) { bonusReady = false; questReady = false; return; }
  try {
    const [d, w, pl, qs] = await Promise.all([api("/daily/status"), api("/wheel/status"), api("/partner-links").catch(() => ({ links: [] })), api("/quests").catch(() => [])]);
    const partnerReady = pl && Array.isArray(pl.links) && pl.links.some((l) => l.claimable);
    bonusReady = !!(d.can_claim || w.can_spin || partnerReady);          // tečka na Bonusy
    questReady = Array.isArray(qs) && qs.some((q) => q.completed && !q.claimed);   // tečka na Úkoly
  } catch (e) { return; }
  renderHeader();
}
async function loadProfTab(tab) {
  document.querySelectorAll('[data-action="prof-tab"]').forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  const box = $("#profContent"); if (!box) return;
  box.innerHTML = skeletonCards(1);
  try {
    if (tab === "orders") {
      const rows = await api("/profile/orders");
      if (!rows.length) { box.innerHTML = `<div class="empty"><div class="big">📦</div>Zatím žádné objednávky.</div>`; return; }
      box.innerHTML = `<div class="table-wrap"><table class="tbl"><thead><tr><th>Odměna</th><th>Body</th><th>Stav</th><th>Kdy</th></tr></thead><tbody>${rows.map((o) => `
        <tr><td>${o.product_type === "raffle" ? "🎟️ " : ""}${esc(o.product_name)}</td><td>${fmtPts(o.points_spent)}</td>
        <td>${o.status === "fulfilled" ? `<span class="tag-done">✓ Vyřízeno</span>` : `<span class="tag-pending">⏳ Čeká na vyřízení</span>`}</td>
        <td class="faint">${timeAgo(o.created_at)}</td></tr>`).join("")}</tbody></table></div>`;
    } else {
      const rows = await api("/profile/points-log");
      if (!rows.length) { box.innerHTML = `<div class="empty"><div class="big">📊</div>Žádná historie.</div>`; return; }
      box.innerHTML = `<div class="table-wrap"><table class="tbl"><thead><tr><th>Změna</th><th>Důvod</th><th>Kdy</th></tr></thead><tbody>${rows.map((l) => `
        <tr><td class="${l.change >= 0 ? "pos" : "neg"}">${l.change >= 0 ? "+" : ""}${fmtPts(l.change)}</td><td>${esc(l.reason || "")} ${l.category ? `<span class="code-pill">${esc(l.category.label)}</span>` : ""}</td><td class="faint">${timeAgo(l.created_at)}</td></tr>`).join("")}</tbody></table></div>`;
    }
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ---------- AUTH ---------- */
function pageConnect() {
  if (state.user) { navigate("shop"); return; }
  const view = $("#view");
  view.innerHTML = `
    <div class="auth-wrap">
      <div class="card auth-card" style="text-align:center">
        <div style="font-size:52px;line-height:1">🟢</div>
        <h2 style="margin-top:8px">Připoj svůj Kick účet</h2>
        <p class="muted" style="margin:10px 0 20px">Žádná registrace ani heslo – přihlásíš se přes Kick a hned můžeš sbírat body.</p>
        <button class="btn btn-kick btn-block" data-action="connect">🟢 Připojit přes Kick</button>
        <div class="demo-box" style="text-align:left">ℹ️ Teď běží <b>demo režim</b> – stačí zadat svůj Kick nick. Pro reálné Kick OAuth přihlášení dodej API klíče (viz README → <code>kick.json</code>). Admin demo: připoj se nickem <code>admin</code>.</div>
      </div>
    </div>`;
}

async function ensureKickMode() {
  if (state.kickMode) return state.kickMode;
  try { const s = await api("/auth/kick/status"); state.kickMode = s.mode; state.demoAdmin = s.demo_admin; }
  catch (e) { state.kickMode = "demo"; }
  return state.kickMode;
}

async function openConnect() {
  const mode = await ensureKickMode();
  if (mode === "oauth") { window.location.href = "/api/auth/kick/login"; return; }
  openModal(`<div class="modal-body" style="text-align:center">
      <div style="font-size:46px">🟢</div>
      <h2>Připojit přes Kick</h2>
      <p class="muted" style="margin:8px 0 2px">Zadej svůj <b>Kick uživatelský název</b> a propoj se.</p>
      <form class="form" data-submit="kick-connect" style="margin-top:16px;text-align:left">
        <div class="field"><label>Kick nick</label><input class="input" id="kick_username" placeholder="např. tvuj_kick_nick" autocomplete="off"></div>
        <button class="btn btn-kick btn-block" type="submit">🟢 Připojit účet</button>
      </form>
      <div class="demo-box" style="text-align:left">ℹ️ Demo režim. Admin: nick <code>${esc(state.demoAdmin || "admin")}</code>. Reálné Kick OAuth zapneš přes <code>kick.json</code> (README).</div>
    </div>`);
}

async function doKickConnect() {
  const u = $("#kick_username").value.trim().replace(/^@/, "");
  if (u.length < 2) { toast("Zadej svůj Kick nick.", "error"); return; }
  try {
    const r = await api("/auth/kick/connect", { method: "POST", body: { username: u } });
    state.user = r.user;
    toast(`Připojeno jako ${r.user.username} 🟢`, "success");
    closeModal(); navigate("shop"); refreshBonusDot();
  } catch (e) { toast(e.message, "error"); }
}
async function doLogout() {
  // Odregistrovat push subscription před odhlášením (sdílené zařízení nesmí dál dostávat notifikace)
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      try { await api("/push/unsubscribe", { method: "POST", body: sub.toJSON() }); } catch (_) {}
      await sub.unsubscribe();
      localStorage.removeItem("push_on");
    }
  } catch (_) {}
  try { await api("/auth/logout", { method: "POST" }); } catch (e) {}
  state.user = null; toast("Odhlášeno.", "info"); navigate("shop");
}

/* ============================================================
   ADMIN PANEL
============================================================ */
async function pageAdmin() {
  if (!state.user) { navigate("connect"); return; }
  const view = $("#view");
  if (!isStaff(state.user)) {
    view.innerHTML = `<div class="empty"><div class="big">🔒</div>Tato sekce je jen pro tým (admin / broadcaster / moderátor).</div>`; return;
  }
  const tabs = [
    ["overview", "📊 Přehled"], ["products", "🎁 Odměny"], ["users", "👥 Uživatelé"], ["subs", "💜 Suby"], ["orders", "📦 Objednávky"],
    ["raffles", "🎟️ Tomboly"], ["predictions", "🎯 Predikce"], ["codes", "🎫 Kódy"], ["drops", "🎁 Dropy"], ["games", "🎮 Hry"],
    ["bot", "🤖 Kick bot"], ["economy", "⚙️ Ekonomika"], ["news", "📣 Novinky"], ["security", "🛡️ Bezpečnost"],
    ["modnabor", "🛡️ Nábor modů"], ["gifts", "💝 Dary"],
  ].filter(([k]) => canSection(state.user, k));
  if (!tabs.some(([k]) => k === adminState.tab)) adminState.tab = tabs.length ? tabs[0][0] : null;
  const lbl = state.user.role === "admin" ? "Admin panel"
    : "Panel — " + (state.user.role === "broadcaster" ? "Broadcaster" : "Moderátor");
  const maintBanner = state.user.role === "admin" ? `<div id="maintBanner" class="maint-banner"></div>` : "";
  view.innerHTML = `
    <div class="page-head"><h1>🛠️ ${lbl}</h1><p class="muted">Vidíš jen sekce, na které máš oprávnění.</p></div>
    ${maintBanner}
    <div class="stat-grid" id="adminStats"></div>
    <div class="admin-tabs">
      ${tabs.map(([k, l]) => `<button data-action="admin-tab" data-tab="${k}">${l}</button>`).join("")}
    </div>
    <div id="adminContent"></div>`;
  loadAdminStats();
  if (state.user.role === "admin") loadMaintBanner();
  if (adminState.tab) renderAdminTab(adminState.tab);
}
async function loadAdminStats() {
  try {
    const s = await api("/admin/stats");
    $("#adminStats").innerHTML = `
      ${statBox(s.users, "Uživatelů")}
      ${statBox(s.active_products + "/" + s.products, "Aktivních odměn")}
      ${statBox(s.orders, "Objednávek")}
      ${statBox(s.pending_orders, "Čeká na vyřízení", "warn")}
      ${statBox(s.codes, "Redeem kódů")}
      ${statBox(Number(s.points_total).toLocaleString("cs-CZ"), "Bodů v oběhu", "accent")}`;
  } catch (e) {}
}
function statBox(v, k, cls = "") { return `<div class="stat"><div class="v ${cls}">${v}</div><div class="k">${k}</div></div>`; }
function riskBadge(risk) {
  const r = risk || { score: 0, level: "ok", reasons: [] };
  const cls = r.level === "danger" ? "risk-danger" : r.level === "warn" ? "risk-warn" : "risk-ok";
  const txt = r.level === "danger" ? "RISK" : r.level === "warn" ? "POZOR" : "OK";
  const title = (r.reasons || []).join(", ") || "bez signálu";
  return `<span class="risk-badge ${cls}" title="${esc(title)}">${txt} ${r.score || 0}</span>`;
}
function userMini(u) {
  if (!u) return "";
  return `<div class="lb-row compact">
    ${avatarHTML(u.username, u.avatar_url)}
    <div style="min-width:0;flex:1"><b>${esc(u.username)}</b> ${roleBadge(u.role || "user")}<div class="faint" style="font-size:12px">${(u.risk && u.risk.reasons || []).map(esc).join(", ") || esc(u.note || "") || "bez detailu"}</div></div>
    ${riskBadge(u.risk)}
  </div>`;
}
function checklistHTML(items) {
  return `<div class="check-grid">${(items || []).map((it) => `<div class="check-card ${it.ok ? "ok" : "bad"}">
    <span>${it.ok ? "✓" : "!"}</span><div><b>${esc(it.label)}</b><small>${esc(it.detail || "")}</small></div>
  </div>`).join("")}</div>`;
}
function economyDashboardHTML(e) {
  if (!e) return "";
  const row = (u, val, lbl) => `<div class="mini-row"><b>${esc(u.username)}</b><span>${Number(val || 0).toLocaleString("cs-CZ")} ${lbl}</span></div>`;
  return `<div class="panel" style="margin-bottom:16px">
    <div class="section-title" style="margin-top:0">Ekonomika – přehled</div>
    <div class="stat-grid">
      ${statBox(Number(e.points_total || 0).toLocaleString("cs-CZ"), "Sedláci v oběhu", "accent")}
      ${statBox("+" + Number(e.day.minted || 0).toLocaleString("cs-CZ"), "Vytvořeno 24 h", "accent")}
      ${statBox("-" + Number(e.day.burned || 0).toLocaleString("cs-CZ"), "Spáleno 24 h", "warn")}
      ${statBox(Number(e.day.net || 0).toLocaleString("cs-CZ"), "Net 24 h", e.day.net > 0 ? "warn" : "accent")}
    </div>
    <div class="dash-columns">
      <div><b>Top zisk 24 h</b>${(e.top_earners || []).length ? e.top_earners.map((u) => row(u, u.gained, "zisk")).join("") : `<div class="empty">Žádný zisk.</div>`}</div>
      <div><b>Top zůstatky 🐋</b>${(e.top_holders || []).map((u) => row(u, u.points, "pts")).join("")}</div>
      <div><b>Top utráceči 💸</b>${(e.top_spenders || []).length ? e.top_spenders.map((u) => row(u, u.spent, "utr")).join("") : `<div class="empty">Zatím nikdo.</div>`}</div>
    </div>
  </div>`;
}
function economyHealthHTML(h) {
  if (!h) return "";
  const nf = (n) => Number(n || 0).toLocaleString("cs-CZ");
  const series = h.series || [];
  const maxv = Math.max(1, ...series.map((s) => Math.max(s.minted, s.burned)));
  const dayLbl = (iso) => iso.slice(8, 10).replace(/^0/, "") + "." + iso.slice(5, 7).replace(/^0/, "") + ".";
  const cols = series.map((s) => {
    const mh = Math.max(s.minted ? 2 : 0, Math.round(s.minted / maxv * 42));
    const bh = Math.max(s.burned ? 2 : 0, Math.round(s.burned / maxv * 42));
    const tip = `${dayLbl(s.date)} — vytvořeno +${nf(s.minted)} · spáleno −${nf(s.burned)} · aktivních diváků: ${s.dau}`;
    return `<div style="flex:1;min-width:15px;display:flex;flex-direction:column;align-items:center" title="${tip}">
      <div style="height:44px;width:100%;display:flex;align-items:flex-end;justify-content:center"><div style="width:62%;height:${mh}px;background:#e0a857;border-radius:3px 3px 0 0"></div></div>
      <div style="height:44px;width:100%;display:flex;align-items:flex-start;justify-content:center;border-top:1px solid #ffffff2e"><div style="width:62%;height:${bh}px;background:#46d369;border-radius:0 0 3px 3px"></div></div>
      <div style="font-size:9.5px;color:#7a8699;margin-top:3px;white-space:nowrap">${dayLbl(s.date)}</div>
    </div>`;
  }).join("");
  const chart = series.length
    ? `<div style="overflow-x:auto"><div style="display:flex;gap:3px;align-items:stretch;min-width:${Math.min(900, series.length * 18)}px">${cols}</div></div>`
    : `<div class="empty">Zatím žádná data.</div>`;
  const cats = h.by_category || [];
  const inflow = cats.filter((c) => c.net > 0).sort((a, b) => b.net - a.net);
  const outflow = cats.filter((c) => c.net < 0).sort((a, b) => a.net - b.net);
  const inSum = inflow.reduce((a, c) => a + c.net, 0) || 1;
  const outSum = outflow.reduce((a, c) => a + Math.abs(c.net), 0) || 1;
  const catRow = (c, total, col) => {
    const val = Math.abs(c.net);
    const pct = Math.round(val * 100 / total);
    const transfer = c.kind === "transfer" ? " ♻️" : "";
    return `<div style="margin:7px 0" title="vytvořeno +${nf(c.minted)} · spáleno −${nf(c.burned)}">
      <div style="display:flex;justify-content:space-between;gap:8px;font-size:12.5px;margin-bottom:3px">
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${c.emoji} ${esc(c.label)}${transfer}</span>
        <span style="color:${col};font-weight:700;white-space:nowrap">${nf(val)} <small style="color:#7a8699;font-weight:400">${pct < 1 ? "&lt;1" : pct} %</small></span>
      </div>
      <div style="background:#ffffff12;border-radius:5px;height:10px;overflow:hidden"><div style="height:100%;width:${Math.max(2, pct)}%;background:${col};border-radius:5px"></div></div>
    </div>`;
  };
  const burnedRatio = h.faucet_total ? Math.round(h.sink_total * 100 / h.faucet_total) : 0;
  const young = series.length < h.days;
  const inflNote = young
    ? "Web zatím běží kratší dobu než okno, takže inflace vychází ~100 % — číslo se usadí, až bude historie delší než " + h.days + " dní."
    : "Inflace za okno: <b>" + (h.inflation_pct > 0 ? "+" : "") + h.inflation_pct + " %</b> oběhu.";
  return `<div class="panel" style="margin-bottom:16px">
    <div class="section-title" style="margin-top:0">📊 Zdraví ekonomiky <span class="faint" style="font-weight:400;font-size:12.5px">— posledních ${h.days} dní</span></div>
    <p class="muted" style="font-size:12.5px;margin:0 0 12px">Hlídá rovnováhu měny: 🟠 <b>vytvořeno</b> = nové sedláky diváci dostali (odměny za sledování, chat, dropy…). 🟢 <b>spáleno</b> = sedláci zmizeli z oběhu (nákupy v shopu, rake). Když se dlouhodobě tvoří víc než pálí, sedlák ztrácí hodnotu a je čas zvednout ceny v shopu nebo přidat odměny, za které se utrácí.</p>
    <div class="stat-grid">
      ${statBox(nf(h.circulation), "Sedláků v oběhu", "accent")}
      ${statBox("+" + nf(h.faucet_total), "Vytvořeno / " + h.days + " d")}
      ${statBox("−" + nf(h.sink_total), "Spáleno / " + h.days + " d")}
      ${statBox((h.net_total > 0 ? "+" : "") + nf(h.net_total), "Net (vytvořeno − spáleno)", h.net_total > 0 ? "warn" : "accent")}
    </div>
    <div class="faint" style="font-size:12px;margin-top:8px">Z každých <b>100</b> vytvořených sedláků se zase spálí <b>${burnedRatio}</b>. ${inflNote}</div>
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin:16px 0 6px">
      <b style="font-size:13.5px">Den po dni</b>
      <span class="faint" style="font-size:12px">aktivních diváků ø ${h.dau_avg} · max ${h.dau_peak}</span>
    </div>
    ${chart}
    <div class="faint" style="font-size:11.5px;margin-top:4px">Nahoře 🟠 kolik sedláků ten den vzniklo, dole 🟢 kolik se spálilo. Najeď myší na den pro detail.</div>
    <div class="dash-columns" style="margin-top:16px">
      <div><b style="font-size:13.5px">🟠 Odkud sedláci přitékají</b>${inflow.map((c) => catRow(c, inSum, "#e0a857")).join("") || `<div class="empty">Nic za období.</div>`}</div>
      <div><b style="font-size:13.5px">🟢 Kde sedláci mizí</b>${outflow.map((c) => catRow(c, outSum, "#46d369")).join("") || `<div class="empty">Nic za období.</div>`}</div>
    </div>
    <div class="faint" style="font-size:11.5px;margin-top:10px">♻️ = hry a sázky: sedláci hlavně kolují mezi diváky, počítá se jen čistý rozdíl (rake / rozehrané sázky). Procenta = podíl ve sloupci. Najetím na řádek uvidíš hrubé částky.</div>
  </div>`;
}
async function adminOverview() {
  const box = $("#adminContent");
  try {
    const d = await api("/admin/overview");
    box.innerHTML = `
      <div class="toolbar"><button class="btn btn-ghost btn-sm" data-action="overview-refresh">↻ Obnovit</button><span class="faint">Rychlý admin přehled za posledních 24 hodin.</span></div>
      <div class="stat-grid">
        ${statBox(d.stats24.new_users, "Nových účtů")}
        ${statBox(d.stats24.orders, "Objednávek 24 h")}
        ${statBox(d.stats24.pending_orders, "Čeká vyřídit", d.stats24.pending_orders ? "warn" : "")}
        ${statBox(d.stats24.drop_claims, "Drop claimů")}
        ${statBox("+" + Number(d.stats24.earned).toLocaleString("cs-CZ"), "Zisk bodů", "accent")}
        ${statBox("-" + Number(d.stats24.spent).toLocaleString("cs-CZ"), "Spáleno", "warn")}
      </div>
      <div id="topchatPanel"></div>
      <div class="section-title" style="margin-top:22px">Checklist před akcí</div>
      ${checklistHTML(d.checklist)}
      <div class="dash-columns" style="margin-top:18px">
        <div class="panel"><div class="section-title" style="margin-top:0">Podezřelé účty</div>${d.risky.length ? d.risky.map(userMini).join("") : `<div class="empty">Nic výrazně podezřelého.</div>`}</div>
        <div class="panel"><div class="section-title" style="margin-top:0">Watchlist</div>${d.watchlist.length ? d.watchlist.map(userMini).join("") : `<div class="empty">Watchlist je prázdný.</div>`}</div>
      </div>
      ${economyDashboardHTML(d.economy)}
      <div class="panel"><div class="section-title" style="margin-top:0">Poslední admin akce</div>
        ${auditTimeline(d.recent_audit || [])}
      </div>`;
    loadTopchatterCard();
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function loadTopchatterCard() {
  const box = document.getElementById("topchatPanel"); if (!box) return;
  try {
    const s = await api("/admin/topchatter/status");
    const medal = ["🥇", "🥈", "🥉"];
    const list = (s.today_top3 || []).length
      ? s.today_top3.map((t, i) => `<div class="row-between" style="padding:4px 0">
          <span>${medal[i] || ""} <b>${esc(t.username)}</b> <span class="faint">· ${t.msgs} 💬</span></span>
          <b style="color:var(--accent-2)">+${fmtPts(t.reward)}</b></div>`).join("")
      : `<div class="empty">Dnes zatím nikdo nepsal.</div>`;
    box.innerHTML = `<div class="panel" style="margin-top:18px">
      <div class="row-between"><div class="section-title" style="margin:0">🗣️ Top Chatteři dne</div>
        ${s.already_paid_today
          ? `<span class="tag-done">✓ Dnes vyplaceno</span>`
          : `<button class="btn btn-accent btn-sm" data-action="topchat-pay">💸 Vyplatit TOP 3 teď</button>`}</div>
      <p class="muted" style="font-size:12.5px;margin:6px 0 10px">TOP 3 berou <b>3000 / 2000 / 1000</b>. Jinak se vyplatí samo o půlnoci (UTC) — tady to pustíš hned po streamu.</p>
      ${list}</div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function payTopchatter() {
  if (!requireTypedConfirm("Vyplatit dnešní TOP 3 chattery teď? (3000/2000/1000). Zamkne aktuální pořadí a jen 1× denně.", "VYPLATIT")) return;
  try {
    const r = await api("/admin/topchatter/pay", { method: "POST" });
    if (r.ok) toast(`✅ Vyplaceno ${r.count}× — ${(r.winners || []).map((w) => w.username + " (+" + w.reward + ")").join(", ")}`, "success");
    else toast(r.error || "Nešlo vyplatit.", "error");
    adminOverview();
  } catch (e) { toast(e.message, "error"); }
}
const maintHHMM = (iso) => new Date(iso).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" });
async function loadMaintBanner() {
  const box = document.getElementById("maintBanner"); if (!box) return;
  try {
    const s = await api("/admin/maintenance");
    const on = !!s.maintenance;
    box.className = "maint-banner" + (on ? " on" : "");
    if (on) {
      const untilTxt = s.until ? `zpátky cca v <b>${maintHHMM(s.until)}</b> — web se pak <b>sám vrátí</b>` : "<b>bez odpočtu</b>";
      box.innerHTML = `
        <div class="mb-info"><span class="mb-ico">🟠</span>
          <div><div class="mb-title">Údržba ZAPNUTÁ — ${untilTxt}</div>
            <div class="mb-sub">Návštěvníci vidí údržbovou stránku${s.until ? " s živým odpočtem" : ""}. Ty (admin) vidíš web normálně.</div></div></div>
        <div class="mb-actions">
          ${s.until ? `<button class="btn btn-ghost btn-sm" data-action="maint-extend" data-mins="15">+15 min</button>` : ""}
          <input type="text" id="maintUntil" class="input input-sm" style="max-width:96px" placeholder="14:00" maxlength="5" inputmode="numeric" title="Konec údržby (24h, např. 14:00)">
          <button class="btn btn-danger btn-sm" data-action="maint-on-time">⏰ Skončit v…</button>
          <button class="btn btn-success btn-sm" data-action="maint-off">✅ Vypnout</button>
        </div>`;
    } else {
      box.innerHTML = `
        <div class="mb-info"><span class="mb-ico">🛠️</span>
          <div><div class="mb-title">Údržbový režim: <b>vypnutý</b></div>
            <div class="mb-sub">Zapni s odpočtem — po jeho vypršení se web návštěvníkům <b>sám vrátí</b>.</div></div></div>
        <div class="mb-actions">
          <span class="mb-lbl">Zapnout na:</span>
          <button class="btn btn-danger btn-sm" data-action="maint-on" data-mins="15">15 min</button>
          <button class="btn btn-danger btn-sm" data-action="maint-on" data-mins="30">30 min</button>
          <button class="btn btn-danger btn-sm" data-action="maint-on" data-mins="60">1 h</button>
          <button class="btn btn-danger btn-sm" data-action="maint-on" data-mins="120">2 h</button>
          <button class="btn btn-ghost btn-sm" data-action="maint-on" data-mins="0">napořád</button>
          <span class="mb-lbl" style="margin-left:6px">nebo do času:</span>
          <input type="text" id="maintUntil" class="input input-sm" style="max-width:96px" placeholder="14:00" maxlength="5" inputmode="numeric" title="24h formát, např. 14:00">
          <button class="btn btn-danger btn-sm" data-action="maint-on-time">⏰ Zapnout do času</button>
        </div>`;
    }
  } catch (e) { box.innerHTML = ""; }
}
async function doMaintOnTime() {
  const v = (document.getElementById("maintUntil")?.value || "").trim().replace(/\s/g, "");
  const mm = v.match(/^(\d{1,2}):(\d{2})$/) || v.match(/^(\d{2})(\d{2})$/);   // „14:00" i „1400"
  if (!mm) { toast("Zadej čas konce jako 14:00 (24h formát).", "error"); return; }
  const h = +mm[1], m = +mm[2];
  if (h > 23 || m > 59) { toast("Neplatný čas — použij 24h formát, např. 14:00.", "error"); return; }
  const now = new Date(), target = new Date(now);
  target.setHours(h, m, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 1);   // čas už dnes proběhl → ber zítra
  const mins = Math.max(1, Math.round((target - now) / 60000));
  try {
    const s = await api("/admin/maintenance?to=on&mins=" + mins, { method: "POST" });
    toast("🛠️ Údržba nastavena — web se sám vrátí v " + maintHHMM(s.until), "success");
    loadMaintBanner();
  } catch (e) { toast(e.message, "error"); }
}
async function doMaintOn(el) {
  try {
    const s = await api("/admin/maintenance?to=on&mins=" + (el.dataset.mins || "0"), { method: "POST" });
    toast(s.until ? "🛠️ Údržba zapnuta — zpátky cca v " + maintHHMM(s.until) : "🛠️ Údržba zapnuta (bez odpočtu)", "success");
    loadMaintBanner();
  } catch (e) { toast(e.message, "error"); }
}
async function doMaintOff() {
  try {
    await api("/admin/maintenance?to=off", { method: "POST" });
    toast("✅ Údržba vypnuta — web běží pro všechny.", "success");
    loadMaintBanner();
  } catch (e) { toast(e.message, "error"); }
}
async function doMaintExtend(el) {
  try {
    const s = await api("/admin/maintenance?to=extend&mins=" + (el.dataset.mins || "15"), { method: "POST" });
    toast("⏱️ Prodlouženo — zpátky cca v " + maintHHMM(s.until), "success");
    loadMaintBanner();
  } catch (e) { toast(e.message, "error"); }
}

function renderAdminTab(tab) {
  if (!canSection(state.user, tab)) { toast("Na tuhle sekci nemáš oprávnění.", "error"); return; }
  adminState.tab = tab;
  document.querySelectorAll('[data-action="admin-tab"]').forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  const box = $("#adminContent"); box.innerHTML = skeletonCards(1);
  if (tab === "overview") adminOverview();
  else if (tab === "products") adminProducts();
  else if (tab === "users") adminUsers();
  else if (tab === "subs") adminSubs();
  else if (tab === "orders") adminOrders();
  else if (tab === "raffles") adminRaffles();
  else if (tab === "predictions") adminPredictions();
  else if (tab === "codes") adminCodes();
  else if (tab === "drops") adminDrops();
  else if (tab === "games") adminGames();
  else if (tab === "bot") adminBot();
  else if (tab === "economy") adminEconomy();
  else if (tab === "news") adminNews();
  else if (tab === "security") adminSecurity();
  else if (tab === "modnabor") adminModApps();
  else if (tab === "gifts") adminGifts();
}

/* --- Admin: Ekonomika (pasivní výdělek) --- */
function ecoField(id, label, hint, val) {
  return `<div class="eco-row">
    <div><b>${label}</b><br><span class="faint" style="font-size:12px">${hint}</span></div>
    <input class="input" id="${id}" type="number" min="0" value="${val}" style="max-width:140px;text-align:right">
  </div>`;
}
function ecoToggle(id, label, hint, on) {
  return `<div class="eco-row">
    <div><b>${label}</b><br><span class="faint" style="font-size:12px">${hint}</span></div>
    <button class="toggle ${on ? "on" : ""}" id="${id}" data-action="eco-toggle" data-on="${on ? 1 : 0}"></button>
  </div>`;
}
function coinIconCardHTML() {
  return `<div class="panel" style="margin-bottom:16px">
    <div class="section-title" style="margin-top:0">🪙 Ikona měny (sedlák)</div>
    <p class="muted" style="font-size:13px;margin-bottom:12px">Obrázek, který se ukáže <b>všude místo oranžové kuličky</b> (u cen, zůstatku…). Ideálně čtverec, PNG s průhledným pozadím.</p>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="coin" style="width:38px;height:38px;border-radius:8px"></span>
      <button type="button" class="btn btn-sm" data-action="coin-upload">📁 Nahrát ikonu z PC</button>
      <span id="coinInfo" class="faint" style="font-size:12px"></span>
    </div>
    <input type="file" id="coinFile" accept="image/png,image/jpeg,image/webp,image/gif" style="display:none">
  </div>`;
}
function coinUploadClick() { const f = $("#coinFile"); if (f) f.click(); }
async function uploadCoinIcon() {
  const f = $("#coinFile");
  const file = f && f.files && f.files[0];
  if (!file) return;
  const info = $("#coinInfo");
  if (file.size > 6 * 1024 * 1024) { toast("Obrázek je příliš velký (max 6 MB).", "error"); f.value = ""; return; }
  if (info) info.textContent = "⏳ Nahrávám…";
  try {
    const dataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(new Error("Nešlo načíst soubor.")); r.readAsDataURL(file); });
    await api("/admin/economy/coin-icon", { method: "POST", body: { data: dataUrl } });
    const bust = `url('/uploads/coin.png?t=${Date.now()}') center/contain no-repeat, radial-gradient(circle at 35% 30%, #ffd98a, #ff9d2e 60%, #e07a10)`;
    document.querySelectorAll(".coin").forEach((c) => { c.style.background = bust; });   // hned obnov náhled i všechny mince
    if (info) info.textContent = "✅ Nahráno — projeví se všude (po Ctrl+F5).";
    toast("Ikona měny nahrána! 🌾", "success");
  } catch (e) { if (info) info.textContent = "⚠️ " + e.message; toast(e.message, "error"); }
  finally { if (f) f.value = ""; }
}
async function saveShopDiscount() {
  const pct = Math.max(0, Math.min(90, parseInt((document.getElementById("shop_disc_pct") || {}).value || "0", 10) || 0));
  const liveOnly = document.getElementById("shop_disc_live_only") && document.getElementById("shop_disc_live_only").classList.contains("on") ? 1 : 0;
  const sub2x = document.getElementById("shop_disc_sub_2x") && document.getElementById("shop_disc_sub_2x").classList.contains("on") ? 1 : 0;
  let minutes = Math.max(0, Math.min(1440, parseInt((document.getElementById("shop_disc_minutes") || {}).value || "0", 10) || 0));
  // „Konec v čase" (HH:MM v lokálním čase) přebije minuty: spočítej minuty do toho času (dnes, nebo zítra když už je po)
  const untilRaw = ((document.getElementById("shop_disc_until_time") || {}).value || "").trim();
  if (untilRaw) {
    let hh = NaN, mm = NaN;
    if (untilRaw.includes(":")) { const p = untilRaw.split(":"); hh = parseInt(p[0], 10); mm = parseInt(p[1], 10); }
    else { const d = untilRaw.replace(/\D/g, ""); if (d.length === 4) { hh = +d.slice(0, 2); mm = +d.slice(2); } else if (d.length === 3) { hh = +d.slice(0, 1); mm = +d.slice(1); } }
    if (!Number.isInteger(hh) || !Number.isInteger(mm) || hh < 0 || hh > 23 || mm < 0 || mm > 59) {
      toast("Neplatný čas — zadej 24h HH:MM, např. 22:00", "error"); return;
    }
    const target = new Date(); target.setHours(hh, mm, 0, 0);
    if (target <= new Date()) target.setDate(target.getDate() + 1);
    minutes = Math.max(1, Math.min(1440, Math.ceil((target - new Date()) / 60000)));
  }
  try {
    const r = await api("/admin/shop-discount", { method: "POST", body: { pct, live_only: liveOnly, sub_2x: sub2x, minutes } });
    const bits = [r.pct > 0 ? `−${r.pct}%` : null, r.sub_2x ? "2× subs 🟣" : null, r.live_only ? "jen live" : null, r.until ? `⏳ ${minutes} min` : null].filter(Boolean);
    toast(bits.length ? `Happy hour: ${bits.join(" · ")} 🔴` : "Happy hour vypnut", "success");
    adminEconomy();
  } catch (e) { toast(e.message, "error"); }
}
function retentionHTML(r) {
  if (!r) return "";
  return `
    <div class="panel" style="margin-bottom:16px">
      <div class="section-title" style="margin-top:0">📊 Retence <span class="faint" style="font-size:12px;font-weight:400">— kdo se vrací (aktivní = nedávno na webu, dle sessions)</span></div>
      <div class="stat-grid">
        ${statBox(r.dau, "DAU (dnes)", "accent")}
        ${statBox(r.wau, "WAU (7 dní)")}
        ${statBox(r.mau, "MAU (30 dní)")}
        ${statBox(r.stickiness + "%", "Stickiness DAU/MAU", r.stickiness >= 20 ? "accent" : "")}
      </div>
      <div class="stat-grid" style="margin-top:12px">
        ${statBox("+" + r.new_today, "Noví dnes")}
        ${statBox("+" + r.new_7d, "Noví za 7 dní")}
        ${statBox(r.retention_pct + "%", "Týdenní retence", r.retention_pct >= 40 ? "accent" : (r.retention_pct < 20 ? "warn" : ""))}
        ${statBox(r.churned, "Odpadlí (týden)", r.churned ? "warn" : "")}
      </div>
      <div class="faint" style="font-size:12px;margin-top:10px">Týdenní retence: z <b>${r.prev_week_active}</b> aktivních minulý týden se <b>${r.retained}</b> vrátilo i tento týden. Stickiness 20 %+ = zdravé · celkem účtů ${r.total_users}.</div>
    </div>`;
}
function gardenEconomyHTML(g) {
  if (!g || !g.by_window) return "";
  const win = (key, label) => {
    const d = g.by_window[key] || {};
    const net = d.net || 0;
    const verdict = net > 0
      ? `<b style="color:#e0a857">FAUCET +${fmtPts(net)}</b> <span class="faint">přidává do oběhu</span>`
      : `<b style="color:#46d369">SINK ${fmtPts(net)}</b> <span class="faint">ubírá z oběhu</span>`;
    return `<div class="ge-win">
      <div class="ge-win-h">${label}</div>
      <div class="ge-line"><span>📥 Příjmy (sklizeň)</span><b style="color:#46d369">+${fmtPts(d.prijmy || 0)}</b></div>
      <div class="ge-line"><span>📤 Výdaje (semínka+dekor)</span><b style="color:#ff6b6b">−${fmtPts(d.vydaje || 0)}</b></div>
      <div class="ge-line ge-net"><span>= Net</span><span>${verdict}</span></div>
    </div>`;
  };
  const crops = (g.per_crop || []).map((c) => `<tr>
    <td>${c.icon} ${esc(c.name)}</td>
    <td class="faint">${c.planted}× / ${c.harvested}×</td>
    <td style="color:#ff6b6b">−${fmtPts(c.seed_spent)}</td>
    <td style="color:#46d369">+${fmtPts(c.harvest_earned)}</td>
    <td style="color:${c.net > 0 ? "#e0a857" : "#46d369"};font-weight:700">${c.net > 0 ? "+" : ""}${fmtPts(c.net)}</td>
  </tr>`).join("");
  const users = (g.per_user || []).map((u) => `<tr><td>${uLink(u.username)}</td><td class="faint">+${fmtPts(u.gained)} / -${fmtPts(u.spent)}</td><td style="font-weight:800;color:${u.net > 0 ? "#e0a857" : "#46d369"}">${u.net > 0 ? "+" : ""}${fmtPts(u.net)}</td></tr>`).join("");
  const recent = (g.recent || []).map((r) => `<tr><td>${uLink(r.username)}</td><td class="${r.change >= 0 ? "pos" : "neg"}">${r.change >= 0 ? "+" : ""}${fmtPts(r.change)}</td><td>${esc(r.reason || "")}</td></tr>`).join("");
  const growing = (g.growing || []).length ? g.growing.map((x) => `${x.count}× ${x.icon || ""} ${esc(x.crop)}`).join(" · ") : "nic neroste";
  return `<div class="panel" style="margin-bottom:16px">
    <div class="section-title" style="margin-top:0">🌱 Ekonomika zahrádky — výdaje vs příjmy</div>
    <p class="muted" style="font-size:12.5px;margin:0 0 12px">Semínka + dekorace = <b>výdaje</b> (sink, ubírá body z oběhu). Sklizně = <b>příjmy</b> (faucet, přidává). <b>Net &gt; 0 = zahrádka tiskne peníze</b> → zvedni cenu semínka (teď ${g.seed_pct || "?"} %), když je to moc inflační. Net &lt; 0 = zdravé.</p>
    <div class="ge-wins">${win("d1", "24 h")}${win("d7", "7 dní")}${win("all", "Celkem")}</div>
    <div class="ge-crop-h">Podle plodin (celkem · zaseto / sklizeno)</div>
    <div class="table-wrap"><table class="tbl"><thead><tr><th>Plodina</th><th>Zaseto / sklizeno</th><th>Semínka</th><th>Sklizeň</th><th>Net</th></tr></thead><tbody>${crops}</tbody></table></div>
    <div class="ge-crop-h">Top uzivatele zahradky</div>
    <div class="table-wrap"><table class="tbl"><thead><tr><th>Uzivatel</th><th>Prijmy / vydaje</th><th>Net</th></tr></thead><tbody>${users || `<tr><td class="faint">zatim nic</td></tr>`}</tbody></table></div>
    <div class="ge-crop-h">Posledni zahradni pohyby</div>
    <div class="table-wrap"><table class="tbl"><thead><tr><th>Uzivatel</th><th>Zmena</th><th>Duvod</th></tr></thead><tbody>${recent || `<tr><td class="faint">zatim nic</td></tr>`}</tbody></table></div>
    <div class="faint" style="font-size:12px;margin-top:10px">🌿 Teď roste: ${growing}</div>
  </div>`;
}
function economyInsightsHTML(d) {
  if (!d) return "";
  const money = (n) => `${n >= 0 ? "+" : ""}${fmtPts(n || 0)}`;
  const rows = (items, cols) => (items || []).map((x) => `<tr>${cols.map((c) => c(x)).join("")}</tr>`).join("");
  const farmers = rows(d.top_farmers, [
    (x) => `<td>${uLink(x.username)}</td>`,
    (x) => `<td style="color:var(--accent);font-weight:800">+${fmtPts(x.farm_xp)}</td>`,
    (x) => `<td class="faint">gross +${fmtPts(x.farm_gross)}</td>`,
  ]);
  const gamblers = rows(d.top_gamblers, [
    (x) => `<td>${uLink(x.username)}</td>`,
    (x) => `<td>${fmtPts(x.gambling_volume)}</td>`,
    (x) => `<td style="font-weight:800;color:${x.gambling_net >= 0 ? "#46d369" : "#ff6b6b"}">${money(x.gambling_net)}</td>`,
  ]);
  const garden = rows(d.top_garden, [
    (x) => `<td>${uLink(x.username)}</td>`,
    (x) => `<td class="faint">+${fmtPts(x.garden_gained)} / −${fmtPts(x.garden_spent)}</td>`,
    (x) => `<td style="font-weight:800;color:${x.garden_net >= 0 ? "#e0a857" : "#46d369"}">${money(x.garden_net)}</td>`,
  ]);
  const flags = (d.red_flags || []).map((x) => `<tr>
    <td>${uLink(x.username)}</td>
    <td>${esc(x.label || x.reason || "")}</td>
    <td style="font-weight:800;color:#e0a857">${fmtPts(x.value || 0)}</td>
    <td class="faint">${fmtPts(x.threshold || 0)}</td>
  </tr>`).join("");
  const cats = (d.categories || []).map((c) => `<span class="code-pill" title="${esc(c.kind)}">${c.emoji} ${esc(c.label)}</span>`).join(" ");
  return `<div class="panel" style="margin-bottom:16px">
    <div class="section-title" style="margin-top:0">📊 Farm vs gambling vs zahrádka <span class="faint" style="font-size:12px;font-weight:400">posledních ${d.days} d</span></div>
    ${flags ? `<div style="margin-bottom:14px"><b style="color:#e0a857">Red flags</b><div class="table-wrap" style="margin-top:8px"><table class="tbl"><thead><tr><th>Uzivatel</th><th>Duvod</th><th>Hodnota</th><th>Limit</th></tr></thead><tbody>${flags}</tbody></table></div></div>` : ""}
    <div class="grid-3">
      <div><b>Top farm XP</b><div class="table-wrap" style="margin-top:8px"><table class="tbl"><tbody>${farmers || `<tr><td class="faint">nic</td></tr>`}</tbody></table></div></div>
      <div><b>Top gambling obrat</b><div class="table-wrap" style="margin-top:8px"><table class="tbl"><tbody>${gamblers || `<tr><td class="faint">nic</td></tr>`}</tbody></table></div></div>
      <div><b>Top zahrádka net</b><div class="table-wrap" style="margin-top:8px"><table class="tbl"><tbody>${garden || `<tr><td class="faint">nic</td></tr>`}</tbody></table></div></div>
    </div>
    <div class="faint" style="font-size:12px;margin-top:12px">Normalizace reasons: ${cats}</div>
  </div>`;
}
async function adminEconomy() {
  const box = $("#adminContent");
  try {
    const [e, lv, dash, rake, health, hh, ret, garden, insights] = await Promise.all([api("/admin/economy"), api("/admin/economy/live"), api("/admin/economy/dashboard"), api("/admin/economy/games-rake"), api("/admin/economy/health?days=14").catch(() => null), api("/admin/shop-discount").catch(() => ({ pct: 0, live_only: false, active_now: 0 })), api("/admin/analytics/retention").catch(() => null), api("/admin/economy/garden").catch(() => null), api("/admin/economy/insights?days=1").catch(() => null)]);
    const modeBtn = (m, label) => `<button class="btn btn-sm ${lv.mode === m ? "btn-primary" : "btn-ghost"}" data-action="eco-live-mode" data-mode="${m}">${label}</button>`;
    box.innerHTML = `
      ${economyDashboardHTML(dash)}
      ${economyInsightsHTML(insights)}
      ${retentionHTML(ret)}
      ${economyHealthHTML(health)}
      ${gardenEconomyHTML(garden)}
      ${coinIconCardHTML()}
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title" style="margin-top:0">📡 Stream — body za sledování jen když je LIVE</div>
        <div class="row-between" style="margin-bottom:12px">
          <div>${lv.live
            ? `<span style="color:#46d369;font-weight:800;font-size:18px">🔴 LIVE</span> <span class="faint">— body za sledování běží</span>`
            : `<span style="color:#9aa;font-weight:800;font-size:18px">⚫ Offline</span> <span class="faint">— body za sledování se nepřičítají</span>`}</div>
          <button class="btn btn-ghost btn-sm" data-action="eco-live-refresh">↻ Obnovit</button>
        </div>
        <div class="faint" style="font-size:12.5px;margin-bottom:8px">Režim detekce (kick.com/zurys1337):</div>
        <div class="toolbar">${modeBtn("auto", "Auto (Kick API)")} ${modeBtn("on", "Vždy zapnuto")} ${modeBtn("off", "Vždy vypnuto")}</div>
        ${lv.mode === "auto" && !lv.detectable ? `<div style="font-size:12.5px;margin-top:10px;color:#e0a857">⚠️ Auto-detekce potřebuje <b>připojeného reálného bota</b> (Kick API). Dokud není, počítá se jako offline — nebo přepni na „Vždy zapnuto”, když jsi live.</div>` : ""}
      </div>
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title" style="margin-top:0">🔴 Happy hour — sleva na shop</div>
        <p class="muted" style="font-size:13px;margin-bottom:14px">Dočasná sleva na <b>všechny nákupy</b> v shopu — láká diváky utrácet (a sledovat živě, když dáš „jen když live”). 0 % = vypnuto. ${hh.active_now ? `<b style="color:#46d369">Teď aktivní: −${hh.active_now} %</b>` : `<span class="faint">Teď: vypnuto</span>`}</p>
        <div class="eco-row"><div><b>Sleva (%)</b><br><span class="faint" style="font-size:12px">0–90 % (0 = vypnuto)</span></div><input class="input" id="shop_disc_pct" type="number" min="0" max="90" value="${hh.pct}" style="width:92px"></div>
        ${ecoToggle("shop_disc_live_only", "Jen když je LIVE", "Sleva platí jen během streamu (víc concurrent diváků)", hh.live_only)}
        ${ecoToggle("shop_disc_sub_2x", "2× body za subs a gift subs 🟣", "Během happy hour dají subscribe, resub i gift sub dvojnásob sedláků. Sdílí přepínač jen-když-live. Funguje i bez slevy na shop.", hh.sub_2x)}
        <div class="eco-row"><div><b>Časovač (min)</b><br><span class="faint" style="font-size:12px">0 = bez limitu; jinak se happy hour sám vypne za N min</span></div><input class="input" id="shop_disc_minutes" type="number" min="0" max="1440" value="0" style="width:92px"></div>
        <div class="eco-row"><div><b>…nebo konec v čase</b><br><span class="faint" style="font-size:12px">24h formát HH:MM (např. 22:00) ve tvém čase — přebije minuty; prázdné = nepoužít</span></div><input class="input" id="shop_disc_until_time" type="text" inputmode="numeric" placeholder="HH:MM" maxlength="5" value="" style="width:120px"></div>
        ${hh.until ? `<div class="faint" style="font-size:12.5px;margin-top:8px;color:#46d369">⏳ Časovač běží — happy hour se sám vypne <b>${new Date(hh.until).toLocaleTimeString("cs-CZ")}</b>.</div>` : ""}
        <div class="row-between" style="margin-top:16px"><span class="faint" style="font-size:12px">Projeví se hned (banner + škrtnuté ceny v shopu; 2× subs platí pro nové Kick eventy).</span><button class="btn btn-accent" data-action="shop-disc-save">💾 Uložit happy hour</button></div>
      </div>
      <div class="panel">
        <div class="section-title" style="margin-top:0">💰 Ekonomika – pasivní výdělek</div>
        <p class="muted" style="font-size:13px;margin-bottom:16px">Body za <b>sledování</b> a <b>aktivitu v chatu</b>. Násobič zvýhodňuje SUB/VIP – motivace sledovat a psát. Násobič se NEvztahuje na nákupy ani admin granty.</p>
        ${ecoToggle("eco_watch_enabled", "Body za sledování", "Divák dostává sedláky za každou minutu u streamu", e.eco_watch_enabled)}
        ${ecoField("eco_pts_per_min", "Sedláci / minutu sledování", "Základ pro běžného diváka (Free)", e.eco_pts_per_min)}
        ${ecoToggle("eco_chat_enabled", "Body za chat aktivitu", "Divák dostává sedláky za aktivní psaní do chatu", e.eco_chat_enabled)}
        ${ecoField("eco_chat_pts", "Sedláci / zprávu v chatu", "Odměna za aktivní zprávu (× násobič)", e.eco_chat_pts)}
        ${ecoField("eco_chat_cooldown_s", "Cooldown chatu (s)", "Min. rozestup mezi odměnami za chat", e.eco_chat_cooldown_s)}
        <div class="eco-sep">Násobiče rolí</div>
        ${ecoField("eco_sub_mult", "Násobič pro SUB", "Kolikrát víc bodů má subscriber (×)", e.eco_sub_mult)}
        ${ecoField("eco_vip_mult", "Násobič pro VIP", "Kolikrát víc bodů má VIP (×)", e.eco_vip_mult)}
        <div class="eco-sep">Body za Kick eventy 🟢 <span class="faint" style="font-weight:400;font-size:11px">(přičte webhook — až bude napojený)</span></div>
        ${ecoField("eco_sub_pts", "Sedláci za sub", "Nový subscriber", e.eco_sub_pts)}
        ${ecoField("eco_resub_pts", "Sedláci za resub", "Obnovení subu", e.eco_resub_pts)}
        ${ecoField("eco_giftsub_pts", "Sedláci za gift sub", "Za KAŽDÝ darovaný sub (5× = 5000)", e.eco_giftsub_pts)}
        ${ecoField("eco_follow_pts", "Sedláci za follow", "Jednorázově za follow", e.eco_follow_pts)}
        <div class="eco-sep">Limity</div>
        ${ecoField("eco_daily_cap", "Denní strop výdělku (sedláci)", "Max pasivních bodů za den (anti-farm)", e.eco_daily_cap)}
        ${ecoField("eco_games_cap", "Denní strop zisku z HER (0 = bez limitu)", "Max čistý zisk z coinflip/kostky/piškvorky za den – brzda na grind", e.eco_games_cap)}
        ${ecoField("eco_wager_cap", "Denni strop SAZEK (global)", "Max protočených sedláků za den napříč Mines/PvP/predikcemi; 0 = vypnuto", e.eco_wager_cap)}
        <div class="row-between" style="margin-top:18px">
          <span class="faint" style="font-size:12px">Změny platí okamžitě pro všechny.</span>
          <button class="btn btn-accent" data-action="eco-save">💾 Uložit nastavení</button>
        </div>
      </div>
      <div class="panel" style="margin-top:16px">
        <div class="section-title" style="margin-top:0">🎲 Rake na hrách & duelech</div>
        <p class="muted" style="font-size:12.5px;margin-bottom:12px">Kolik % z banku si bere house (coinflip, kostky, piškvorky). Vítěz dostane <b>(100 − rake) %</b> → house edge + sink (proti nekonečnému grindění). Teď <b>${rake.rake_pct}%</b>, vítěz bere <b>${100 - rake.rake_pct}%</b>.</p>
        <div class="eco-row"><div><b>Rake (%)</b><br><span class="faint" style="font-size:12px">0 = férové 50/50 bez poplatku · doporučeno 2</span></div>
          <span style="display:flex;gap:6px;align-items:center"><input class="input input-sm" id="gamesRake" type="number" min="0" max="50" value="${rake.rake_pct}" style="width:84px;text-align:right"><button class="btn btn-sm btn-accent" data-action="games-rake-save">💾 Uložit</button></span></div>
      </div>
      <div id="adminPartners" style="margin-top:16px"></div>`;
    loadAdminPartners();
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function saveEconomy() {
  const ids = ["eco_pts_per_min","eco_sub_mult","eco_vip_mult","eco_chat_pts","eco_chat_cooldown_s","eco_daily_cap","eco_games_cap","eco_wager_cap","eco_sub_pts","eco_resub_pts","eco_giftsub_pts","eco_follow_pts"];
  const body = {};
  ids.forEach((id) => { const el = $("#" + id); if (el) body[id] = parseInt(el.value, 10) || 0; });
  ["eco_watch_enabled","eco_chat_enabled"].forEach((id) => { const el = $("#" + id); if (el) body[id] = el.classList.contains("on") ? 1 : 0; });
  try { await api("/admin/economy", { method: "POST", body }); toast("Ekonomika uložena. ✅", "success"); adminEconomy(); }
  catch (e) { toast(e.message, "error"); }
}
async function saveGamesRake() {
  const pct = parseInt(($("#gamesRake") || {}).value, 10);
  if (isNaN(pct) || pct < 0 || pct > 50) { toast("Rake musí být 0–50 %.", "error"); return; }
  try { const r = await api("/admin/economy/games-rake", { method: "POST", body: { rake_pct: pct } }); toast(`Rake nastaven na ${r.rake_pct}% (vítěz bere ${100 - r.rake_pct}%). ✅`, "success"); adminEconomy(); }
  catch (e) { toast(e.message, "error"); }
}
async function setLiveMode(mode) {
  try {
    await api("/admin/economy/live", { method: "POST", body: { mode } });
    toast("Režim sledování: " + ({ auto: "Auto (Kick API)", on: "Vždy zapnuto", off: "Vždy vypnuto" }[mode] || mode), "success");
    adminEconomy();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: partnerské/sponzorské odkazy (klikni-a-ber bonus) --- */
async function loadAdminPartners() {
  const box = document.getElementById("adminPartners"); if (!box) return;
  try {
    const [d, fl] = await Promise.all([api("/admin/economy/partner-links"), api("/admin/economy/partner-flash")]);
    const links = d.links || [];
    const rowHTML = (l) => `<div class="eco-row" style="flex-wrap:wrap;gap:6px;${l.enabled ? "" : "opacity:.5"}">
      <input class="input input-sm" id="pl-${l.id}-icon" value="${esc(l.icon || "🤝")}" maxlength="8" style="width:46px;text-align:center" title="Ikona">
      <input class="input input-sm" id="pl-${l.id}-label" value="${esc(l.label)}" placeholder="Popisek" style="width:140px">
      <input class="input input-sm" id="pl-${l.id}-url" value="${esc(l.url)}" placeholder="https://…" style="flex:1;min-width:150px">
      <input class="input input-sm" id="pl-${l.id}-reward" value="${l.reward}" type="number" min="0" style="width:76px;text-align:right" title="Odměna">
      <button class="btn btn-sm ${l.mode === "flash" ? "btn-accent" : "btn-ghost"}" data-action="pl-mode" data-id="${l.id}" data-mode="${l.mode}" title="Režim: 1× navždy / ⚡ Flash">${l.mode === "flash" ? "⚡ Flash" : "1× navždy"}</button>
      <span class="faint" style="font-size:12px" title="Vyzvednutí once / flash">🖱️ ${l.claims}${l.flash_claims ? " +" + l.flash_claims + "⚡" : ""}</span>
      <button class="btn btn-sm ${l.enabled ? "btn-primary" : "btn-ghost"}" data-action="pl-toggle" data-id="${l.id}" data-on="${l.enabled ? 1 : 0}">${l.enabled ? "Zap" : "Vyp"}</button>
      <button class="btn btn-sm btn-accent" data-action="pl-save" data-id="${l.id}">💾</button>
      <button class="btn btn-sm btn-ghost" data-action="pl-del" data-id="${l.id}">🗑️</button>
    </div>`;
    const flBadge = fl.active ? `<span style="color:#e8b923;font-weight:700">⚡ BĚŽÍ teď</span>`
      : (fl.pflash_enabled ? "zapnuto, čeká na náhodný spoušť" : "vypnuto");
    box.innerHTML = `<div class="panel">
      <div class="section-title" style="margin-top:0">🤝 Partnerské odkazy <span class="faint" style="font-weight:400;font-size:13px">– „klikni a ber” bonus v Bonusech</span></div>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px"><b>1× navždy</b> = každý vyzvedne jednou. <b>⚡ Flash</b> = jen během náhodného okna (nastav níž). Ověřuje se klik (ne návštěva cíle). ⚠️ U affiliate pozor na ToS.</p>
      ${links.map(rowHTML).join("") || `<div class="faint" style="margin-bottom:10px">Zatím žádný odkaz. Přidej první níž. 👇</div>`}
      <div class="eco-sep">➕ Přidat odkaz</div>
      <div class="eco-row" style="flex-wrap:wrap;gap:6px">
        <input class="input input-sm" id="pl-new-icon" value="🤝" maxlength="8" style="width:46px;text-align:center" title="Ikona">
        <input class="input input-sm" id="pl-new-label" placeholder="Popisek (např. Náš sponzor XY)" style="width:190px">
        <input class="input input-sm" id="pl-new-url" placeholder="https://…" style="flex:1;min-width:150px">
        <input class="input input-sm" id="pl-new-reward" type="number" min="0" value="100" style="width:76px;text-align:right" title="Odměna">
        <button class="btn btn-sm btn-accent" data-action="pl-add">Přidat</button>
      </div>
    </div>
    <div class="panel" style="margin-top:12px">
      <div class="section-title" style="margin-top:0">⚡ Flash bonus <span class="faint" style="font-weight:400;font-size:13px">– náhodná obnova „flash” odkazů + bot do chatu</span></div>
      <p class="muted" style="font-size:12.5px;margin-bottom:12px">Když zapnuto a jsi <b>LIVE</b>, v náhodném intervalu se „flash” odkazy obnoví a bot to napíše do chatu. Stav: ${flBadge} · <b>${fl.flash_links}</b> flash odkazů.</p>
      ${ecoToggle("pf_enabled", "Flash zapnutý", "Hlavní vypínač automatického flashe", fl.pflash_enabled)}
      ${ecoToggle("pf_only_live", "Jen když je LIVE", "Neflashuj (a nespamuj chat), když nevysíláš", fl.pflash_only_live)}
      <div class="eco-row"><div><b>Interval OD–DO (min)</b><br><span class="faint" style="font-size:12px">Náhodná pauza mezi flashe</span></div>
        <span style="display:flex;gap:6px"><input class="input input-sm" id="pf_imin" type="number" min="1" value="${fl.pflash_interval_min}" style="width:72px;text-align:right"><input class="input input-sm" id="pf_imax" type="number" min="1" value="${fl.pflash_interval_max}" style="width:72px;text-align:right"></span></div>
      ${ecoField("pf_window", "Délka okna (min)", "Jak dlouho jde flash vyzvednout", fl.pflash_window_min)}
      <div class="row-between" style="margin-top:16px">
        <button class="btn btn-ghost btn-sm" data-action="pf-trigger" title="Spustí flash kolo HNED (i mimo live) – test">⚡ Spustit teď (test)</button>
        <button class="btn btn-accent" data-action="pf-save">💾 Uložit flash</button>
      </div>
    </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function _plReadBody(id) {
  const p = id == null ? "pl-new-" : ("pl-" + id + "-");
  const enBtn = id == null ? null : document.querySelector('[data-action="pl-toggle"][data-id="' + id + '"]');
  const moBtn = id == null ? null : document.querySelector('[data-action="pl-mode"][data-id="' + id + '"]');
  return {
    icon: ($("#" + p + "icon")?.value || "🤝").trim() || "🤝",
    label: ($("#" + p + "label")?.value || "").trim(),
    url: ($("#" + p + "url")?.value || "").trim(),
    reward: parseInt($("#" + p + "reward")?.value, 10) || 0,
    enabled: enBtn ? enBtn.dataset.on === "1" : true,
    mode: moBtn ? (moBtn.dataset.mode || "once") : "once",
  };
}
async function addPartnerLink() {
  const body = _plReadBody(null);
  if (!body.label || !body.url) { toast("Vyplň popisek i URL.", "error"); return; }
  try { await api("/admin/economy/partner-links", { method: "POST", body }); toast("Odkaz přidán. ✅", "success"); loadAdminPartners(); }
  catch (e) { toast(e.message, "error"); }
}
async function savePartnerLink(id) {
  const body = _plReadBody(id);
  if (!body.label || !body.url) { toast("Vyplň popisek i URL.", "error"); return; }
  try { await api("/admin/economy/partner-links/" + id, { method: "POST", body }); toast("Uloženo. ✅", "success"); loadAdminPartners(); }
  catch (e) { toast(e.message, "error"); }
}
async function togglePartnerLink(id, on) {
  const body = _plReadBody(id); body.enabled = !on;          // data-on = aktuální stav → překlopit
  if (!body.label || !body.url) { toast("Vyplň popisek i URL.", "error"); return; }
  try { await api("/admin/economy/partner-links/" + id, { method: "POST", body }); toast(body.enabled ? "Zapnuto. ✅" : "Vypnuto.", "success"); loadAdminPartners(); }
  catch (e) { toast(e.message, "error"); }
}
async function modePartnerLink(id, cur) {
  const body = _plReadBody(id); body.mode = cur === "flash" ? "once" : "flash";
  if (!body.label || !body.url) { toast("Vyplň popisek i URL.", "error"); return; }
  try { await api("/admin/economy/partner-links/" + id, { method: "POST", body }); toast(body.mode === "flash" ? "Režim: ⚡ Flash" : "Režim: 1× navždy", "success"); loadAdminPartners(); }
  catch (e) { toast(e.message, "error"); }
}
async function delPartnerLink(id) {
  if (!confirm("Smazat tenhle partnerský odkaz? (smažou se i záznamy o vyzvednutí)")) return;
  try { await api("/admin/economy/partner-links/" + id, { method: "DELETE" }); toast("Smazáno.", "success"); loadAdminPartners(); }
  catch (e) { toast(e.message, "error"); }
}
async function savePartnerFlash() {
  const body = {
    pflash_enabled: $("#pf_enabled")?.classList.contains("on") ? 1 : 0,
    pflash_only_live: $("#pf_only_live")?.classList.contains("on") ? 1 : 0,
    pflash_interval_min: parseInt($("#pf_imin")?.value, 10) || 1,
    pflash_interval_max: parseInt($("#pf_imax")?.value, 10) || 1,
    pflash_window_min: parseInt($("#pf_window")?.value, 10) || 1,
  };
  try { await api("/admin/economy/partner-flash", { method: "POST", body }); toast("Flash nastavení uloženo. ✅", "success"); loadAdminPartners(); }
  catch (e) { toast(e.message, "error"); }
}
async function triggerPartnerFlash() {
  try {
    const r = await api("/admin/economy/partner-flash/trigger", { method: "POST" });
    if (r.ok) toast("⚡ Flash kolo spuštěno! Bot to napsal do chatu.", "success");
    else toast(r.error || "Nepodařilo se spustit.", "error");
    loadAdminPartners();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Kick bot (SedlakBOT) --- */
function botMsgRow(m) {
  const badge = m.kind === "drop" ? `<span class="bm-tag drop">DROP</span>`
    : m.kind === "system" ? `<span class="bm-tag sys">SYS</span>` : "";
  const status = m.sent_real ? `<span class="bm-ok" title="Odesláno na Kick">✓</span>`
    : (m.error ? `<span class="bm-err" title="${esc(m.error)}">✕</span>`
      : `<span class="bm-demo" title="Demo – neodesláno">◴</span>`);
  return `<div class="bm-line"><span class="bm-bot">🤖 ${esc(m.author || "bot")}</span>${badge}
    <span class="bm-text">${esc(m.content)}</span>
    <span class="bm-meta">${status} <span class="faint">${timeAgo(m.created_at)}</span></span></div>`;
}

async function adminBot() {
  const box = $("#adminContent");
  try {
    const st = await api("/admin/bot/status");
    const connected = st.connected;
    const modeBadge = !connected ? `<span class="badge badge-admin">NEPŘIPOJEN</span>`
      : st.mode === "real" ? `<span class="badge badge-ok">● REÁLNÝ</span>`
        : `<span class="badge badge-vip">● DEMO</span>`;
    const msgs = st.messages || [];
    box.innerHTML = `
      <div class="panel" style="margin-bottom:16px">
        <div class="row-between" style="align-items:flex-start">
          <div>
            <div class="section-title" style="margin:0">🤖 ${esc(st.bot_username)} ${modeBadge}</div>
            <div class="muted" style="font-size:13px;margin-top:6px">Píše do kanálu <b>kick.com/${esc(st.channel)}</b></div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
            ${connected
              ? `<button class="btn btn-ghost btn-sm" data-action="bot-disconnect">Odpojit</button>`
              : `<button class="btn btn-success btn-sm" data-action="bot-connect-real">⚡ Připojit ${esc(st.bot_username)} (chat:write)</button>
                 <button class="btn btn-ghost btn-sm" data-action="bot-demo-connect">▶️ Demo připojení</button>`}
          </div>
        </div>
        ${!connected ? `<div class="demo-box" style="margin-top:12px;text-align:left">ℹ️ <b>Připojit</b> = přihlas bot účet přes Kick OAuth (v prohlížeči musíš být na Kicku přihlášený jako <b>${esc(st.bot_username)}</b>, ne jako ty). <b>Demo</b> = vyzkoušej konzoli i auto-post lokálně bez Kicku.</div>` : ""}
        <div class="bot-toggle" style="margin-top:14px">
          <label class="switch-row">
            <span><b>Auto-post dropů</b><br><span class="faint" style="font-size:12px">Při spuštění dropu bot automaticky hodí kód do chatu</span></span>
            <button class="toggle ${st.auto_post ? "on" : ""}" data-action="bot-auto-post" data-on="${st.auto_post ? 1 : 0}"></button>
          </label>
        </div>
      </div>

      ${connected && st.mode === "real" ? `
      <div class="panel" style="margin-bottom:16px">
        <div class="section-title" style="margin-top:0">🔌 Kick napojení — body za eventy</div>
        <p class="muted" style="font-size:13px;margin-bottom:8px">Automatické přičítání sedláků za <b>sub / resub / gift sub / follow / chat</b> přes Kick webhook. Kolik za co nastavíš v ⚙️ <b>Ekonomice</b>.</p>
        <ol class="faint" style="font-size:12.5px;margin:0 0 12px;padding-left:20px;line-height:1.7">
          <li>V Kick <b>app settings</b> nastav Webhook URL na <span class="code-pill">https://zurys.live/api/kick/webhook</span></li>
          <li>Klikni níž na <b>Aktivovat napojení</b> — přihlásí eventy přes tvůj token.</li>
        </ol>
        <button class="btn btn-primary" data-action="bot-subscribe-events">🔌 Aktivovat napojení (subscribe eventy)</button>
        <div id="subResult" style="font-size:12.5px;margin-top:10px"></div>
      </div>` : ""}

      <div class="panel" style="margin-bottom:16px">
        <div class="section-title" style="margin-top:0">✍️ Poslat zprávu jako bot</div>
        <form class="form" data-submit="bot-send">
          <textarea class="input" id="botMsg" rows="2" maxlength="480" placeholder="Napiš zprávu, kterou ${esc(st.bot_username)} pošle do chatu…" ${connected ? "" : "disabled"}></textarea>
          <div class="row-between" style="margin-top:10px">
            <span class="faint" style="font-size:12px">${connected ? (st.mode === "demo" ? "Demo režim – zpráva se jen zaloguje." : "Reálně odešle na Kick.") : "Nejdřív připoj bota."}</span>
            <button class="btn btn-accent btn-sm" type="submit" ${connected ? "" : "disabled"}>Odeslat ➤</button>
          </div>
        </form>
      </div>

      <div class="kick-chat">
        <div class="kc-head"><span class="kc-dot"></span> kick.com/${esc(st.channel)} <span class="faint" style="margin-left:auto;font-size:12px">log bota</span></div>
        <div class="kc-body" id="botChat">
          ${msgs.length ? msgs.map(botMsgRow).join("") : `<div class="empty" style="padding:30px">Zatím žádné zprávy. Pošli první nebo spusť drop.</div>`}
        </div>
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="section-title" style="margin-top:0">🧪 Simulace chat aktivity <span class="faint" style="font-size:12px">(test odměn za psaní)</span></div>
        <p class="muted" style="font-size:12.5px;margin-bottom:10px">Napiš Kick nick existujícího diváka – simuluje jeho zprávu v chatu a připíše mu body za aktivitu (dle Ekonomiky). Reálně tohle udělá Kick chat reader.</p>
        <form class="toolbar" data-submit="bot-sim-chat">
          <input class="input input-sm" id="simNick" placeholder="kick nick (např. divak)" style="max-width:220px">
          <button class="btn btn-sm" type="submit">💬 Simulovat zprávu</button>
        </form>
      </div>`;
    const chat = $("#botChat"); if (chat) chat.scrollTop = chat.scrollHeight;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function botSend() {
  const ta = $("#botMsg"); if (!ta) return;
  const content = ta.value.trim();
  if (!content) { toast("Napiš nějakou zprávu.", "error"); return; }
  try {
    const r = await api("/admin/bot/send", { method: "POST", body: { content } });
    if (r.sent) { ta.value = ""; toast(r.real ? "Odesláno na Kick. ✓" : "Zalogováno (demo). ◴", "success"); adminBot(); }
    else toast("Nepodařilo se: " + (r.error || "neznámá chyba"), "error");
  } catch (e) { toast(e.message, "error"); }
}
async function botDemoConnect() {
  try { await api("/admin/bot/demo-connect", { method: "POST" }); toast("Bot připojen (demo).", "success"); adminBot(); }
  catch (e) { toast(e.message, "error"); }
}
async function botDisconnect() {
  try { await api("/admin/bot/disconnect", { method: "POST" }); toast("Bot odpojen.", "info"); adminBot(); }
  catch (e) { toast(e.message, "error"); }
}
async function botToggleAutoPost(on) {
  try { await api("/admin/bot/auto-post", { method: "POST", body: { enabled: on } }); adminBot(); }
  catch (e) { toast(e.message, "error"); }
}
async function botSubscribeEvents() {
  const box = $("#subResult");
  if (box) box.textContent = "Aktivuji…";
  try {
    const r = await api("/admin/bot/subscribe-events", { method: "POST" });
    if (r.ok) {
      toast(`Napojení aktivní — přihlášeno ${r.subscribed} eventů 🔌`, "success");
      if (box) box.innerHTML = `<span style="color:#46d369">✅ Hotovo — přihlášeno <b>${r.subscribed}</b> eventů. Otestuj reálným subem/followem/zprávou.</span>`;
    } else {
      toast("Aktivace selhala — viz detail", "error");
      if (box) box.innerHTML = `<span style="color:#e07a7a">⚠️ ${esc(String(r.error || r.status || "chyba"))}</span>`;
    }
  } catch (e) { toast(e.message, "error"); if (box) box.textContent = e.message; }
}
async function botSimChat() {
  const el = $("#simNick"); if (!el) return;
  const nick = el.value.trim();
  if (!nick) { toast("Zadej Kick nick.", "error"); return; }
  try {
    const r = await api("/admin/bot/simulate-chat", { method: "POST", body: { kick_username: nick } });
    if (r.awarded > 0) toast(`${nick}: +${r.awarded} sedláků za aktivitu ✅`, "success");
    else toast(`${nick}: bez odměny (${r.error || (r.cooldown ? "cooldown " + r.cooldown + "s" : "strop")})`, "info");
    adminBot();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Odměny --- */
async function adminProducts() {
  const box = $("#adminContent");
  try {
    const list = await api("/admin/products");
    adminState.products = list;
    box.innerHTML = `
      <div class="toolbar"><button class="btn btn-primary" data-action="product-new">➕ Přidat odměnu</button><span class="muted">${list.length} odměn</span></div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Název</th><th>Kat.</th><th>Typ</th><th>Cena</th><th>Příznaky</th><th>Sklad</th><th>Aktivní</th><th></th></tr></thead><tbody>
      ${list.map((p) => `<tr>
        <td>${esc(p.name)}</td><td class="faint">${esc(p.category || "—")}</td><td>${TYPE_LABEL[p.type] || p.type}</td>
        <td><b>${fmtPts(p.cost_points)}</b></td>
        <td>${p.subs_only ? `<span class="badge badge-sub">SUB</span> ` : ""}${p.vip_only ? `<span class="badge badge-vip">VIP</span>` : ""}${!p.subs_only && !p.vip_only ? `<span class="faint">—</span>` : ""}</td>
        <td>${p.unlimited ? "∞" : p.stock}</td>
        <td>${p.active ? `<span class="tag-done">ano</span>` : `<span class="neg">ne</span>`}</td>
        <td><div class="tbl-actions"><button class="btn btn-ghost btn-sm" data-action="product-edit" data-id="${p.id}">✏️</button><button class="btn btn-danger btn-sm" data-action="product-delete" data-id="${p.id}">🗑️</button></div></td>
      </tr>`).join("")}
      </tbody></table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
// ISO (UTC) → hodnota pro <input type="datetime-local"> (lokální čas), a zpět
function isoToLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso); if (isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function localInputToIso(val) {
  if (!val) return "";
  const d = new Date(val); return isNaN(d) ? "" : d.toISOString();
}

function productForm(p) {
  p = p || { name: "", image_url: "", cost_points: 100, category: "", type: "instant", period: "", subs_only: false, vip_only: false, stock: -1, description: "", active: true, hot: false, ends_at: null, max_per_person_pct: 0 };
  const types = Object.entries(TYPE_LABEL).map(([k, l]) => `<option value="${k}" ${p.type === k ? "selected" : ""}>${l}</option>`).join("");
  const periods = PERIOD_OPTIONS.map(([k, l]) => `<option value="${k}" ${(p.period || "") === k ? "selected" : ""}>${l}</option>`).join("");
  openModal(`<div class="modal-body">
    <h2>${p.id ? "Upravit odměnu" : "Nová odměna"}</h2>
    <form class="form" data-submit="save-product" style="margin-top:16px">
      <input type="hidden" id="pf_id" value="${p.id || ""}">
      <div class="field"><label>Název</label><input class="input" id="pf_name" value="${esc(p.name)}" required></div>
      <div class="field"><label>Obrázek (URL, volitelné)</label>
        <div style="display:flex;gap:8px">
          <input class="input" id="pf_image" value="${esc(p.image_url)}" placeholder="https://… / 📁 z PC / 🔍 skin" style="flex:1">
          <button type="button" class="btn btn-sm" data-action="upload-image" title="Nahrát obrázek z počítače" style="white-space:nowrap">📁 Z PC</button>
          <button type="button" class="btn btn-sm" data-action="skin-picker" title="Vyhledat CS2 skin z katalogu (s náhledy)" style="white-space:nowrap">🔍 Najít skin</button>
        </div>
        <input type="file" id="pf_file" accept="image/png,image/jpeg,image/webp,image/gif" style="display:none">
        <div id="pf_skininfo" class="faint" style="font-size:11.5px;margin-top:6px"></div>
        <div id="skinPicker" class="skin-picker" style="display:none">
          <div style="display:flex;gap:8px">
            <input class="input" id="skinQ" placeholder="napiš skin: asiimov, butterfly, dragon lore…" autocomplete="off" style="flex:1">
            <button type="button" class="btn btn-sm btn-primary" data-action="skin-search">Hledat</button>
          </div>
          <div id="skinResults" class="skin-results"></div>
        </div>
      </div>
      <div class="field"><label>Popis (volitelné)</label><textarea class="input" id="pf_description" rows="2" placeholder="Krátký popis / instrukce pro diváka">${esc(p.description || "")}</textarea></div>
      <div class="field-row">
        <div class="field"><label>Cena (body)</label><input class="input" id="pf_cost" type="number" min="0" value="${p.cost_points}" required></div>
        <div class="field"><label>Sklad (−1 = ∞)</label><input class="input" id="pf_stock" type="number" value="${p.stock}" required></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Kategorie</label><input class="input" id="pf_category" value="${esc(p.category)}" placeholder="např. Discord"></div>
        <div class="field"><label>Typ</label><select class="select" id="pf_type">${types}</select></div>
      </div>
      <div class="field"><label>🎁 Perioda giveaway (hlavně pro Tombolu, volitelné)</label><select class="select" id="pf_period">${periods}</select>
        <span class="faint" style="font-size:11px">Štítek pro přehled — Denní/Týdenní/Měsíční/Roční/Random. Odměnu neukončuje (na to je „K dispozici do”).</span>
      </div>
      <div class="field"><label>🎟️ Tombola: max ticketů na osobu (0 = neomezeno)</label>
        <input class="input" id="pf_maxpct" type="number" min="0" max="10000" value="${p.max_per_person_pct || 0}">
        <span class="faint" style="font-size:11px">Jen pro tomboly. <b>Přímý počet</b> — např. <b>5</b> = každý max 5 ticketů na osobu. <b>0</b> = bez limitu.</span>
      </div>
      <div class="field"><label>⏳ K dispozici do (volitelné – časovač na kartě)</label>
        <input class="input" id="pf_ends" type="datetime-local" value="${isoToLocalInput(p.ends_at)}">
        <span class="faint" style="font-size:11px">Necháš prázdné = bez limitu. Po vypršení už nejde koupit a karta ukáže „UKONČENO”.</span>
      </div>
      <div class="field-row">
        <label class="check"><input type="checkbox" id="pf_subs" ${p.subs_only ? "checked" : ""}> Jen pro suby</label>
        <label class="check"><input type="checkbox" id="pf_vip" ${p.vip_only ? "checked" : ""}> Jen pro VIP</label>
        <label class="check"><input type="checkbox" id="pf_active" ${p.active ? "checked" : ""}> Aktivní</label>
        <label class="check" title="Připne odměnu na začátek shopu (nad nejnovější) + HOT odznak"><input type="checkbox" id="pf_hot" ${p.hot ? "checked" : ""}> 🔥 Zvýraznit (nahoru)</label>
      </div>
      <button class="btn btn-primary btn-block" type="submit">${p.id ? "Uložit změny" : "Vytvořit odměnu"}</button>
    </form></div>`);
}
async function saveProduct() {
  const id = $("#pf_id").value;
  const body = {
    name: $("#pf_name").value.trim(), image_url: $("#pf_image").value.trim(),
    cost_points: parseInt($("#pf_cost").value || "0", 10), category: $("#pf_category").value.trim(),
    type: $("#pf_type").value, period: $("#pf_period") ? $("#pf_period").value : "",
    subs_only: $("#pf_subs").checked, vip_only: $("#pf_vip").checked,
    stock: parseInt($("#pf_stock").value, 10), description: $("#pf_description").value.trim(),
    active: $("#pf_active").checked, hot: $("#pf_hot").checked, ends_at: localInputToIso($("#pf_ends").value),
    max_per_person_pct: parseInt(($("#pf_maxpct") && $("#pf_maxpct").value) || "0", 10),
  };
  if (!body.name) { toast("Zadej název.", "error"); return; }
  try {
    if (id) await api("/admin/products/" + id, { method: "PUT", body });
    else await api("/admin/products", { method: "POST", body });
    toast("Odměna uložena.", "success"); closeModal(); loadAdminStats(); adminProducts();
  } catch (e) { toast(e.message, "error"); }
}
async function lookupSkinImage() {
  const name = ($("#pf_name").value || "").trim();
  const info = $("#pf_skininfo");
  if (name.length < 3) { toast("Nejdřív napiš název skinu do pole Název (např. AWP | Asiimov (Field-Tested)).", "error"); return; }
  if (info) info.innerHTML = "⏳ Hledám na Steamu…";
  try {
    const r = await api("/admin/products/skin-lookup", { method: "POST", body: { name } });
    if (!r.ok || !r.image_url) {
      if (info) info.innerHTML = "⚠️ Přesná shoda nenalezena. Napiš název <b>přesně jako na Steam marketu</b> (vč. opotřebení, např. „AK-47 | Redline (Field-Tested)”). Když Steam zrovna omezuje dotazy, zkus to za chvíli znovu — nebo vlož URL obrázku ručně.";
      return;
    }
    $("#pf_image").value = r.image_url;
    if (info) info.innerHTML = `<img src="${esc(r.image_url)}" style="height:44px;vertical-align:middle;border-radius:6px;background:#0b0c16;padding:2px"> ✅ <b>${esc(r.name)}</b>${r.price ? ` · <span class="muted">tržní cena ${esc(r.price)}</span> <span style="opacity:.55">(orientačně — cenu zadej v sedlácích)</span>` : ""}`;
  } catch (e) {
    if (info) info.innerHTML = `⚠️ ${esc(e.message)}`;
    toast(e.message, "error");
  }
}
/* --- Vizuální pickr skinů (lokální katalog, našeptávač s náhledy) --- */
let _skinDebounce = null;
function toggleSkinPicker() {
  const p = $("#skinPicker"); if (!p) return;
  const show = p.style.display === "none";
  p.style.display = show ? "block" : "none";
  if (show) {
    const q = $("#skinQ");
    if (q) { q.value = ($("#pf_name").value || "").trim(); q.focus(); }
    if (($("#skinQ").value || "").length >= 2) searchSkins();
  }
}
function debouncedSkinSearch() { clearTimeout(_skinDebounce); _skinDebounce = setTimeout(searchSkins, 280); }
async function searchSkins() {
  const q = ($("#skinQ").value || "").trim();
  const box = $("#skinResults"); if (!box) return;
  if (q.length < 2) { box.innerHTML = `<div class="faint" style="padding:8px">Napiš aspoň 2 znaky…</div>`; return; }
  box.innerHTML = `<div class="faint" style="padding:8px">⏳ Hledám…</div>`;
  try {
    const r = await api("/admin/products/skin-search", { method: "POST", body: { query: q } });
    const res = r.results || [];
    if (!res.length) { box.innerHTML = `<div class="faint" style="padding:8px">Nic nenalezeno. Zkus jinak (např. jen „asiimov”, „karambit”).</div>`; return; }
    box.innerHTML = res.map((s) => `
      <button type="button" class="skin-card" data-action="skin-pick" data-name="${esc(s.name)}" data-image="${esc(s.image)}" title="${esc(s.name)}">
        <img src="${esc(s.image)}" loading="lazy" alt=""><span>${esc(s.name)}</span></button>`).join("");
  } catch (e) { box.innerHTML = `<div class="faint" style="padding:8px">⚠️ ${esc(e.message)}</div>`; }
}
function pickSkin(el) {
  const name = el.dataset.name, image = el.dataset.image;
  if ($("#pf_name")) $("#pf_name").value = name;
  if ($("#pf_image")) $("#pf_image").value = image;
  const info = $("#pf_skininfo");
  if (info) info.innerHTML = `<img src="${esc(image)}" style="height:40px;vertical-align:middle;border-radius:6px;background:#0b0c16;padding:2px"> ✅ Vybráno: <b>${esc(name)}</b>`;
  const p = $("#skinPicker"); if (p) p.style.display = "none";
}
function uploadImageClick() {
  const f = $("#pf_file");
  if (f) f.click();                       // otevře dialog výběru souboru
}
async function uploadImageFile() {
  const f = $("#pf_file");
  const file = f && f.files && f.files[0];
  if (!file) return;
  const info = $("#pf_skininfo");
  if (file.size > 6 * 1024 * 1024) { toast("Obrázek je příliš velký (max 6 MB).", "error"); f.value = ""; return; }
  if (info) info.innerHTML = "⏳ Nahrávám…";
  try {
    const dataUrl = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = () => rej(new Error("Nepodařilo se načíst soubor."));
      r.readAsDataURL(file);
    });
    const r = await api("/admin/products/upload-image", { method: "POST", body: { data: dataUrl } });
    if ($("#pf_image")) $("#pf_image").value = r.url;
    if (info) info.innerHTML = `<img src="${esc(r.url)}" style="height:44px;vertical-align:middle;border-radius:6px;background:#0b0c16;padding:2px"> ✅ Nahráno z PC`;
  } catch (e) {
    if (info) info.innerHTML = `⚠️ ${esc(e.message)}`;
    toast(e.message, "error");
  } finally {
    if (f) f.value = "";                  // reset – ať jde nahrát stejný soubor znovu
  }
}
async function deleteProduct(id) {
  if (!requireTypedConfirm("Smazání odměny je nevratné a může ovlivnit shop.", "SMAZAT")) return;
  try { await api("/admin/products/" + id, { method: "DELETE" }); toast("Smazáno.", "info"); loadAdminStats(); adminProducts(); }
  catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Uživatelé --- */
function userAdminTools(u, isAdmin) {
  if (!isAdmin) return "";
  return `<div class="user-admin-tools">
    ${riskBadge(u.risk)}
    <button class="mini-btn ${u.watchlisted ? "on" : ""}" data-action="user-watch" data-id="${u.id}" data-on="${u.watchlisted ? 1 : 0}" title="Watchlist">★</button>
    <button class="mini-btn ${u.admin_note ? "on" : ""}" data-action="user-note" data-id="${u.id}" data-name="${esc(u.username)}" data-note="${esc(u.admin_note || "")}" title="Admin poznámka">✎</button>
  </div>${u.admin_note ? `<div class="admin-note-preview">${esc(u.admin_note)}</div>` : ""}`;
}
async function banCluster(idsCsv, label) {
  const ids = (idsCsv || "").split(",").map(Number).filter(Boolean);
  if (!ids.length) return;
  if (!confirm(`Zbanovat celý cluster (${ids.length} účtů) — ${label}?\nStaff/admin se přeskočí. Hromadnou akci nelze snadno vrátit.`)) return;
  const reason = prompt("Důvod banu:", "alt farma (" + label + ")");
  if (reason === null) return;
  try {
    const r = await api("/admin/security/ban-cluster", { method: "POST", body: { user_ids: ids, reason } });
    toast(`Zbanováno ${r.banned} účtů${r.skipped ? ` · ${r.skipped} staff přeskočeno` : ""} 🔨`, "success");
    adminSecurity();
  } catch (e) { toast(e.message, "error"); }
}
async function adminUsers() {
  const box = $("#adminContent");
  try {
    const list = await api("/admin/users?q=" + encodeURIComponent(adminState.userQuery) + "&sort=" + adminState.userSort);
    const isAdmin = state.user && state.user.role === "admin";   // IP + změnu role vidí/dělá jen admin
    box.innerHTML = `
      <form class="toolbar" data-submit="user-search">
        <input class="input grow" id="userSearch" placeholder="🔍 Hledat podle jména…" value="${esc(adminState.userQuery)}">
        <button class="btn btn-ghost" type="submit">Hledat</button>
        <button type="button" class="btn btn-ghost${adminState.userSort === "points" ? " on" : ""}" data-action="user-sort" data-sort="points" title="Seřadit podle zůstatku bodů">Dle bodů</button>
        <button type="button" class="btn btn-ghost${adminState.userSort === "level" ? " on" : ""}" data-action="user-sort" data-sort="level" title="Seřadit podle úrovně (nafarmeno XP) – kdo má nejvyšší level">Dle úrovně ⭐</button>
        <a class="btn btn-ghost" href="/api/admin/export/users.csv" title="Export všech uživatelů: zůstatek, utraceno, nasbíráno, registrace">📥 CSV</a>
      </form>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Uživatel</th><th>${isAdmin ? "Kick / IP" : "Kick"}</th><th>Role</th><th title="Úroveň z celkem nafarmeného (earned_total). Gambling se nepočítá, placené/gift suby jen z 50 %.">Úroveň</th><th>Body</th><th>Upravit body</th><th>Stav</th></tr></thead><tbody>
      ${list.map((u) => `<tr>
        <td><div style="display:flex;align-items:center;gap:9px">${avatarHTML(u.username, u.avatar_url)}<b>${esc(u.username)}</b></div>${userAdminTools(u, isAdmin)}</td>
        <td class="faint" style="font-size:12.5px">${u.kick_username ? "🟢 " + esc(u.kick_username) : (isAdmin ? esc(u.email || "—") : "—")}${isAdmin ? "<br>" + (u.last_ip ? `<span class="code-pill">${esc(u.last_ip)}</span>${u.ip_count > 1 ? ` <span class="faint">(${u.ip_count} IP)</span>` : ""}` : "<span class='faint'>bez IP</span>") : ""}</td>
        <td>${isAdmin ? `<select class="select" style="width:128px;padding:6px 8px" data-action="user-role" data-id="${u.id}">
          ${["user", "sub", "vip", "mod", "broadcaster", "admin"].map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${r}</option>`).join("")}
        </select>
        <div style="display:flex;gap:6px;margin-top:7px;flex-wrap:wrap">
          ${[["is_sub", "SUB"], ["is_vip", "VIP"], ["is_og", "OG"]].map(([key, lbl]) => `<button type="button" data-action="user-flag-toggle" data-id="${u.id}" data-flag="${key}" class="flag-chip${u[key] ? " on" : ""}" title="Odznak ${lbl} – klikni pro zapnutí/vypnutí">${lbl}</button>`).join("")}
        </div>` : roleBadge(u.role) + " " + subVipBadges(u)}</td>
        <td title="Nafarmeno celkem (XP). Do dalšího levelu chybí ${Number(Math.max(0, (u.level_span || 0) - (u.level_into || 0))).toLocaleString("cs-CZ")} XP."><b style="color:var(--accent);font-size:13px;white-space:nowrap">⭐ ${u.level || 1}</b><br><span class="faint" style="font-size:11px;white-space:nowrap">${Number(u.earned_total || 0).toLocaleString("cs-CZ")} XP</span></td>
        <td><b style="color:var(--kick)">${fmtPts(u.points)}</b></td>
        <td><div style="display:flex;gap:6px;align-items:center">
          <input class="input" style="width:84px;padding:6px 8px" type="number" id="pts_${u.id}" placeholder="±body">
          <button class="btn btn-success btn-sm" data-action="user-points" data-id="${u.id}" data-sign="1" data-name="${esc(u.username)}">＋</button>
          <button class="btn btn-danger btn-sm" data-action="user-points" data-id="${u.id}" data-sign="-1" data-name="${esc(u.username)}">－</button>
        </div></td>
        <td><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${u.timeout_until ? `<span style="background:#3a2e0a;color:#ffd25a;border:1px solid #6b5512;border-radius:999px;padding:2px 7px;font-size:11px;font-weight:800;white-space:nowrap" title="V timeoutu do ${esc(String(u.timeout_until).slice(0, 16).replace("T", " "))}">⏳ TIMEOUT</span>` : ""}
          ${u.role === "admin" ? `<span class="faint">—</span>` : u.banned
            ? `<button class="btn btn-ghost btn-sm" data-action="unban-user" data-id="${u.id}">Odbanovat</button>`
            : `<button class="btn btn-danger btn-sm" data-action="ban-user" data-id="${u.id}">Ban</button>`}
          <button class="btn btn-ghost btn-sm" data-action="user-ticket" data-name="${esc(u.kick_username || u.username)}" title="Vytvořit ticket pro ${esc(u.username)}">🎫</button>
          <select class="select" style="width:120px;padding:6px 8px" data-action="user-gamble" data-id="${u.id}" data-name="${esc(u.username)}" title="Zablokovat/odemknout sázení (self-exclusion)">
            <option value="">🔒 Sázky…</option>
            <option value="1d">Blok 1 den</option>
            <option value="7d">Blok 7 dní</option>
            <option value="30d">Blok 30 dní</option>
            <option value="perm">Blok napořád</option>
            <option value="off">Odemknout</option>
          </select>
          <select class="select" style="width:130px;padding:6px 8px" data-action="user-timeout" data-id="${u.id}" data-name="${esc(u.username)}" title="Timeout: dočasně zablokovat web i Kick chat">
            <option value="">⏳ Timeout…</option>
            <option value="5m">Timeout 5 min</option>
            <option value="15m">Timeout 15 min</option>
            <option value="1h">Timeout 1 h</option>
            <option value="6h">Timeout 6 h</option>
            <option value="24h">Timeout 24 h</option>
            <option value="7d">Timeout 7 dní</option>
            <option value="off">Zrušit timeout</option>
          </select>
        </div></td>
      </tr>`).join("")}
      </tbody></table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function setUserSort(sort) { adminState.userSort = (sort === "level") ? "level" : "points"; adminUsers(); }
async function setUserRole(id, role) {
  if (["admin", "broadcaster", "mod"].includes(role)
      && !requireTypedConfirm(`Chystáš se dát uživateli citlivou roli: ${role}.`, "ROLE")) {
    adminUsers();
    return;
  }
  try { await api(`/admin/users/${id}/role`, { method: "POST", body: { role } }); toast("Role změněna na " + role + ".", "success"); }
  catch (e) { toast(e.message, "error"); adminUsers(); }
}
async function setUserFlag(id, flag, on) {
  const lbl = { is_sub: "SUB", is_vip: "VIP", is_og: "OG" }[flag] || flag;
  try { await api(`/admin/users/${id}/flags`, { method: "POST", body: { [flag]: on } }); toast(`${lbl} ${on ? "zapnut" : "vypnut"}.`, "success"); }
  catch (e) { toast(e.message, "error"); adminUsers(); }
}
async function toggleUserFlag(el) {
  const id = parseInt(el.dataset.id, 10);
  const flag = el.dataset.flag;
  const on = !el.classList.contains("on");
  const lbl = { is_sub: "SUB", is_vip: "VIP", is_og: "OG" }[flag] || flag;
  el.classList.toggle("on", on);   // optimistické přepnutí, při chybě vrátíme
  try { await api(`/admin/users/${id}/flags`, { method: "POST", body: { [flag]: on } }); toast(`${lbl} ${on ? "zapnut" : "vypnut"}.`, "success"); }
  catch (e) { el.classList.toggle("on", !on); toast(e.message, "error"); }
}
function changeUserPoints(id, sign, name) {
  const inp = $("#pts_" + id); const v = Math.abs(parseInt(inp && inp.value || "0", 10));
  if (!v) { toast("Zadej počet bodů.", "error"); if (inp) inp.focus(); return; }
  const change = sign * v;
  openModal(`
    <h3 style="margin:0 0 4px">${change > 0 ? "➕ Přidat" : "➖ Odebrat"} ${fmtPts(v)}</h3>
    <p class="muted" style="margin:0 0 14px">uživateli <b>${esc(name || ("#" + id))}</b></p>
    <div class="field"><label>Důvod (uvidí se v audit logu) *</label>
      <input class="input" id="ptsReason" maxlength="120" autocomplete="off" placeholder="např. odměna za výhru / oprava chyby"></div>
    <div class="toolbar" style="margin-top:14px;justify-content:flex-end;gap:8px">
      <button class="btn btn-ghost" data-action="close-modal">Zrušit</button>
      <button class="btn ${change > 0 ? "btn-success" : "btn-danger"}" data-action="pts-confirm" data-id="${id}" data-change="${change}">✔ Potvrdit</button>
    </div>`);
  setTimeout(() => { const r = document.getElementById("ptsReason"); if (r) r.focus(); }, 50);
}
async function confirmUserPoints(el) {
  const id = parseInt(el.dataset.id, 10), change = parseInt(el.dataset.change, 10);
  const reason = (document.getElementById("ptsReason")?.value || "").trim();
  if (!reason) { toast("Uveď důvod úpravy (do audit logu).", "error"); document.getElementById("ptsReason")?.focus(); return; }
  if (Math.abs(change) >= 10000
      && !requireTypedConfirm(`Velká úprava: ${change > 0 ? "+" : ""}${change} sedláků.`, "BODY")) return;
  try {
    await api(`/admin/users/${id}/points`, { method: "POST", body: { change, reason } });
    closeModal();
    toast("Body upraveny ✓", "success"); loadAdminStats(); adminUsers();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Suby (přehled aktivních subů + expirace) --- */
async function toggleUserWatch(el) {
  const id = parseInt(el.dataset.id, 10);
  const on = el.dataset.on !== "1";
  try {
    await api(`/admin/users/${id}/admin-meta`, { method: "POST", body: { watchlisted: on } });
    toast(on ? "Přidáno na watchlist." : "Odebráno z watchlistu.", on ? "success" : "info");
    adminUsers();
  } catch (e) { toast(e.message, "error"); }
}
function openUserNote(el) {
  const id = parseInt(el.dataset.id, 10);
  openModal(`<div class="modal-body">
    <h2>Admin poznámka</h2>
    <p class="muted" style="margin:6px 0 12px">${esc(el.dataset.name || "")}</p>
    <textarea class="input" id="adminNoteText" rows="6" maxlength="1000">${esc(el.dataset.note || "")}</textarea>
    <button class="btn btn-primary btn-block" data-action="user-note-save" data-id="${id}" style="margin-top:12px">Uložit poznámku</button>
  </div>`);
}
async function saveUserNote(id) {
  try {
    await api(`/admin/users/${id}/admin-meta`, { method: "POST", body: { note: ($("#adminNoteText")?.value || "") } });
    toast("Poznámka uložena.", "success");
    closeModal();
    adminUsers();
  } catch (e) { toast(e.message, "error"); }
}
async function adminSubs() {
  const box = $("#adminContent");
  try {
    const data = await api("/admin/subs");
    const subs = data.subs || [];
    const srcLabel = (s) => {
      if (!s) return `<span class="faint">—</span>`;
      if (s.indexOf("(příjemce)") >= 0) return "🎁 gift";
      if (s.indexOf("resub") >= 0) return "🔁 resub";
      if (s.indexOf("Kick sub") >= 0) return "🟣 sub";
      return esc(s);
    };
    const dayWord = (n) => (n === 1 ? "den" : n >= 2 && n <= 4 ? "dny" : "dní");
    const expCell = (su) => {
      if (su.days_left == null) return `<span class="faint">ruční / bez data</span>`;
      const d = su.days_left;
      const color = d < 3 ? "#ff5b6e" : d < 7 ? "#ffb02e" : "#46d369";
      const n = Math.floor(d);
      const txt = d < 0 ? "vypršel" : d < 1 ? "dnes" : `za ${n} ${dayWord(n)}`;
      return `<b style="color:${color}">${txt}</b><div class="faint" style="font-size:11.5px">${newsDate(su.sub_expires_at, { day: "numeric", month: "numeric", year: "numeric" })}</div>`;
    };
    const soon = subs.filter((s) => s.days_left != null && s.days_left < 7).length;
    box.innerHTML = `
      <div class="toolbar" style="justify-content:space-between">
        <div>💜 <b>Aktivní suby: ${subs.length}</b>${soon ? ` <span class="faint">· ${soon} vyprší do 7 dní</span>` : ""}</div>
        <button class="btn btn-ghost btn-sm" data-action="subs-refresh">↻ Obnovit</button>
      </div>
      <p class="faint" style="margin:2px 0 12px">Řazeno podle toho, komu vyprší nejdřív. „Ruční / bez data” = zaškrtnuté adminem (nevyprší samo). Příjemci gift subů se logují od teď.</p>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th>Uživatel</th><th>Role</th><th>Vyprší</th><th>Jak získal</th><th>Od kdy</th></tr></thead>
        <tbody>
        ${subs.length ? subs.map((su) => `<tr>
          <td><div style="display:flex;align-items:center;gap:9px">${avatarHTML(su.username, su.avatar_url)}<b>${esc(su.username)}</b></div>${su.is_vip || su.is_og ? `<div style="margin-top:4px">${su.is_og ? `<span class="badge badge-role badge-admin">OG</span> ` : ""}${su.is_vip ? `<span class="badge badge-role badge-vip-role">VIP</span> ` : ""}</div>` : ""}</td>
          <td>${roleBadge(su.role)}</td>
          <td>${expCell(su)}</td>
          <td>${srcLabel(su.source)}</td>
          <td class="faint" style="font-size:12px">${su.since ? timeAgo(su.since) : "—"}</td>
        </tr>`).join("") : `<tr><td colspan="5" class="empty" style="padding:20px">Zatím žádní aktivní subové.</td></tr>`}
        </tbody>
      </table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* --- Admin: Objednávky --- */
async function adminOrders() {
  const box = $("#adminContent");
  try {
    const pq = adminState.orderProduct ? "&product_id=" + encodeURIComponent(adminState.orderProduct) : "";
    const [list, products] = await Promise.all([
      api("/admin/orders?status=" + adminState.orderFilter + pq),
      api("/admin/order-products"),
    ]);
    const filt = [["all", "Vše"], ["pending", "Čeká"], ["fulfilled", "Vyřízeno"]];
    box.innerHTML = `
      <div class="toolbar">${filt.map(([k, l]) => `<button class="chip ${adminState.orderFilter === k ? "active" : ""}" data-action="order-filter" data-f="${k}">${l}</button>`).join("")}
        <select class="select" data-action="order-product-filter" style="max-width:260px;padding:6px 8px" title="Filtrovat objednávky podle položky">
          <option value="">📦 Všechny položky</option>
          ${products.map((p) => `<option value="${p.id}" ${String(adminState.orderProduct) === String(p.id) ? "selected" : ""}>${esc(p.name)} (${p.count})</option>`).join("")}
        </select>
        <button class="btn btn-success btn-sm" style="margin-left:auto" data-action="order-manual-add" title="Ručně přidat objednávku/ticket (např. kompenzace za bug)">➕ Přidat ticket</button>
        <button class="btn btn-primary btn-sm" data-action="orders-fulfill-all" title="Označit všechny ČEKAJÍCÍ (dle vybrané položky) jako vyřízené">✓ Vše vyřízeno</button>
        <a class="btn btn-ghost btn-sm" href="/api/admin/export/orders.csv?status=${encodeURIComponent(adminState.orderFilter)}${pq}">📥 Export CSV</a>
        <button class="btn btn-danger btn-sm" data-action="orders-clear-fulfilled" title="Smazat všechny vyřízené objednávky">🗑 Smazat vyřízené</button>
      </div>
      <div class="faint" style="font-size:12px;margin:2px 0 10px">🧹 Vyřízené objednávky starší <b>30 dní</b> se mažou samy (ať tabulka nebobtná). Body zůstávají v historii.</div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>#</th><th>Uživatel</th><th>Odměna</th><th>Body</th><th>Stav</th><th>Kdy</th><th></th></tr></thead><tbody>
      ${list.length ? list.map((o) => `<tr>
        <td class="faint">${o.id}</td>
        <td>${esc(o.username)}${o.steam_trade_url
          ? `<div style="font-size:11.5px;margin-top:3px;line-height:1.4"><a href="${esc(o.steam_trade_url)}" target="_blank" rel="noopener">🎁 trade link</a> · <a href="#" data-action="copy-trade" data-url="${esc(o.steam_trade_url)}">kopírovat</a></div>`
          : `<div style="font-size:11.5px;margin-top:3px;color:#e07a7a">⚠ bez trade linku</div>`}</td>
        <td>${esc(o.product_name)}</td><td>${fmtPts(o.points_spent)}</td>
        <td>${o.status === "fulfilled" ? `<span class="tag-done">✓ Vyřízeno</span>` : `<span class="tag-pending">⏳ Čeká</span>`}</td>
        <td class="faint">${timeAgo(o.created_at)}</td>
        <td><div class="tbl-actions" style="justify-content:flex-end">
          ${o.status === "pending" ? `<button class="btn btn-success btn-sm" data-action="order-fulfill" data-id="${o.id}">Označit vyřízeno</button>` : ""}
          <button class="btn btn-danger btn-sm" data-action="order-delete" data-id="${o.id}" title="Smazat objednávku">✕</button>
        </div></td>
      </tr>`).join("") : `<tr><td colspan="7"><div class="empty">Žádné objednávky.</div></td></tr>`}
      </tbody></table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function fulfillOrder(id) {
  try { await api(`/admin/orders/${id}/status`, { method: "POST", body: { status: "fulfilled" } }); toast("Objednávka vyřízena.", "success"); loadAdminStats(); adminOrders(); }
  catch (e) { toast(e.message, "error"); }
}
async function deleteOrder(id) {
  if (!confirm("Smazat tuhle objednávku z přehledu? (body se nevrací)")) return;
  try { await api(`/admin/orders/${id}`, { method: "DELETE" }); toast("Objednávka smazána.", "info"); loadAdminStats(); adminOrders(); }
  catch (e) { toast(e.message, "error"); }
}
async function clearFulfilledOrders() {
  if (!requireTypedConfirm("Smažeš všechny vyřízené objednávky z historie. Body se nevrací.", "SMAZAT")) return;
  try { const r = await api("/admin/orders/clear-fulfilled", { method: "POST" }); toast(`Smazáno ${r.deleted} vyřízených objednávek.`, "success"); loadAdminStats(); adminOrders(); }
  catch (e) { toast(e.message, "error"); }
}
async function fulfillAllOrders() {
  const pq = adminState.orderProduct ? "?product_id=" + encodeURIComponent(adminState.orderProduct) : "";
  const sel = document.querySelector('[data-action="order-product-filter"]');
  const prodLabel = (adminState.orderProduct && sel && sel.options[sel.selectedIndex]) ? sel.options[sel.selectedIndex].text : null;
  const msg = prodLabel
    ? `Označit VŠECHNY čekající tickety pro „${prodLabel}” jako vyřízené?`
    : "Označit VŠECHNY čekající objednávky (všechny položky) jako vyřízené?";
  if (!confirm(msg)) return;
  try {
    const r = await api("/admin/orders/fulfill-all" + pq, { method: "POST" });
    toast(`Hotovo — ${r.fulfilled} ticketů označeno jako vyřízené. ✅`, "success");
    loadAdminStats(); adminOrders();
  } catch (e) { toast(e.message, "error"); }
}
function openManualOrder(prefillUser) {
  openModal(`
    <style>
      #moUserHits { margin-top:4px; max-height:228px; overflow:auto; }
      #moUserHits .ac-hit { display:flex; justify-content:space-between; gap:8px; padding:7px 9px; border-radius:8px; cursor:pointer; }
      #moUserHits .ac-hit:hover { background:rgba(255,255,255,.07); }
    </style>
    <h3 style="margin:0 0 10px">🎫 Přidat ticket</h3>
    <div class="toolbar" style="gap:6px;margin-bottom:12px">
      <button class="chip active" id="moTabOne" data-action="mo-mode" data-mode="one">Jeden</button>
      <button class="chip" id="moTabBulk" data-action="mo-mode" data-mode="bulk">Hromadně</button>
    </div>
    <div id="moOne">
      <p class="muted" style="margin:0 0 12px;font-size:12.5px">Objednávka k vyřízení (kompenzace za bug). <b>Neúčtuje body.</b></p>
      <div class="field"><label>Uživatel (nick) *</label>
        <input class="input" id="moUser" maxlength="64" autocomplete="off" placeholder="začni psát nick…" value="${esc(prefillUser || "")}">
        <div id="moUserHits"></div></div>
      <div class="field"><label>Odměna / důvod *</label>
        <input class="input" id="moProduct" list="moProdList" maxlength="120" autocomplete="off" placeholder="vyber odměnu ze seznamu nebo napiš důvod">
        <datalist id="moProdList"></datalist></div>
      <div class="field"><label>Počet (kolik ticketů vytvořit)</label>
        <input class="input" id="moCount" type="number" min="1" max="50" value="1"></div>
      <div class="field"><label>Poznámka do audit logu (volitelně)</label>
        <input class="input" id="moNote" maxlength="200" autocomplete="off" placeholder="interní poznámka"></div>
      <div class="toolbar" style="margin-top:8px;justify-content:flex-end;gap:8px">
        <button class="btn btn-ghost" data-action="close-modal">Zrušit</button>
        <button class="btn btn-success" data-action="order-manual-submit">✔ Vytvořit ticket</button>
      </div>
    </div>
    <div id="moBulk" style="display:none">
      <p class="muted" style="margin:0 0 8px;font-size:12.5px">Jeden řádek = <b>nick | odměna</b> (volitelně <b>| počet</b>, výchozí 1). Vytvoří se všechny naráz. <b>Neúčtuje body.</b></p>
      <textarea class="input" id="moBulkText" rows="8" style="font-family:ui-monospace,monospace;font-size:13px;line-height:1.5" placeholder="Ksiltovka | ★ Navaja Knife&#10;warpyxxx | ★ Navaja Knife | 2&#10;pata677 | VIP na měsíc"></textarea>
      <div id="moBulkResult"></div>
      <div class="toolbar" style="margin-top:10px;justify-content:flex-end;gap:8px">
        <button class="btn btn-ghost" data-action="close-modal">Zrušit</button>
        <button class="btn btn-success" data-action="order-bulk-submit">✔ Vytvořit všechny</button>
      </div>
    </div>`);
  moLoadProducts();
  moWireUserAC();
  setTimeout(() => {
    const u = document.getElementById("moUser");
    if (prefillUser) document.getElementById("moProduct")?.focus();
    else if (u) u.focus();
  }, 60);
}
async function moLoadProducts() {
  try {
    const prods = (adminState.products && adminState.products.length) ? adminState.products : await api("/admin/products");
    adminState.products = prods;
    const dl = document.getElementById("moProdList");
    if (dl) dl.innerHTML = (prods || []).map((p) => `<option value="${esc(p.name)}">`).join("");
  } catch (e) {}
}
let _moAcTimer = null, _moAcSeq = 0;
function moWireUserAC() {
  const inp = document.getElementById("moUser"), hits = document.getElementById("moUserHits");
  if (!inp || !hits) return;
  inp.addEventListener("input", () => {
    const q = inp.value.trim();
    clearTimeout(_moAcTimer);
    if (q.length < 2) { hits.innerHTML = ""; return; }
    const seq = ++_moAcSeq;
    _moAcTimer = setTimeout(async () => {
      try {
        const list = await api("/admin/users?q=" + encodeURIComponent(q));
        if (seq !== _moAcSeq) return;   // zahoď zastaralou odpověď
        hits.innerHTML = (list || []).slice(0, 6).map((u) =>
          `<div class="ac-hit" data-action="mo-pick-user" data-nick="${esc(u.kick_username || u.username)}">
            <span><b>${esc(u.username)}</b>${u.kick_username ? ` <span class="faint">@${esc(u.kick_username)}</span>` : ""}</span>
            <span class="faint">${fmtPts(u.points)}${u.banned ? " · 🔴" : ""}</span>
          </div>`).join("") || `<div class="faint" style="padding:7px 9px">nic nenalezeno</div>`;
      } catch (e) {}
    }, 220);
  });
}
function moPickUser(el) {
  const inp = document.getElementById("moUser"); if (inp) inp.value = el.dataset.nick || "";
  const hits = document.getElementById("moUserHits"); if (hits) hits.innerHTML = "";
  document.getElementById("moProduct")?.focus();
}
function moMode(mode) {
  document.getElementById("moTabOne")?.classList.toggle("active", mode === "one");
  document.getElementById("moTabBulk")?.classList.toggle("active", mode === "bulk");
  const one = document.getElementById("moOne"), bulk = document.getElementById("moBulk");
  if (one) one.style.display = mode === "one" ? "" : "none";
  if (bulk) bulk.style.display = mode === "bulk" ? "" : "none";
}
async function submitBulkOrders() {
  const txt = (document.getElementById("moBulkText")?.value || "").trim();
  if (!txt) { toast("Vlož aspoň jeden řádek.", "error"); return; }
  const items = txt.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const p = l.split("|").map((s) => s.trim());
    return { username: p[0] || "", product_name: p[1] || "", count: Math.max(1, parseInt(p[2], 10) || 1) };
  });
  if (!items.length) { toast("Žádné řádky.", "error"); return; }
  try {
    const r = await api("/admin/orders/bulk", { method: "POST", body: { items } });
    const res = document.getElementById("moBulkResult");
    if (r.error_count) {
      if (res) res.innerHTML = `<div style="margin-top:10px;font-size:12.5px">✅ ${r.created_count} vytvořeno · ⚠️ ${r.error_count} chyb:<br>`
        + r.errors.map((e) => `<span style="color:#e07a7a">řádek ${e.line} (${esc(e.username || "?")}): ${esc(e.error)}</span>`).join("<br>") + `</div>`;
      toast(`${r.created_count} vytvořeno, ${r.error_count} chyb – oprav červené řádky.`, "info");
      loadAdminStats(); adminOrders();
    } else {
      closeModal();
      toast(`✅ Vytvořeno ${r.created_count} ticketů.`, "success");
      loadAdminStats(); adminOrders();
    }
  } catch (e) { toast(e.message, "error"); }
}
async function submitManualOrder() {
  const username = (document.getElementById("moUser")?.value || "").trim();
  const product_name = (document.getElementById("moProduct")?.value || "").trim();
  const count = Math.max(1, parseInt(document.getElementById("moCount")?.value, 10) || 1);
  const note = (document.getElementById("moNote")?.value || "").trim();
  if (!username) { toast("Zadej uživatele (nick).", "error"); document.getElementById("moUser")?.focus(); return; }
  if (!product_name) { toast("Zadej odměnu/důvod.", "error"); document.getElementById("moProduct")?.focus(); return; }
  try {
    const r = await api("/admin/orders", { method: "POST", body: { username, product_name, count, note } });
    closeModal();
    toast(`✅ Vytvořeno ${r.count}× „${product_name}” pro ${r.username} (čeká na vyřízení).`, "success");
    loadAdminStats(); adminOrders();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Hry (piškvorky) – moderace --- */
async function adminGames() {
  const box = $("#adminContent");
  let active = [], history = [];
  try { [active, history] = await Promise.all([api("/admin/games"), api("/admin/games/history")]); }
  catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const activeRows = active.length ? active.map((g) => {
    const st = g.status === "open" ? "🟢 čeká na soupeře" : "⚔️ hraje se";
    const who = g.status === "open" ? esc(g.p1) : `${esc(g.p1)} <span class="faint">vs</span> ${esc(g.p2)}`;
    return `<div class="lb-row">
      <span class="lb-rank" style="flex:0 0 44px;width:44px">#${g.id}</span>
      <div style="min-width:0;flex:1">
        <div style="font-weight:800">${who}</div>
        <div class="faint" style="font-size:12.5px">${st} · bank <b>${fmtPts(g.pot)}</b> · ${g.move_count} tahů · ${timeAgo(g.created_at)}</div>
      </div>
      <button class="btn btn-danger btn-sm" data-action="game-admin-cancel" data-id="${g.id}" style="margin-left:auto">Ukončit</button>
    </div>`;
  }).join("") : `<div class="empty"><div class="big">🎮</div>Žádné rozehrané hry právě teď.</div>`;
  const histRows = history.length ? history.map((h) => {
    const winTxt = h.status === "cancelled" ? `<span class="faint">zrušeno / refund</span>`
      : (h.winner === "remíza" ? `🤝 remíza` : `🏆 <b style="color:var(--accent-2)">${esc(h.winner || "?")}</b>`);
    const right = h.refundable
      ? `<button class="btn btn-ghost btn-sm" data-action="game-refund" data-kind="${h.duel ? "duel" : "gomoku"}" data-id="${h.id}" style="margin-left:auto" title="Vrátit oběma vklad + stornovat výhru">↩️ Refund</button>`
      : `<span class="faint" style="margin-left:auto;font-size:12px">${h.status === "cancelled" ? "vráceno" : "—"}</span>`;
    return `<div class="lb-row">
      <span class="lb-rank" style="flex:0 0 60px;width:60px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(h.kind)}">${esc(h.kind)}</span>
      <div style="min-width:0;flex:1">
        <div style="font-weight:700">${esc(h.p1)} <span class="faint">vs</span> ${esc(h.p2)} — ${winTxt}</div>
        <div class="faint" style="font-size:12.5px">#${h.id} · bank <b>${fmtPts(h.bank)}</b> · ${timeAgo(h.when)}</div>
      </div>
      ${right}
    </div>`;
  }).join("") : `<div class="empty">Zatím žádná historie.</div>`;
  box.innerHTML = `<div class="section-title" style="margin:0 0 8px">💣 Mines — historie & house</div>
    <div id="minesHistBox">${skeletonCards(1)}</div>
    <div id="minesBanBox" style="margin-top:22px">${skeletonCards(1)}</div>
    <div class="row-between" style="margin:26px 0 6px">
      <div class="section-title" style="margin:0">🎮 Probíhající hry (${active.length})</div>
      <button class="btn btn-ghost btn-sm" data-action="games-refresh">🔄 Obnovit</button>
    </div>
    <p class="muted" style="font-size:13px;margin-bottom:14px">Ukončení vrátí oběma hráčům vklad a hru zruší.</p>
    <div class="lb-list">${activeRows}</div>
    <div class="section-title" style="margin:24px 0 6px">📜 Historie her (gomoku + duely)</div>
    <p class="muted" style="font-size:13px;margin-bottom:14px">Kdo s kým hrál a kdo vyhrál. <b>↩️ Refund</b> u dohrané hry vrátí oběma vklad a stornuje výhru — třeba když to rozbil deploy/bug. (Vítěz může jít do mínusu, když už výhru utratil.)</p>
    <div class="lb-list">${histRows}</div>`;
  loadMinesHistory();
  loadMinesBans();
}
async function loadMinesBans() {
  const box = document.getElementById("minesBanBox"); if (!box) return;
  try {
    const d = await api("/admin/mines-bans");
    const rows = (d.banned || []).length
      ? d.banned.map((b) => `<div class="lb-row" style="padding:7px 0">
          <span class="hof-name">${uLink(b.username)}</span><span class="faint" style="font-size:12px">${b.expires_at ? `do ${new Date(b.expires_at).toLocaleString("cs-CZ")}` : "trvalý"}</span>
          <button class="btn btn-ghost btn-sm" data-action="mines-unban" data-username="${esc(b.username)}" style="margin-left:auto">✅ Odbanit</button>
        </div>`).join("")
      : `<div class="faint" style="font-size:13px">Nikdo nemá zákaz Mines.</div>`;
    box.innerHTML = `<div class="panel">
      <div class="section-title" style="margin:0 0 4px">🚫 Mines — zákaz hraní</div>
      <p class="muted" style="font-size:12.5px;margin:0 0 10px">Zabanovaný nemůže spustit Mines (dostane 403). Zbytek webu — shop, ostatní hry, predikce — mu funguje dál.</p>
      <div class="toolbar" style="margin-bottom:10px">
        <input id="minesBanNick" class="input input-sm" placeholder="Kick nick" style="max-width:200px">
        <button class="btn btn-danger btn-sm" data-action="mines-ban-add">🚫 Zabanovat na Mines</button>
      </div>
      <div class="lb-list">${rows}</div>
    </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function minesBanAdd() {
  const inp = document.getElementById("minesBanNick");
  const nick = ((inp && inp.value) || "").trim();
  if (!nick) { toast("Zadej nick.", "error"); if (inp) inp.focus(); return; }
  try {
    const r = await api("/admin/mines-ban", { method: "POST", body: { username: nick, banned: true } });
    if (r.ok) toast(`${r.username} zabanován na Mines. 🚫`, "success");
    loadMinesBans();
  } catch (e) { toast(e.message, "error"); }
}
async function minesUnban(username) {
  try {
    const r = await api("/admin/mines-ban", { method: "POST", body: { username, banned: false } });
    if (r.ok) toast(`${r.username} odbanován z Mines. ✅`, "success");
    loadMinesBans();
  } catch (e) { toast(e.message, "error"); }
}
let _minesHistQ = "";
async function loadMinesHistory(q) {
  if (q !== undefined) _minesHistQ = q;
  const box = document.getElementById("minesHistBox"); if (!box) return;
  try {
    const d = await api("/admin/mines-history?limit=60&q=" + encodeURIComponent(_minesHistQ));
    const s = d.stats, col = (n) => n > 0 ? "#46d369" : n < 0 ? "#ff7a7a" : "var(--text-dim)";
    const sign = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + fmtPts(Math.abs(n));
    const plr = (p) => `<div class="lb-row" style="padding:6px 0"><span class="hof-name">${uLink(p.username)}</span><span class="faint" style="font-size:12px">${p.games}× · bust ${p.bust_rate}%</span><span class="hof-metric" style="color:${col(p.net)}">${sign(p.net)}</span></div>`;
    box.innerHTML = `
      <div class="stat-grid" style="margin-bottom:14px">
        ${statBox(s.games, "Her celkem")}
        ${statBox(s.players, "Hráčů")}
        ${statBox(fmtPts(s.wagered), "Vsazeno")}
        ${statBox(("+" + fmtPts(s.house_net)), "House zisk", s.house_net >= 0 ? "accent" : "warn")}
      </div>
      <div class="hof-grid" style="margin-bottom:16px">
        <div class="panel hof-card"><div class="hof-head">🤑 Nejvíc vydělali</div><div class="hof-list">${d.winners.length ? d.winners.map(plr).join("") : `<div class="faint" style="font-size:13px">Nikdo v plusu.</div>`}</div></div>
        <div class="panel hof-card"><div class="hof-head">😭 Nejvíc prohráli</div><div class="hof-list">${d.losers.length ? d.losers.map(plr).join("") : `<div class="faint" style="font-size:13px">Nikdo v mínusu.</div>`}</div></div>
      </div>
      <div class="toolbar" style="margin-bottom:8px">
        <input id="minesHistQ" class="input input-sm" placeholder="Filtr: nick" value="${esc(_minesHistQ)}" style="max-width:180px">
        <button class="btn btn-sm" data-action="mines-hist-filter">Filtrovat</button>
        ${_minesHistQ ? `<button class="btn btn-ghost btn-sm" data-action="mines-hist-reset">Reset</button>` : ""}
        <span class="faint" style="margin-left:auto;font-size:12px">${d.feed.length} posledních her</span>
      </div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>#</th><th>Hráč</th><th>Sázka</th><th>💣</th><th>Odkryto</th><th>Výsledek</th><th>Net</th><th>Kdy</th></tr></thead><tbody>
        ${d.feed.length ? d.feed.map((g) => `<tr>
          <td class="faint">${g.id}</td>
          <td>${uLink(g.username)}</td>
          <td>${fmtPts(g.bet)}</td>
          <td>${g.mines}</td>
          <td class="faint">${g.safe}</td>
          <td>${g.status === "cashed" ? `<span class="tag-done">💰 cashout</span>` : `<span style="color:#ff7a7a">💥 bust</span>`}</td>
          <td><b style="color:${col(g.net)}">${sign(g.net)}</b></td>
          <td class="faint">${timeAgo(g.created_at)}</td>
        </tr>`).join("") : `<tr><td colspan="8"><div class="empty">Žádné hry.</div></td></tr>`}
      </tbody></table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function cancelGameAdmin(id) {
  if (!confirm("Ukončit tuhle hru? Oběma hráčům se vrátí vklad.")) return;
  try {
    const r = await api(`/admin/games/${id}/cancel`, { method: "POST" });
    if (r.ok) toast("Hra ukončena, vklady vráceny. ✅", "success");
    else toast(r.error || "Hru nešlo ukončit.", "error");
    adminGames();
  } catch (e) { toast(e.message, "error"); }
}
async function refundGame(kind, id) {
  if (!requireTypedConfirm("Refund hry: oběma hráčům se vrátí vklad a vítězi se stornuje výhra (může ho dát do mínusu, když už utratil).", "REFUND")) return;
  const path = kind === "duel" ? `/admin/games/duels/${id}/refund` : `/admin/games/${id}/refund`;
  try {
    const r = await api(path, { method: "POST" });
    if (r.ok) toast("Refund hotový — vklady vráceny, výhra stornována. ✅", "success");
    else toast(r.error || "Refund se nepovedl.", "error");
    adminGames();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Tomboly --- */
async function adminRaffles() {
  const box = $("#adminContent");
  try {
    const list = await api("/admin/raffle/products");
    const ord = { daily: 1, weekly: 2, monthly: 3, yearly: 4, random: 5 };
    const sorted = [...list].sort((a, b) => (ord[a.period] || 9) - (ord[b.period] || 9));
    box.innerHTML = list.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(280px,1fr))">${sorted.map((p) => {
      const thumb = p.image_url
        ? `<div class="ex-emoji" style="background-image:url('${esc(p.image_url)}');background-size:cover;background-repeat:no-repeat;background-position:center"></div>`
        : `<div class="ex-emoji">🎟️</div>`;
      const pb = PERIOD_LABEL[p.period] ? `<span class="badge badge-cat" style="margin-left:6px;font-size:10px">${PERIOD_LABEL[p.period]}</span>` : "";
      return `
      <div class="card" style="padding:18px">
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px">${thumb}<div><div style="font-weight:700">${esc(p.name)}${pb}</div><div class="faint" style="font-size:13px">${fmtPts(p.cost_points)} / tiket</div></div></div>
        <div class="row-between" style="margin-bottom:6px"><span class="muted">Tiketů:</span><b>${p.tickets}</b></div>
        <div class="row-between" style="margin-bottom:12px"><span class="muted">Účastníků:</span><b>${p.participants}</b></div>
        ${p.winner ? `<div class="panel gold" style="margin-bottom:12px;padding:10px 12px"><div class="row-between" style="gap:8px;flex-wrap:wrap"><span>🏆 Výherce: <a class="prof-link" href="#/u/${encodeURIComponent(p.winner)}"><b>${esc(p.winner)}</b></a></span><span style="display:flex;gap:6px">${p.winner_id ? `<a class="btn btn-primary btn-sm" href="#/zpravy/${p.winner_id}" title="Napsat výherci zprávu">✉️ Napsat</a>` : ""}<button class="btn btn-ghost btn-sm" data-action="raffle-undo" data-id="${p.id}" title="Smazat výherce – účastníci zůstanou">↩️ Vrátit</button></span></div></div>` : ""}
        <button class="btn btn-primary btn-block" data-action="raffle-draw" data-id="${p.id}" ${p.tickets ? "" : "disabled"}>🎲 ${p.winner ? "Losovat znovu" : "Vylosovat výherce"}</button>
      </div>`;
    }).join("")}</div>` : `<div class="empty"><div class="big">🎟️</div>Žádné tomboly. Vytvoř odměnu typu „Tombola”.</div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
/* --- Tombola: losovací animace ve stylu CS „case opening" --- */
let _audioCtx = null;
function _audio() {
  try {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (_audioCtx.state === "suspended") _audioCtx.resume();
    return _audioCtx;
  } catch (e) { return null; }
}
function rouletteTick() {
  const ac = _audio(); if (!ac) return;
  try {
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = "square"; o.frequency.value = 1150 + Math.random() * 120;
    g.gain.setValueAtTime(0.05, ac.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + 0.05);
    o.connect(g); g.connect(ac.destination); o.start(); o.stop(ac.currentTime + 0.05);
  } catch (e) {}
}
function winFanfare() {
  const ac = _audio(); if (!ac) return;
  try {
    [523, 659, 784, 1047].forEach((hz, i) => {
      const o = ac.createOscillator(), g = ac.createGain(), st = ac.currentTime + i * 0.11;
      o.type = "triangle"; o.frequency.value = hz;
      g.gain.setValueAtTime(0.0001, st);
      g.gain.exponentialRampToValueAtTime(0.16, st + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, st + 0.38);
      o.connect(g); g.connect(ac.destination); o.start(st); o.stop(st + 0.4);
    });
  } catch (e) {}
}
function confettiBurst(host, n = 80) {
  if (!host) return;
  const colors = ["#ff9d2e", "#ffd34d", "#46e08a", "#4b69ff", "#d32ee6", "#ff4d5e", "#ffffff"];
  const layer = document.createElement("div"); layer.className = "confetti-layer";
  for (let i = 0; i < n; i++) {
    const c = document.createElement("i");
    c.style.left = Math.random() * 100 + "%";
    c.style.background = colors[i % colors.length];
    c.style.animationDelay = (Math.random() * 0.4).toFixed(2) + "s";
    c.style.animationDuration = (1.6 + Math.random() * 1.6).toFixed(2) + "s";
    if (Math.random() < 0.5) c.style.borderRadius = "50%";
    layer.appendChild(c);
  }
  host.appendChild(layer);
  setTimeout(() => layer.remove(), 4200);
}
function weightedPick(parts) {
  const total = parts.reduce((s, p) => s + (p.tickets || 1), 0);
  let x = Math.random() * total;
  for (const p of parts) { x -= (p.tickets || 1); if (x <= 0) return p; }
  return parts[parts.length - 1];
}
function caseCard(p, win) {
  return `<div class="case-card${win ? " win" : ""}">${avatarHTML(p.username, p.avatar_url, "cc-av")}<div class="cc-name">${esc(p.username)}</div></div>`;
}
function runRaffleAnimation(parts, winner) {
  const N = 56, winIdx = N - 8;                          // výherce blízko konce pásu
  const cards = [];
  for (let i = 0; i < N; i++) cards.push(i === winIdx ? winner : weightedPick(parts));
  openModal(`
    <div class="case-wrap">
      <div class="case-head"><span class="case-spin">🎰</span> <span id="caseTitle">Losování běží…</span></div>
      <div class="case-window"><div class="case-marker"></div><div class="case-reel" id="caseReel">${cards.map((c, i) => caseCard(c, i === winIdx)).join("")}</div></div>
      <div class="case-result" id="caseResult"></div>
    </div>`, "modal-raffle");

  const reel = document.getElementById("caseReel");
  if (!reel || reel.children.length < 2) { return finishRaffle(reel, reel && reel.children[winIdx], winner); }
  const win = reel.parentElement, els = reel.children;
  requestAnimationFrame(() => {
    const pitch = els[1].offsetLeft - els[0].offsetLeft;     // šířka karty vč. mezery
    const cardW = els[0].offsetWidth, winW = win.clientWidth, target = els[winIdx];
    const jitter = (Math.random() - 0.5) * cardW * 0.6;
    const endX = winW / 2 - (target.offsetLeft + cardW / 2) - jitter;
    const dur = 5200, ease = (t) => 1 - Math.pow(1 - t, 5);  // easeOutQuint
    let t0 = performance.now(), lastIdx = -1;
    function frame(now) {
      let t = (now - t0) / dur; if (t > 1) t = 1;
      const x = endX * ease(t);
      reel.style.transform = `translateX(${x}px)`;
      const idx = Math.round((winW / 2 - x) / pitch);
      if (idx !== lastIdx && t < 0.99) { lastIdx = idx; rouletteTick(); }
      if (t < 1) requestAnimationFrame(frame);
      else finishRaffle(reel, target, winner);
    }
    requestAnimationFrame(frame);
  });
}
function finishRaffle(reel, target, winner) {
  if (target) target.classList.add("landed");
  winFanfare();
  confettiBurst(document.querySelector(".modal-raffle"));
  const title = document.getElementById("caseTitle"); if (title) title.textContent = "🎉 Máme výherce!";
  const res = document.getElementById("caseResult");
  if (res) {
    res.innerHTML = `
      ${avatarHTML(winner.username, winner.avatar_url, "cr-avatar")}
      <div class="cr-name">🏆 ${esc(winner.username)}</div>
      <div class="cr-sub">Gratulujeme k výhře! 🎉</div>
      <button class="btn btn-primary" data-action="close-modal">Super!</button>`;
    res.classList.add("show");
  }
  toast(`Výherce vylosován: ${winner.username} 🎉`, "success");
}
async function drawRaffle(id) {
  if (!requireTypedConfirm("Spouštíš ostré losování tomboly. Výsledek se uloží do auditu.", "LOSOVAT")) return;
  let parts = [];
  try {
    const d = await api(`/shop/raffle/${id}/entries`);
    parts = (d.participants || []).map((p) => ({ username: p.username, avatar_url: p.avatar_url, tickets: p.tickets || 1 }));
  } catch (e) { /* fallback níže */ }
  try {
    const r = await api(`/admin/raffle/${id}/draw`, { method: "POST" });
    if (!parts.length) parts = [{ username: r.winner.username, avatar_url: r.winner.avatar_url, tickets: 1 }];
    runRaffleAnimation(parts, r.winner);
    adminRaffles();   // obnoví seznam pod modalem (výherce je už uložen v DB)
  } catch (e) { toast(e.message, "error"); }
}
async function undoRaffleDraw(id) {
  if (!confirm("Vrátit losování? Smaže se výherce, ale účastníci (tikety) zůstanou — můžeš pak losovat znovu.")) return;
  try {
    await api(`/admin/raffle/${id}/undo-draw`, { method: "POST" });
    toast("Losování vráceno — výherce smazán, účastníci zůstali. ↩️", "success");
    adminRaffles();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Predikce (CS2) --- */
async function adminPredictions() {
  const box = $("#adminContent");
  try {
    const list = await api("/predictions/admin/all");
    const form = `<div class="panel" style="margin-bottom:16px">
      <div class="section-title" style="margin-top:0">➕ Nová predikce</div>
      <form data-submit="pred-create">
        <div class="field"><label>Otázka</label><input class="input" id="predQ" placeholder="Vyhrajeme příští zápas?" maxlength="200" autocomplete="off"></div>
        <div class="field"><label>Možnosti — 2 až 4, oddělené čárkou</label><input class="input" id="predOpts" value="Ano, Ne" autocomplete="off"></div>
        <div class="field"><label>Auto-zavřít sázky za ⏱️</label><select class="input" id="predLock" style="max-width:240px">
          <option value="0">Ručně (zavřu sám)</option>
          <option value="60">1 minuta</option>
          <option value="120">2 minuty</option>
          <option value="180" selected>3 minuty</option>
          <option value="300">5 minut</option>
        </select><div class="form-hint" style="margin-top:5px">Po uplynutí se sázky samy zavřou, divákům běží odpočet.</div></div>
        <button class="btn btn-primary" type="submit">🎯 Vytvořit predikci</button>
      </form>
    </div>`;
    const rows = list.length ? list.map(adminPredRowHTML).join("") : `<div class="empty">Zatím žádné predikce.</div>`;
    box.innerHTML = form + rows;
    startPredPoll("admin");
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function adminPredRowHTML(p) {
  const active = p.status === "open" || p.status === "locked";
  const badge = p.status === "open" ? `<span class="badge badge-sub">Otevřeno</span>`
    : p.status === "locked" ? `<span class="badge badge-admin">Uzamčeno</span>`
    : p.status === "resolved" ? `<span class="tag-done">✓ Vyhodnoceno</span>` : `<span class="badge">Zrušeno</span>`;
  const opts = p.options.map((o) => `<span class="code-pill" data-opt="${o.id}">${esc(o.label)}: <b class="apred-pool">${fmtPts(o.pool)}</b> (<span class="apred-bettors">${o.bettors}</span>👤)${o.is_winner ? " 🏆" : ""}</span>`).join(" ");
  let controls = "";
  if (active) {
    const resolveBtns = p.options.map((o) => `<button class="btn btn-success btn-sm" data-action="pred-resolve" data-pid="${p.id}" data-oid="${o.id}">🏆 ${esc(o.label)}</button>`).join(" ");
    controls = `
      <div class="toolbar" style="margin-top:10px">
        ${p.status === "open"
          ? `<button class="btn btn-sm" data-action="pred-lock" data-pid="${p.id}">🔒 Zamknout sázky</button>`
          : `<button class="btn btn-sm" data-action="pred-unlock" data-pid="${p.id}">🔓 Odemknout</button>`}
        <button class="btn btn-danger btn-sm" data-action="pred-cancel" data-pid="${p.id}">❌ Zrušit (vrátit vklady)</button>
      </div>
      <div class="faint" style="font-size:12px;margin:10px 0 4px">Vyhodnotit — klikni na vítěznou možnost (vyplatí výhry):</div>
      <div class="toolbar">${resolveBtns}</div>`;
  } else if (p.status === "cancelled") {
    controls = `<div class="faint" style="margin-top:6px">Vklady vráceny.</div>`;
  } else {
    const w = p.options.find((o) => o.is_winner);
    const reBtns = p.options.map((o) =>
      `<button class="btn btn-sm ${o.is_winner ? "btn-success" : "btn-ghost"}" data-action="pred-reresolve" data-pid="${p.id}" data-oid="${o.id}"${o.is_winner ? " disabled" : ""}>${o.is_winner ? "🏆 " : "✏️ "}${esc(o.label)}</button>`).join(" ");
    controls = `
      <div class="faint" style="margin-top:6px">Vítěz: <b style="color:var(--accent-2)">${w ? esc(w.label) : "?"}</b></div>
      <div class="faint" style="font-size:12px;margin:10px 0 4px">Špatně vyhodnoceno? Klikni na správného vítěze — stornuje výplaty a vyplatí znovu:</div>
      <div class="toolbar">${reBtns}</div>`;
  }
  return `<div class="panel apred-card" data-pred="${p.id}" data-status="${p.status}" style="margin-bottom:12px">
    <div class="row-between"><b>🎯 ${esc(p.question)}</b>${badge}</div>
    <div style="margin:8px 0;display:flex;gap:6px;flex-wrap:wrap">${opts}</div>
    <div class="faint" style="font-size:12px">Celkový bank: <span class="apred-bank">${fmtPts(p.total_pool)}</span>${predBy(p)}</div>
    ${controls}
  </div>`;
}
async function createPrediction() {
  const q = ($("#predQ")?.value || "").trim();
  const options = ($("#predOpts")?.value || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (q.length < 3) { toast("Zadej otázku (aspoň 3 znaky).", "error"); return; }
  if (options.length < 2) { toast("Zadej aspoň 2 možnosti (oddělené čárkou).", "error"); return; }
  const lockSeconds = parseInt(($("#predLock") || {}).value || "180", 10) || 0;
  try {
    await api("/predictions", { method: "POST", body: { question: q, options, game: "CS2", lock_seconds: lockSeconds } });
    toast(lockSeconds ? `Predikce vytvořena 🎯 — sázky se zavřou za ${Math.round(lockSeconds / 60 * 10) / 10} min` : "Predikce vytvořena 🎯", "success");
    adminPredictions();
  } catch (e) { toast(e.message, "error"); }
}
async function predLock(pid) { try { await api(`/predictions/${pid}/lock`, { method: "POST" }); toast("Sázky uzamčeny 🔒", "info"); adminPredictions(); } catch (e) { toast(e.message, "error"); } }
async function predUnlock(pid) { try { await api(`/predictions/${pid}/unlock`, { method: "POST" }); toast("Odemčeno 🔓", "info"); adminPredictions(); } catch (e) { toast(e.message, "error"); } }
async function predResolve(pid, oid) {
  if (!confirm("Vyhodnotit predikci touhle možností? Výhry se hned vyplatí a nejde to vzít zpět.")) return;
  try { await api(`/predictions/${pid}/resolve`, { method: "POST", body: { option_id: oid } }); toast("Vyhodnoceno a vyplaceno 🎉", "success"); adminPredictions(); } catch (e) { toast(e.message, "error"); }
}
async function predReresolve(pid, oid) {
  if (!requireTypedConfirm("OPRAVA vítěze: stornuju špatné výplaty a vyplatím znovu na nového vítěze. Může někoho dát do mínusu (kdo už špatnou výhru utratil).", "OPRAVIT")) return;
  try { await api(`/predictions/${pid}/reresolve`, { method: "POST", body: { option_id: oid } }); toast("Vítěz opraven, výplaty přepočítané ✅", "success"); adminPredictions(); } catch (e) { toast(e.message, "error"); }
}
async function predCancel(pid) {
  if (!confirm("Zrušit predikci a vrátit všem vklady?")) return;
  try { const r = await api(`/predictions/${pid}/cancel`, { method: "POST" }); toast(`Zrušeno — vráceno ${r.refunded} sázek.`, "info"); adminPredictions(); } catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Kódy --- */
async function adminCodes() {
  const box = $("#adminContent");
  try {
    const [codes, products] = await Promise.all([api("/admin/codes"), adminState.products.length ? Promise.resolve(adminState.products) : api("/admin/products")]);
    adminState.products = products;
    const prodOpts = `<option value="">— bez odměny —</option>` + products.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    box.innerHTML = `
      <div class="panel" style="margin-bottom:18px">
        <div class="section-title">🎫 Vygenerovat kódy</div>
        <form class="form" data-submit="gen-code">
          <div class="field-row">
            <div class="field"><label>Hodnota v bodech</label><input class="input" id="cg_points" type="number" min="0" value="0"></div>
            <div class="field"><label>…nebo odměna</label><select class="select" id="cg_product">${prodOpts}</select></div>
          </div>
          <div class="field-row">
            <div class="field"><label>Max. použití</label><input class="input" id="cg_maxuses" type="number" min="1" value="1"></div>
            <div class="field"><label>Počet kódů</label><input class="input" id="cg_count" type="number" min="1" max="100" value="1"></div>
            <div class="field"><label>Platnost do (volitelné)</label><input class="input" id="cg_expires" type="date"></div>
          </div>
          <div class="field"><label>Vlastní kód (jen při 1 ks, volitelné)</label><input class="input" id="cg_code" placeholder="např. VANOCE2026" style="text-transform:uppercase"></div>
          <button class="btn btn-primary" type="submit">Vygenerovat</button>
        </form>
      </div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Kód</th><th>Hodnota</th><th>Použití</th><th>Platnost</th><th></th></tr></thead><tbody>
      ${codes.length ? codes.map((c) => `<tr>
        <td><span class="code-pill">${esc(c.code)}</span></td>
        <td>${c.points_value ? `+${fmtPts(c.points_value)}` : ""}${c.product_name ? `🎁 ${esc(c.product_name)}` : (c.points_value ? "" : "—")}</td>
        <td>${c.uses_count}/${c.max_uses}</td>
        <td class="faint">${c.expires_at ? new Date(c.expires_at).toLocaleDateString("cs-CZ") : "∞"}</td>
        <td><div class="tbl-actions"><button class="btn btn-ghost btn-sm" data-action="copy-code" data-code="${esc(c.code)}">📋</button><button class="btn btn-danger btn-sm" data-action="code-delete" data-id="${c.id}">🗑️</button></div></td>
      </tr>`).join("") : `<tr><td colspan="5"><div class="empty">Žádné kódy.</div></td></tr>`}
      </tbody></table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function genCodes() {
  const points = parseInt($("#cg_points").value || "0", 10);
  const pid = $("#cg_product").value ? parseInt($("#cg_product").value, 10) : null;
  const body = {
    points_value: points, product_id: pid, max_uses: parseInt($("#cg_maxuses").value || "1", 10),
    count: parseInt($("#cg_count").value || "1", 10),
    expires_at: $("#cg_expires").value ? new Date($("#cg_expires").value).toISOString() : null,
    code: $("#cg_code").value.trim() || null,
  };
  try {
    const r = await api("/admin/codes", { method: "POST", body });
    toast(`Vytvořeno ${r.created.length} kódů: ${r.created.map((c) => c.code).join(", ")}`, "success");
    loadAdminStats(); adminCodes();
  } catch (e) { toast(e.message, "error"); }
}
async function deleteCode(id) {
  if (!confirm("Smazat tento redeem kod?")) return;
  try { await api("/admin/codes/" + id, { method: "DELETE" }); toast("Kód smazán.", "info"); loadAdminStats(); adminCodes(); }
  catch (e) { toast(e.message, "error"); }
}

/* --- Admin: Bezpečnost / Anticheat --- */
function uaShort(ua) {
  if (!ua) return "—";
  if (/Mobi|Android|iPhone/i.test(ua)) return "📱 Mobil";
  if (/ukázka|seed/i.test(ua)) return "🧪 ukázka";
  if (/Edg/i.test(ua)) return "💻 Edge";
  if (/Chrome/i.test(ua)) return "💻 Chrome";
  if (/Firefox/i.test(ua)) return "💻 Firefox";
  if (/Safari/i.test(ua)) return "💻 Safari";
  return "💻 prohlížeč";
}

const AUDIT_LABELS = {
  "user.points": "💰 Body", "user.role": "🎖️ Role", "user.ban": "🔨 Ban", "user.unban": "✅ Odban",
  "product.create": "🎁 Odměna +", "product.update": "🎁 Odměna ✎", "product.delete": "🎁 Odměna ✕",
  "code.generate": "🎫 Kódy +", "code.delete": "🎫 Kód ✕", "order.status": "📦 Objednávka",
  "raffle.draw": "🎟️ Losování", "drop.create": "🎁 Drop +", "drop.end": "🎁 Drop konec",
  "rule.update": "⚙️ Pravidlo", "anticheat.block": "🛡️ AC blok",
  "ip.ban": "🚫 IP ban", "ip.unban": "✅ IP odban",
  "gift.approve": "🎁 Dar povolen", "gift.reject": "🎁 Dar zamítnut",
  "subgoal.update": "🟣 SUB cíl", "cgoal.update": "💬 Chat cíl",
  "modapp.accept": "🛡️ Mod přijat", "modapp.reject": "🛡️ Mod zamítnut", "modapp.toggle": "🛡️ Nábor",
};
function auditLabel(action) { return AUDIT_LABELS[action] || action || "?"; }
function auditTone(action) {
  if (["user.ban", "product.delete", "code.delete", "ip.ban", "anticheat.block"].includes(action)) return "danger";
  if (["user.role", "user.points", "raffle.draw", "drop.create", "drop.end", "rule.update"].includes(action)) return "warn";
  return "ok";
}
function auditTimeline(rows) {
  if (!rows.length) {
    return `<div class="empty">${adminState.auditAction || adminState.auditAdmin ? "Žádné záznamy neodpovídají filtru." : "Zatím žádné admin akce."}</div>`;
  }
  return `<div class="audit-timeline">${rows.map((a) => `<div class="audit-item ${auditTone(a.action)}">
    <div class="audit-dot"></div>
    <div class="audit-body">
      <div class="audit-top">
        <b>${esc(auditLabel(a.action))}</b>
        <span>${timeAgo(a.created_at)}</span>
      </div>
      <div class="audit-detail">${esc(a.details || a.target || "")}</div>
      <div class="audit-meta">
        <span>${esc(a.admin_name || "?")}</span>
        ${a.target ? `<span>${esc(a.target)}</span>` : ""}
        <span>${esc(a.ip || "?")}</span>
      </div>
    </div>
  </div>`).join("")}</div>`;
}

function acUserRow(u) {
  return `<div class="lb-row" style="padding:9px 12px">
    ${avatarHTML(u.username)}
    <span class="uname">${esc(u.username)}</span> ${roleBadge(u.role)}
    ${u.banned ? `<span class="badge badge-admin">BAN</span>` : ""}
    <span class="pts" style="margin-left:auto">${typeof u.points === "number" ? fmtPts(u.points) : ""}</span>
    ${u.banned
      ? `<button class="btn btn-ghost btn-sm" data-action="unban-user" data-id="${u.id}">Odbanovat</button>`
      : `<button class="btn btn-danger btn-sm" data-action="ban-user" data-id="${u.id}">Zabanovat</button>`}
  </div>`;
}

function ruleRowHTML(r) {
  const sev = { CRITICAL: "sev-crit", HIGH: "sev-high", MEDIUM: "sev-med", LOW: "sev-low" }[r.severity] || "sev-low";
  return `<div class="ac-rule ${r.enabled ? "" : "off"}">
    <div class="ac-rule-main">
      <div class="ac-rule-top"><b>${esc(r.label)}</b><span class="ac-sev ${sev}">${r.severity}</span>${r.enforced ? `<span class="ac-tag">aktivní</span>` : `<span class="ac-tag mon">monitoring</span>`}</div>
      <div class="muted" style="font-size:12.5px">${esc(r.desc)}</div>
      <div class="ac-prah">Práh: <b>${esc(r.prah)}</b></div>
    </div>
    <button class="ac-switch ${r.enabled ? "on" : ""}" data-action="rule-toggle" data-key="${r.key}" data-on="${r.enabled ? 1 : 0}" title="${r.enabled ? "Vypnout" : "Zapnout"}"><span class="knob"></span></button>
  </div>`;
}
async function toggleRule(key, enabled) {
  try {
    await api(`/admin/security/rules/${key}`, { method: "POST", body: { enabled } });
    adminSecurity();
  } catch (e) { toast(e.message, "error"); }
}
function giftRequestsHTML(giftReqs) {
  const grPending = (giftReqs && giftReqs.pending) || [], grRecent = (giftReqs && giftReqs.recent) || [];
  let giftReqHtml = `<div class="section-title" style="margin-top:6px">🎁 Žádosti o dar <span class="faint" style="font-size:12px;font-weight:400">— čekají na tvé schválení; body jsou u odesílatele zatím zablokované</span></div>`;
  if (!grPending.length) {
    giftReqHtml += `<div class="panel ok">✅ Žádné čekající žádosti o dar.</div>`;
  } else {
    giftReqHtml += grPending.map((g) => `
        <div class="panel ${g.shared ? "bad" : "gold"}" style="margin-bottom:10px">
          <div class="row-between" style="flex-wrap:wrap;gap:8px">
            <div><b>${uLink(g.from)}</b>${g.from_banned ? ` <span class="badge badge-admin">BAN</span>` : ""} <span style="opacity:.7">→</span> <b>${uLink(g.to)}</b>${g.to_banned ? ` <span class="badge badge-admin">BAN</span>` : ""}
              <span class="badge badge-vip" style="margin-left:6px">${fmtPts(g.amount)}</span></div>
            <span class="faint" style="font-size:12px">${timeAgo(g.created_at)}</span>
          </div>
          ${g.note ? `<div class="muted" style="font-size:13px;margin:8px 0 0;font-style:italic">💬 „${esc(g.note)}”</div>` : `<div class="faint" style="font-size:12px;margin:8px 0 0">💬 bez důvodu</div>`}
          ${g.shared ? `<div class="muted" style="font-size:12.5px;margin:8px 0 0;color:#ff8a8a">🚩 <b>Stejná IP / zařízení</b> — možný pokus o přelévání bodů (funnel). Zvaž zamítnutí.</div>` : ""}
          <div class="toolbar" style="margin-top:10px">
            <button class="btn btn-primary btn-sm" data-action="gift-approve" data-id="${g.id}" data-label="${esc(g.from)}→${esc(g.to)} ${fmtPts(g.amount)}">✅ Povolit</button>
            <button class="btn btn-danger btn-sm" data-action="gift-reject" data-id="${g.id}" data-label="${esc(g.from)}→${esc(g.to)}">✖ Zamítnout (vrátit)</button>
            <span class="faint" style="font-size:12px;margin-left:auto">zůstatek odesílatele: ${fmtPts(g.from_points)}</span>
          </div>
        </div>`).join("");
  }
  if (grRecent.length) {
    giftReqHtml += `<div class="muted" style="font-size:12.5px;margin:12px 0 6px">Posledně vyřízené:</div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Od</th><th>Komu</th><th>Sedláků</th><th>Stav</th><th>Admin</th><th>Kdy</th></tr></thead><tbody>
      ${grRecent.map((g) => `<tr>
        <td>${uLink(g.from)}</td><td>${uLink(g.to)}</td>
        <td><b>${fmtPts(g.amount)}</b></td>
        <td>${g.status === "approved" ? `<span style="color:#46d369">✅ povoleno</span>` : `<span style="color:#ff7a7a">✖ zamítnuto</span>`}</td>
        <td class="faint">${esc(g.decided_by || "—")}</td>
        <td class="faint">${timeAgo(g.decided_at || g.created_at)}</td>
      </tr>`).join("")}
      </tbody></table></div>`;
  }
  return giftReqHtml;
}
async function adminGifts() {
  const box = $("#adminContent");
  box.innerHTML = skeletonCards(2);
  try {
    const giftReqs = await api("/admin/gift-requests");
    box.innerHTML = giftRequestsHTML(giftReqs);
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function banIp() {
  const ip = ($("#ipBanIp")?.value || "").trim();
  const reason = ($("#ipBanReason")?.value || "").trim();
  const hours = parseInt($("#ipBanHours")?.value || "1", 10);
  if (!ip) { toast("Zadej IP adresu.", "error"); return; }
  try { await api("/admin/security/ip-ban", { method: "POST", body: { ip, reason, hours } }); toast("🚫 IP zablokována: " + ip, "success"); adminSecurity(); }
  catch (e) { toast(e.message, "error"); }
}
async function unbanIp(ip) {
  try { await api("/admin/security/ip-unban", { method: "POST", body: { ip } }); toast("IP odblokována.", "info"); adminSecurity(); }
  catch (e) { toast(e.message, "error"); }
}
async function toggleAutoban(enabled) {
  try { await api("/admin/security/autoban", { method: "POST", body: { enabled } }); toast("Auto-ban při náporu " + (enabled ? "zapnut ✅" : "vypnut"), enabled ? "success" : "info"); adminSecurity(); }
  catch (e) { toast(e.message, "error"); }
}
async function quickBanIp(ip) {
  if (!ip) return;
  try { await api("/admin/security/ip-ban", { method: "POST", body: { ip, reason: "Manuální ban z přehledu provozu", hours: 24 } }); toast("🚫 IP zablokována: " + ip, "success"); adminSecurity(); }
  catch (e) { toast(e.message, "error"); }
}

async function adminSecurity() {
  const box = $("#adminContent");
  try {
    const auditQS = `action=${encodeURIComponent(adminState.auditAction)}`
      + `&admin_name=${encodeURIComponent(adminState.auditAdmin)}`
      + `&limit=${adminState.auditLimit}&offset=${adminState.auditOffset}`;
    const loginsQS = `username=${encodeURIComponent(adminState.loginsQuery)}`
      + `&ip=${encodeURIComponent(adminState.loginsIp)}`
      + `&limit=${adminState.loginsLimit}&offset=${adminState.loginsOffset}`;
    const pfQS = `q=${encodeURIComponent(adminState.pfQuery)}&flow=${adminState.pfFlow}`
      + `&min_amount=${adminState.pfMin}&reason=${encodeURIComponent(adminState.pfReason)}`
      + `&limit=${adminState.pfLimit}&offset=${adminState.pfOffset}`;
    const [ac, sessions, logins, rules, audit, ipbans, traffic, gifts, pf, funnel, giftReqs] = await Promise.all([
      api("/admin/security/anticheat"),
      api("/admin/security/sessions"),
      api("/admin/security/logins?" + loginsQS),
      api("/admin/security/rules"),
      api("/admin/security/audit?" + auditQS),
      api("/admin/security/ip-bans"),
      api("/admin/security/traffic"),
      api("/admin/security/gifts?limit=100"),
      api("/admin/security/points-feed?" + pfQS),
      api("/admin/security/funnel"),
      api("/admin/gift-requests"),
    ]);

    const rf = ac.rapid_farming || [], ra = ac.redeem_abuse || [];
    let alerts;
    if (!ac.stats.flags) {
      alerts = `<div class="panel ok">✅ Žádné podezřelé vzorce – vše v pořádku.</div>`;
    } else {
      alerts = ac.shared_ips.map((s) => `
        <div class="panel bad" style="margin-bottom:12px">
          <div class="row-between"><b>🚩 Sdílená IP <span class="code-pill">${esc(s.ip)}</span></b><span class="badge badge-admin">${s.user_count} účtů</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 10px">Z této IP se přihlásilo více účtů – může jít o farmu alt účtů na body.</div>
          <div class="stack-12">${s.users.map(acUserRow).join("")}</div>
          <button class="btn btn-danger btn-sm" data-action="ban-cluster" data-ids="${s.users.map((u) => u.id).join(",")}" data-label="IP ${esc(s.ip)}" style="margin-top:10px">🔨 Zbanovat celý cluster (${s.user_count})</button>
        </div>`).join("");
      alerts += ra.map((r) => `
        <div class="panel bad" style="margin-bottom:12px">
          <div class="row-between"><b>🚩 Redeem kód ze stejné IP <span class="code-pill">${esc(r.code)}</span></b><span class="badge badge-admin">${esc(r.ip)}</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 10px">Jeden kód uplatnilo víc účtů ze stejné IP – možný abuse.</div>
          <div class="stack-12">${r.users.map(acUserRow).join("")}</div>
        </div>`).join("");
      alerts += rf.map((f) => `
        <div class="panel gold" style="margin-bottom:12px">
          <div class="row-between"><b>⚡ Rychlé farmení bodů</b><span class="badge badge-vip">${f.events}× / hod · +${fmtPts(f.gained)}</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 8px">Účet nasbíral hodně bodů v krátkém čase.</div>
          <div class="stack-12">${acUserRow(f.user)}</div>
        </div>`).join("");
      alerts += ac.multi_ip_users.map((m) => `
        <div class="panel gold" style="margin-bottom:12px">
          <div class="row-between"><b>⚠️ Účet z mnoha IP</b><span class="badge badge-vip">${m.ip_count} IP</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 8px">IP adresy: ${m.ips.map((ip) => `<span class="code-pill">${esc(ip)}</span>`).join(" ")}</div>
          <div class="stack-12">${acUserRow(m.user)}</div>
        </div>`).join("");
      alerts += (ac.rapid_fire || []).map((x) => `
        <div class="panel bad" style="margin-bottom:12px">
          <div class="row-between"><b>🚩 Rapid-fire nákupy</b><span class="badge badge-admin">${x.count}× / 5 min</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 10px">Příliš mnoho nákupů v krátkém čase – bot pattern.</div>
          <div class="stack-12">${acUserRow(x.user)}</div></div>`).join("");
      alerts += (ac.new_account_spend || []).map((x) => `
        <div class="panel bad" style="margin-bottom:12px">
          <div class="row-between"><b>🚩 Nový účet, vysoká útrata</b><span class="badge badge-admin">${fmtPts(x.spent)} / 24h</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 10px">Účet mladší 24 h hodně utratil – možný farm bot.</div>
          <div class="stack-12">${acUserRow(x.user)}</div></div>`).join("");
      alerts += (ac.headless || []).map((x) => `
        <div class="panel bad" style="margin-bottom:12px">
          <div class="row-between"><b>🤖 Headless browser</b><span class="badge badge-admin">webdriver</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 10px">Detekován automatizovaný prohlížeč (Selenium / Puppeteer / Playwright).</div>
          <div class="stack-12">${acUserRow(x.user)}</div></div>`).join("");
      alerts += (ac.device_accounts || []).map((d) => `
        <div class="panel gold" style="margin-bottom:12px">
          <div class="row-between"><b>🖥️ Stejný otisk prohlížeče, víc účtů <span class="code-pill">fp:${esc(d.fp)}</span></b><span class="badge badge-admin">${d.user_count} účtů</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 10px">⚠️ Otisk je <b>hrubý</b> (model zařízení + prohlížeč + jazyk), takže ho můžou sdílet i <b>různí lidé se stejným mobilem</b> — sám o sobě to <b>není důkaz</b> alt-farmy. Ověř tvrdými signály: <b>sdílená IP</b> a <b>převody/gifty mezi účty</b>. (Ban jednoho už NEzablokuje sdílený otisk — jen unikátní pro pár účtů.)</div>
          <div class="stack-12">${d.users.map(acUserRow).join("")}</div><button class="btn btn-danger btn-sm" data-action="ban-cluster" data-ids="${d.users.map((u) => u.id).join(",")}" data-label="otisk fp:${esc(d.fp)}" style="margin-top:10px">🔨 Zbanovat celý cluster (${d.user_count})</button></div>`).join("");
      alerts += (ac.vpn_ips || []).map((v) => `
        <div class="panel gold" style="margin-bottom:12px">
          <div class="row-between"><b>🛡️ VPN / datacenter IP <span class="code-pill">${esc(v.ip)}</span></b><span class="badge badge-vip">${v.users.length} účtů</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 8px">IP spadá do známého datacenter/VPN rozsahu — možný pokus o obejití (multiúčet / evasion banu).</div>
          <div class="stack-12">${v.users.map(acUserRow).join("")}</div></div>`).join("");
    }
    // --- Funnel / chip-dumping: přelévání bodů mezi dvojicemi účtů ---
    const fPairs = (funnel && funnel.pairs) || [], housePl = (funnel && funnel.house_pl) || [];
    let funnelHtml = `<div class="section-title" style="margin-top:26px">💸 Přelévání bodů mezi účty <span class="faint" style="font-size:12px;font-weight:400">— chip-dumping: kdo komu sype body přes duely / piškvorky / dary</span></div>`;
    if (!fPairs.length) {
      funnelHtml += `<div class="panel ok">✅ Žádné podezřelé jednostranné převody mezi dvojicemi.</div>`;
    } else {
      funnelHtml += fPairs.map((p) => {
        const link = p.shared_ip || p.shared_device;
        const tags = `${p.shared_ip ? `<span class="badge badge-admin">⚠️ sdílená IP</span> ` : ""}${p.shared_device ? `<span class="badge badge-admin">⚠️ stejné zařízení</span>` : ""}`;
        const via = [];
        if (p.via.duels) via.push(`${p.via.duels}× duel`);
        if (p.via.games) via.push(`${p.via.games}× piškvorky`);
        if (p.via.gifts) via.push(`${p.via.gifts}× dar`);
        const ids = [p.source && p.source.id, p.receiver && p.receiver.id].filter(Boolean);
        const sN = esc((p.source && p.source.username) || "?"), rN = esc((p.receiver && p.receiver.username) || "?");
        return `
        <div class="panel ${link ? "bad" : "gold"}" style="margin-bottom:12px">
          <div class="row-between"><b>${link ? "🚩" : "💸"} ${sN} → ${rN}</b><span class="badge ${link ? "badge-admin" : "badge-vip"}">netto +${fmtPts(p.net)}</span></div>
          <div class="muted" style="font-size:13px;margin:7px 0 8px"><b>${sN}</b> přelil/poslal účtu <b>${rN}</b> netto <b>${fmtPts(p.net)}</b> přes ${via.join(" + ") || "—"}${p.matches ? ` · výhra příjemce <b>${p.recv_wins}/${p.matches}</b>` : ""}. ${tags || ""}</div>
          <div class="stack-12">${acUserRow(p.receiver)}${acUserRow(p.source)}</div>
          ${ids.length === 2 ? `<button class="btn btn-danger btn-sm" data-action="ban-cluster" data-ids="${ids.join(",")}" data-label="funnel ${sN}→${rN}" style="margin-top:10px">🔨 Zbanovat oba</button>` : ""}
        </div>`;
      }).join("");
    }
    if (housePl.length) {
      funnelHtml += `<div class="section-title" style="margin-top:22px">🏦 Nejvíc v plusu vůči house <span class="faint" style="font-size:12px;font-weight:400">— čistý zisk z PvP + predikcí; velký objem = spíš variance, malý objem + velký zisk = prověř</span></div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Uživatel</th><th>Čistý zisk</th><th>Objem vsazeno</th></tr></thead><tbody>
      ${housePl.map((h) => `<tr><td>${uLink(h.user.username)} ${roleBadge(h.user.role)}</td><td><b style="color:#46d369">+${fmtPts(h.net)}</b></td><td class="faint">${fmtPts(h.volume)}</td></tr>`).join("")}
      </tbody></table></div>`;
    }

    const rulesHtml = rules.map(ruleRowHTML).join("");

    // --- Provoz / anti-DDoS ---
    const ts = traffic.stats || {};
    const thr = ts.threshold_per_min || 600;
    const topRows = (traffic.top || []).map((t) => {
      const hot = t.per_min > thr, warn = !hot && t.per_min > thr * 0.5;
      const col = hot ? "color:#ff5c5c;font-weight:800" : warn ? "color:#ffae57;font-weight:700" : "";
      return `<tr>
        <td><span class="code-pill">${esc(t.ip)}</span></td>
        <td>${t.count}</td>
        <td style="${col}">${t.per_min}/min${hot ? " 🔥" : warn ? " ⚠️" : ""}</td>
        <td><button class="btn btn-danger btn-sm" data-action="ban-ip-quick" data-ip="${esc(t.ip)}">Zabanovat</button></td>
      </tr>`;
    }).join("");
    const autobanRows = (traffic.recent_autobans || []).map((a) => `<tr>
      <td><span class="code-pill">${esc(a.ip)}</span></td>
      <td style="color:#ff5c5c">${a.per_min}/min</td>
      <td class="faint">${timeAgo(a.at)}</td>
      <td><button class="btn btn-ghost btn-sm" data-action="ip-unban" data-ip="${esc(a.ip)}">Odblokovat</button></td>
    </tr>`).join("");

    const giftPending = (giftReqs && giftReqs.pending_count) || 0;
    const SEC_TABS = [["anticheat", "🚨 Anticheat"], ["points", "💸 Pohyby bodů"], ["logins", "🔐 Přihlášení & provoz"], ["audit", "🧾 Audit & dary"]];
    const pills = `<div class="sec-tabs">${SEC_TABS.map(([k, l]) => `<button class="sec-pill${adminState.secTab === k ? " active" : ""}" data-action="sec-tab" data-tab="${k}">${l}${k === "audit" && giftPending ? ` <span class="req-badge">${giftPending}</span>` : ""}</button>`).join("")}</div>`;
    const pfHtml = `
      <div class="row-between" style="margin:4px 0 12px;flex-wrap:wrap;gap:8px">
        <div class="section-title" style="margin:0">💸 Pohyby bodů <span class="faint" style="font-size:12px;font-weight:400">— každý +/− sedlák, nic neunikne</span></div>
        <div class="faint" style="font-size:13px">filtr: <b style="color:#46d369">+${fmtPts(pf.sum_in)}</b> / <b style="color:#ff7a7a">−${fmtPts(pf.sum_out)}</b> · ${pf.total} záznamů</div>
      </div>
      <div class="toolbar">
        <input id="pfQuery" class="input input-sm" placeholder="Nick" value="${esc(adminState.pfQuery)}" style="max-width:150px">
        <select id="pfFlow" class="input input-sm" style="max-width:140px">
          <option value="" ${adminState.pfFlow === "" ? "selected" : ""}>± vše</option>
          <option value="in" ${adminState.pfFlow === "in" ? "selected" : ""}>jen příjmy +</option>
          <option value="out" ${adminState.pfFlow === "out" ? "selected" : ""}>jen výdaje −</option>
        </select>
        <input id="pfMin" class="input input-sm" type="number" min="0" placeholder="Min částka" value="${adminState.pfMin || ""}" style="max-width:120px">
        <input id="pfReason" class="input input-sm" placeholder="Důvod (dar, drop, duel…)" value="${esc(adminState.pfReason)}" style="max-width:190px">
        <button class="btn btn-sm" data-action="pf-filter">Filtrovat</button>
        ${(adminState.pfQuery || adminState.pfFlow || adminState.pfMin || adminState.pfReason || adminState.pfOffset) ? `<button class="btn btn-ghost btn-sm" data-action="pf-reset">Reset</button>` : ""}
      </div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Uživatel</th><th>Pohyb</th><th>Důvod</th><th>Kdy</th></tr></thead><tbody>
        ${pf.rows.length ? pf.rows.map((r) => `<tr>
          <td>${uLink(r.username)} ${roleBadge(r.role)}</td>
          <td><b style="color:${r.change >= 0 ? "#46d369" : "#ff7a7a"}">${r.change >= 0 ? "+" : "−"}${fmtPts(Math.abs(r.change))}</b></td>
          <td class="faint" style="font-size:12.5px">${esc(r.reason || "—")}</td>
          <td class="faint">${timeAgo(r.created_at)}</td>
        </tr>`).join("") : `<tr><td colspan="4"><div class="empty">Žádné pohyby pro tento filtr.</div></td></tr>`}
      </tbody></table></div>
      <div class="toolbar" style="margin-top:8px">
        ${adminState.pfOffset > 0 ? `<button class="btn btn-ghost btn-sm" data-action="pf-page" data-dir="prev">← Předchozí</button>` : ""}
        ${adminState.pfOffset + pf.rows.length < pf.total ? `<button class="btn btn-ghost btn-sm" data-action="pf-page" data-dir="next">Další →</button>` : ""}
        <span class="faint" style="margin-left:auto;font-size:12px">${pf.total ? adminState.pfOffset + 1 : 0}–${adminState.pfOffset + pf.rows.length} z ${pf.total}</span>
      </div>`;

    // --- Žádosti o dar (schvalování) — render sdílí samostatný tab „Dary" (i pro broadcastera) ---
    const giftReqHtml = giftRequestsHTML(giftReqs);

    box.innerHTML = `
      <div class="toolbar"><a class="btn btn-ghost btn-sm" href="/api/admin/backup">💾 Stáhnout zálohu DB</a><span class="faint" style="font-size:12px">Doporučeno zálohovat pravidelně.</span></div>
      <div class="stat-grid">
        ${statBox(ac.stats.flags, "Anticheat příznaků", ac.stats.flags ? "warn" : "accent")}
        ${statBox(sessions.length, "Aktivních relací")}
        ${statBox(ac.stats.unique_ips, "Unikátních IP")}
        ${statBox(ac.stats.events, "Přihlášení celkem")}
        ${statBox(ac.stats.banned, "Zabanovaných", ac.stats.banned ? "warn" : "")}
      </div>

      ${pills}
      <div class="sec-pane${adminState.secTab === "anticheat" ? " active" : ""}" data-pane="anticheat">
      <div class="section-title">🚨 Anticheat detekce <span class="faint" style="font-size:12px;font-weight:400">— VPN detekce proxycheck: <b style="color:${ac.iprep_enabled ? "#46d369" : "#999"}">${ac.iprep_enabled ? "ZAP ✓" : "VYP"}</b></span></div>
      ${alerts}
      ${funnelHtml}

      <div class="section-title" style="margin-top:26px">⚙️ Anticheat pravidla</div>
      <div class="ac-rules">${rulesHtml}</div>
      </div>

      <div class="sec-pane${adminState.secTab === "points" ? " active" : ""}" data-pane="points">
      ${pfHtml}
      </div>

      <div class="sec-pane${adminState.secTab === "logins" ? " active" : ""}" data-pane="logins">
      <div class="section-title" style="margin-top:26px">🟢 Aktivní relace</div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Uživatel</th><th>IP</th><th>Poslední aktivita</th><th>Zařízení</th></tr></thead><tbody>
        ${sessions.length ? sessions.map((s) => `<tr>
          <td>${esc(s.username)} ${roleBadge(s.role)}${s.banned ? ` <span class="badge badge-admin">BAN</span>` : ""}</td>
          <td><span class="code-pill">${esc(s.ip || "?")}</span></td>
          <td class="faint">${s.last_seen ? timeAgo(s.last_seen) : "—"}</td>
          <td class="faint" style="font-size:12px">${uaShort(s.user_agent)}</td>
        </tr>`).join("") : `<tr><td colspan="4"><div class="empty">Nikdo není přihlášený.</div></td></tr>`}
      </tbody></table></div>

      <div class="section-title" style="margin-top:26px">🚫 IP bany <span class="faint" style="font-size:13px">— zabanovaná IP web vůbec neotevře</span></div>
      <form class="toolbar" data-submit="ip-ban">
        <input id="ipBanIp" class="input input-sm" placeholder="IP adresa (např. 78.80.80.197)" style="max-width:230px">
        <input id="ipBanReason" class="input input-sm" placeholder="Důvod (cheating…)" style="max-width:190px">
        <select id="ipBanHours" class="input input-sm" style="max-width:120px">
          <option value="1">1 hodina</option><option value="24">24 hodin</option>
          <option value="168">7 dní</option><option value="0">Trvale</option>
        </select>
        <button class="btn btn-danger btn-sm" type="submit">🚫 Zablokovat IP</button>
      </form>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>IP</th><th>Důvod</th><th>Zabanováno</th><th>Vyprší</th><th></th></tr></thead><tbody>
        ${ipbans.length ? ipbans.map((b) => `<tr>
          <td><span class="code-pill">${esc(b.ip)}</span></td>
          <td>${esc(b.reason || "—")}</td>
          <td class="faint">${timeAgo(b.created_at)}</td>
          <td class="faint">${b.expires_at ? new Date(b.expires_at).toLocaleString("cs-CZ") : "trvale"}</td>
          <td><button class="btn btn-ghost btn-sm" data-action="ip-unban" data-ip="${esc(b.ip)}">Odblokovat</button></td>
        </tr>`).join("") : `<tr><td colspan="5"><div class="empty">Žádné IP bany.</div></td></tr>`}
      </tbody></table></div>

      <div class="section-title" style="margin-top:26px">📈 Provoz (anti-DDoS) <span class="faint" style="font-size:13px">— Top IP za posl. ${ts.window_min || 5} min</span></div>
      <div class="toolbar">
        <span class="faint" style="font-size:13px">Sleduji <b>${ts.tracked_ips || 0}</b> IP · <b>${ts.total_requests || 0}</b> req / ${ts.window_min || 5} min · práh auto-banu <b>${thr}/min</b> → ban ${ts.ban_minutes || 10} min</span>
        <span style="margin-left:auto;font-size:12.5px">Auto-ban <b>${ts.autoban_enabled ? "ZAP" : "VYP"}</b></span>
        <button class="ac-switch ${ts.autoban_enabled ? "on" : ""}" data-action="ddos-autoban" data-on="${ts.autoban_enabled ? 1 : 0}" title="Auto-ban IP při náporu"><span class="knob"></span></button>
        <button class="btn btn-ghost btn-sm" data-action="sec-refresh">↻ Obnovit</button>
      </div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>IP</th><th>Req / ${ts.window_min || 5} min</th><th>Rychlost</th><th></th></tr></thead><tbody>
        ${topRows || `<tr><td colspan="4"><div class="empty">Zatím žádný provoz k zobrazení.</div></td></tr>`}
      </tbody></table></div>
      ${autobanRows ? `<div class="muted" style="margin:12px 0 6px;font-size:13px">🔨 Nedávno auto-zabanované IP (dočasně):</div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>IP</th><th>Rychlost</th><th>Kdy</th><th></th></tr></thead><tbody>${autobanRows}</tbody></table></div>` : ""}

      <div class="section-title" style="margin-top:26px">📜 Historie přihlášení (IP log)</div>
      <div class="toolbar">
        <input id="loginsQuery" class="input input-sm" placeholder="Filtr: nick" value="${esc(adminState.loginsQuery)}" style="max-width:180px">
        <input id="loginsIp" class="input input-sm" placeholder="Filtr: IP" value="${esc(adminState.loginsIp)}" style="max-width:160px">
        <button class="btn btn-sm" data-action="logins-filter">Filtrovat</button>
        ${(adminState.loginsQuery || adminState.loginsIp || adminState.loginsOffset) ? `<button class="btn btn-ghost btn-sm" data-action="logins-reset">Reset</button>` : ""}
        <span class="faint" style="margin-left:auto;font-size:12px">${logins.length} záznamů (offset ${adminState.loginsOffset})</span>
      </div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Uživatel</th><th>IP</th><th>Způsob</th><th>Kdy</th><th>Zařízení</th></tr></thead><tbody>
        ${logins.length ? logins.map((l) => `<tr>
          <td>${esc(l.username)} ${roleBadge(l.role)}</td>
          <td><span class="code-pill">${esc(l.ip || "?")}</span></td>
          <td class="faint">${esc(l.method || "")}</td>
          <td class="faint">${timeAgo(l.created_at)}</td>
          <td class="faint" style="font-size:12px">${uaShort(l.user_agent)}</td>
        </tr>`).join("") : `<tr><td colspan="5"><div class="empty">Žádné záznamy.</div></td></tr>`}
      </tbody></table></div>
      <div class="toolbar" style="margin-top:8px">
        ${adminState.loginsOffset > 0 ? `<button class="btn btn-ghost btn-sm" data-action="logins-page" data-dir="prev">← Předchozí</button>` : ""}
        ${logins.length >= adminState.loginsLimit ? `<button class="btn btn-ghost btn-sm" data-action="logins-page" data-dir="next">Další →</button>` : ""}
      </div>
      </div>

      <div class="sec-pane${adminState.secTab === "audit" ? " active" : ""}" data-pane="audit">
      ${giftReqHtml}
      <div class="section-title" style="margin-top:26px">🧾 Audit log (akce adminů) <span class="faint" style="font-size:13px">– celkem ${audit.total}</span></div>
      <div class="toolbar">
        <select id="auditAction" class="input input-sm" style="max-width:180px">
          <option value="">— všechny akce —</option>
          ${(audit.filters.actions || []).map((a) => `<option value="${esc(a)}" ${a === adminState.auditAction ? "selected" : ""}>${esc(auditLabel(a))}</option>`).join("")}
        </select>
        <select id="auditAdmin" class="input input-sm" style="max-width:160px">
          <option value="">— všichni admini —</option>
          ${(audit.filters.admins || []).map((a) => `<option value="${esc(a)}" ${a === adminState.auditAdmin ? "selected" : ""}>${esc(a)}</option>`).join("")}
        </select>
        <button class="btn btn-sm" data-action="audit-filter">Filtrovat</button>
        ${(adminState.auditAction || adminState.auditAdmin || adminState.auditOffset) ? `<button class="btn btn-ghost btn-sm" data-action="audit-reset">Reset</button>` : ""}
        <a class="btn btn-ghost btn-sm" style="margin-left:auto" href="/api/admin/export/audit.csv?action=${encodeURIComponent(adminState.auditAction)}&admin_name=${encodeURIComponent(adminState.auditAdmin)}">📥 Export CSV</a>
      </div>
      ${auditTimeline(audit.rows)}
      <div class="toolbar" style="margin-top:8px">
        ${adminState.auditOffset > 0 ? `<button class="btn btn-ghost btn-sm" data-action="audit-page" data-dir="prev">← Předchozí</button>` : ""}
        ${adminState.auditOffset + audit.rows.length < audit.total ? `<button class="btn btn-ghost btn-sm" data-action="audit-page" data-dir="next">Další →</button>` : ""}
        <span class="faint" style="margin-left:auto;font-size:12px">${adminState.auditOffset + 1}–${adminState.auditOffset + audit.rows.length} z ${audit.total}</span>
      </div>

      <div class="section-title" style="margin-top:26px">🎁 Dary ve Směnárně <span class="faint" style="font-size:13px">– kdo komu poslal sedláky (celkem ${gifts.total})</span></div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Od koho</th><th>Komu</th><th>Sedláků</th><th>Kdy</th></tr></thead><tbody>
        ${gifts.rows.length ? gifts.rows.map((g) => `<tr>
          <td><b>${uLink(g.from)}</b></td>
          <td>${uLink(g.to)}</td>
          <td><b style="color:var(--accent)">${fmtPts(g.amount)}</b></td>
          <td class="faint">${timeAgo(g.created_at)}</td>
        </tr>`).join("") : `<tr><td colspan="4"><div class="empty">Zatím nikdo nikomu nic neposlal.</div></td></tr>`}
      </tbody></table></div>
      </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function banUser(id, ban) {
  let reason = "";
  if (ban) {
    reason = prompt("Důvod zabanování (anticheat):", "Multi-accounting / sdílená IP");
    if (reason === null) return;
  }
  try {
    const r = await api(`/admin/users/${id}/ban`, { method: "POST", body: { banned: ban, reason } });
    const dev = r && r.devices_banned ? ` (+ ${r.devices_banned} zařízení zabanováno)` : "";
    const kickOk = r && r.kick && r.kick.ok ? " · Kick chat ✅" : "";
    toast(ban ? `Účet zabanován a odhlášen${dev}${kickOk}.` : `Ban zrušen${kickOk}.`, ban ? "info" : "success");
    if (r && r.kick && !r.kick.ok && !r.kick.skipped) {
      if (r.kick.mod_block) toast("ℹ️ " + r.kick.error, "info");   // moderátora/broadcastera Kick zabanovat nedovolí – web ban platí
      else toast("⚠️ Kick chat se nepovedl: " + (r.kick.error || ""), "error");
    }
    loadAdminStats();
    if (adminState.tab === "security") adminSecurity(); else adminUsers();
  } catch (e) { toast(e.message, "error"); }
}

async function giftDecide(id, approve, label) {
  const verb = approve ? "POVOLIT" : "ZAMÍTNOUT";
  const tail = approve ? "\n\nBody se přesunou příjemci." : "\n\nBody se vrátí odesílateli, příjemce nedostane nic.";
  if (!confirm(`${verb} dar ${label || ""}?${tail}`)) return;
  try {
    const r = await api(`/admin/gift-requests/${id}/${approve ? "approve" : "reject"}`, { method: "POST" });
    toast(approve ? `✅ Dar povolen — ${fmtPts(r.amount)} přesunuto.` : `✖ Dar zamítnut — ${fmtPts(r.amount)} vráceno odesílateli.`, approve ? "success" : "info");
    loadAdminStats();
    adminSecurity();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Nábor moderátorů: přihláška (divák) + admin review --- */
async function pageModApply() {
  const view = $("#view");
  view.innerHTML = `<div class="page-head"><h1>🛡️ Nábor moderátorů</h1><p class="muted">Chceš pomáhat držet chat v pohodě? Vyplň přihlášku — je napojená na tvůj účet, takže nemusíš nic dokazovat.</p></div><div id="modApplyBox"></div>`;
  const box = $("#modApplyBox");
  if (!state.user) { box.innerHTML = `<div class="panel"><div class="empty"><div class="big">🔒</div>Pro přihlášku se <a href="#" data-action="connect" style="color:var(--accent)">připoj přes Kick</a>.</div></div>`; return; }
  box.innerHTML = skeletonCards(1);
  try {
    const s = await api("/mod-apply/status");
    if (s.is_staff) { box.innerHTML = `<div class="panel ok">✅ Už jsi člen týmu. 🛡️</div>`; return; }
    if (!s.open) { box.innerHTML = `<div class="panel"><div class="empty"><div class="big">🔒</div>Nábor moderátorů je teď zavřený. Zkus to zase příště!</div></div>`; return; }
    if (s.applied && s.status === "pending") { box.innerHTML = `<div class="panel ok">⏳ Přihlášku už máš odeslanou — čeká na vyřízení. Ozveme se ti přes 🔔.</div>`; return; }
    if (s.applied && s.status === "accepted") { box.innerHTML = `<div class="panel ok">✅ Tvoje přihláška byla přijata! 🎉 Vítej v týmu.</div>`; return; }
    box.innerHTML = modApplyFormHTML(s.applied && s.status === "rejected");
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function modApplyFormHTML(reapply) {
  const ta = (id, label, ph) => `<div class="field"><label>${label}</label><textarea class="input" id="${id}" rows="2" maxlength="2000" placeholder="${ph}"></textarea></div>`;
  return `<div class="panel">
    ${reapply ? `<div class="muted" style="margin-bottom:12px">Předchozí přihláška nebyla vybrána — klidně to zkus znovu. 🌾</div>` : ""}
    <form data-submit="mod-apply">
      <div class="field-row">
        <div class="field"><label>Věk</label><input class="input" id="ma_age" maxlength="10" placeholder="např. 18"></div>
        <div class="field"><label>Discord *</label><input class="input" id="ma_discord" maxlength="64" placeholder="tvuj_nick"></div>
        <div class="field"><label>Pásmo / odkud</label><input class="input" id="ma_tz" maxlength="64" placeholder="ČR / CET"></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Hodin týdně</label><input class="input" id="ma_hours" maxlength="40" placeholder="např. 10h"></div>
        <div class="field"><label>Kdy býváš online?</label><input class="input" id="ma_avail" maxlength="200" placeholder="večery, víkendy…"></div>
      </div>
      <div class="field"><label>Jak dlouho sleduješ stream?</label><input class="input" id="ma_watch" maxlength="100" placeholder="např. půl roku"></div>
      ${ta("ma_exp", "Moderoval jsi už někde? Kde, jak dlouho?", "Nech prázdné, pokud ne")}
      ${ta("ma_motivation", "Proč chceš moderovat zrovna pro Zurys? *", "Pár vět (povinné)")}
      <div class="section-title" style="margin-top:18px;font-size:15px">🧩 Modelové situace</div>
      ${ta("ma_spam", "Někdo v chatu spamuje a uráží ostatní. Co uděláš?", "")}
      ${ta("ma_reward", "Divák tvrdí že nedostal odměnu z webu a je agresivní. Jak to vyřešíš?", "")}
      ${ta("ma_ban", "Někdo se vrátí na novém účtu po banu. Co teď?", "")}
      ${ta("ma_note", "Cokoliv chceš dodat (nepovinné)", "")}
      <button class="btn btn-primary btn-block" type="submit" style="margin-top:10px">🛡️ Odeslat přihlášku</button>
    </form>
  </div>`;
}
async function doModApply() {
  const g = (id) => ($("#" + id)?.value || "").trim();
  const body = {
    age: g("ma_age"), discord: g("ma_discord"), timezone: g("ma_tz"),
    hours_week: g("ma_hours"), availability: g("ma_avail"), watch_time: g("ma_watch"),
    experience: g("ma_exp"), motivation: g("ma_motivation"),
    scenario_spam: g("ma_spam"), scenario_reward: g("ma_reward"), scenario_banevasion: g("ma_ban"),
    note: g("ma_note"),
  };
  if (body.discord.length < 2) { toast("Zadej Discord.", "error"); return; }
  if (body.motivation.length < 10) { toast("Napiš pár vět proč chceš moderovat.", "error"); return; }
  try {
    const r = await api("/mod-apply", { method: "POST", body });
    toast(r.message, "success");
    pageModApply();
  } catch (e) { toast(e.message, "error"); }
}
async function adminModApps() {
  const box = $("#adminContent");
  try {
    const d = await api("/admin/mod-applications");
    const card = (a, pending) => {
      const s = a.stats || {}, ans = a.answers || {};
      const flags = `${s.banned ? `<span class="badge badge-admin">BAN</span> ` : ""}${s.is_sub ? `<span class="badge badge-sub">SUB</span> ` : ""}`;
      const qa = [
        ["Věk", ans.age], ["Discord", ans.discord], ["Pásmo", ans.timezone],
        ["Hodin/týden", ans.hours_week], ["Online", ans.availability], ["Sleduje", ans.watch_time],
        ["Zkušenosti", ans.experience], ["Motivace", ans.motivation],
        ["💬 Spam situace", ans.scenario_spam], ["💬 Odměna situace", ans.scenario_reward],
        ["💬 Ban evasion", ans.scenario_banevasion], ["Dodatek", ans.note],
      ].filter(([, v]) => v && String(v).trim());
      return `<div class="panel" style="margin-bottom:14px">
        <div class="row-between" style="flex-wrap:wrap;gap:8px">
          <div><b>${uLink(a.username)}</b> ${flags}<span class="faint" style="font-size:12px">· ${timeAgo(a.created_at)}</span></div>
          ${pending ? "" : `<span class="badge ${a.status === "accepted" ? "badge-sub" : "badge-admin"}">${a.status === "accepted" ? "✅ přijat" : "✖ zamítnut"}${a.decided_by ? ` · ${esc(a.decided_by)}` : ""}</span>`}
        </div>
        <div class="ma-stats">🌾 ${fmtPts(s.points || 0)} · 📅 ${s.age_days != null ? s.age_days + " dní" : "?"} účet · 💬 ${s.chat_msgs || 0} zpráv · role <b>${esc(s.role || "user")}</b>${s.kick ? ` · kick:${esc(s.kick)}` : ""}</div>
        <div class="ma-qa">${qa.map(([q, v]) => `<div><span class="ma-q">${q}:</span> ${esc(String(v))}</div>`).join("")}</div>
        ${pending ? `<div class="toolbar" style="margin-top:12px">
          <label class="ma-setmod"><input type="checkbox" id="setmod_${a.id}" checked> rovnou dát roli mod</label>
          <button class="btn btn-primary btn-sm" data-action="modapp-accept" data-id="${a.id}" data-name="${esc(a.username)}">✅ Přijmout</button>
          <button class="btn btn-danger btn-sm" data-action="modapp-reject" data-id="${a.id}" data-name="${esc(a.username)}">✖ Zamítnout</button>
        </div>` : ""}
      </div>`;
    };
    box.innerHTML = `
      <div class="row-between" style="margin-bottom:12px;flex-wrap:wrap;gap:10px">
        <div class="section-title" style="margin:0">🛡️ Nábor moderátorů ${d.pending_count ? `<span class="badge badge-admin">${d.pending_count}</span>` : ""}</div>
        <div class="toolbar">
          <span class="badge ${d.open ? "badge-sub" : ""}">${d.open ? "🟢 nábor otevřený" : "🔒 zavřený"}</span>
          <button class="btn btn-ghost btn-sm" data-action="modapp-toggle">${d.open ? "Zavřít nábor" : "Otevřít nábor"}</button>
        </div>
      </div>
      <div class="faint" style="font-size:12.5px;margin-bottom:16px">Odkaz pro diváky (napiš do Kick chatu): <code style="user-select:all;color:var(--accent)">${location.origin}/#/mod-nabor</code></div>
      ${d.pending.length ? d.pending.map((a) => card(a, true)).join("") : `<div class="panel ok">✅ Žádné čekající přihlášky.</div>`}
      ${d.recent.length ? `<div class="section-title" style="margin-top:24px">Vyřízené</div>${d.recent.map((a) => card(a, false)).join("")}` : ""}`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function modAppDecide(id, accept, name) {
  const setmod = accept ? (($("#setmod_" + id) || {}).checked !== false) : false;
  const verb = accept ? "PŘIJMOUT" : "ZAMÍTNOUT";
  if (!confirm(`${verb} přihlášku od ${name}?${accept && setmod ? "\n\nRovnou dostane roli MOD." : ""}`)) return;
  try {
    await api(`/admin/mod-applications/${id}/decide`, { method: "POST", body: { action: accept ? "accept" : "reject", set_mod: setmod } });
    toast(accept ? `✅ ${name} přijat${setmod ? " + role mod" : ""}.` : `✖ Zamítnuto.`, accept ? "success" : "info");
    loadAdminStats(); adminModApps();
  } catch (e) { toast(e.message, "error"); }
}
async function modAppToggle() {
  try {
    const r = await api("/admin/mod-applications/toggle", { method: "POST" });
    toast(r.open ? "Nábor otevřen 🟢" : "Nábor zavřen 🔒", "info");
    adminModApps();
  } catch (e) { toast(e.message, "error"); }
}

/* --- Osobní herní staty --- */
async function pageGameStats() {
  const view = $("#view");
  view.innerHTML = `<div class="page-head"><h1>📊 Moje herní staty</h1><p class="muted">Tvoje čísla napříč hrami — vsazeno, vyhráno, win rate, největší výhra.</p></div><div id="gsBox"></div>`;
  const box = $("#gsBox");
  if (!state.user) { box.innerHTML = `<div class="panel"><div class="empty"><div class="big">🔒</div>Pro staty se <a href="#" data-action="connect" style="color:var(--accent)">připoj přes Kick</a>.</div></div>`; return; }
  box.innerHTML = skeletonCards(2);
  const col = (n) => n > 0 ? "#46d369" : n < 0 ? "#ff7a7a" : "var(--text-dim)";
  const sign = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + fmtPts(Math.abs(n));
  const stat = (label, val, c) => `<div class="gs-stat"><div class="gs-v" style="color:${c || "var(--text)"}">${val}</div><div class="gs-l">${label}</div></div>`;
  const card = (icon, name, g, opts = {}) => {
    if (!g.games) return `<div class="panel gs-card"><div class="gs-head">${icon} ${name}</div><div class="faint" style="font-size:13px;margin-top:6px">Zatím žádné hry.</div></div>`;
    return `<div class="panel gs-card">
      <div class="gs-head">${icon} ${name} <span class="faint" style="font-size:12.5px;font-weight:400">· ${g.games}× her</span></div>
      <div class="gs-grid">
        ${stat("Net", sign(g.net), col(g.net))}
        ${stat("Vsazeno", fmtPts(g.wagered))}
        ${stat("Vyhráno", fmtPts(g.won))}
        ${opts.winRate ? stat("Win rate", g.win_rate + " %") : ""}
        ${opts.wl ? stat("Výhry / prohry", g.won + " / " + g.lost) : ""}
        ${stat("Nej výhra", fmtPts(g.biggest))}
      </div></div>`;
  };
  try {
    const s = await api("/me/game-stats");
    const o = s.overall;
    box.innerHTML = `
      <div class="panel gs-overall">
        <div class="gs-head" style="font-size:18px">🏆 Celkem${o.games ? ` <span class="faint" style="font-size:13px;font-weight:400">· ${o.games}× her</span>` : ""}</div>
        ${o.games ? `<div class="gs-bignet" style="color:${col(o.net)}">${sign(o.net)} 🌾</div>
        <div class="gs-grid">
          ${stat("Vsazeno celkem", fmtPts(o.wagered))}
          ${stat("Vyhráno celkem", fmtPts(o.won))}
          ${stat("Největší výhra", fmtPts(o.biggest))}
        </div>` : `<div class="empty" style="padding:18px 0"><div class="big">🎲</div>Zatím jsi nehrál. Skoč na <a href="#/games" style="color:var(--accent)">Hry</a>!</div>`}
      </div>
      <div class="gs-cards">
        ${card("💣", "Mines", s.mines, { winRate: true })}
        ${card("🎲", "PvP — coinflip / piškvorky", s.pvp, { winRate: true, wl: true })}
        ${card("🃏", "Blackjack", s.blackjack)}
        ${card("🎯", "Predikce", s.predictions)}
      </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* --- Síň slávy (veřejní top podporovatelé) --- */
function memberSince(iso) {
  try {
    const days = Math.floor((Date.now() - Date.parse(iso)) / 86400000);
    if (days < 31) return days + " dní";
    if (days < 365) return Math.floor(days / 30) + " měs";
    const y = Math.floor(days / 365), m = Math.floor((days % 365) / 30);
    return y + " r" + (m ? " " + m + " m" : "");
  } catch (e) { return ""; }
}
function loyaltyBadge(iso) {
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86400000);
  if (isNaN(days)) return "";
  if (days >= 365) return ` <span class="loy-badge loy-gold">🥇 Veterán</span>`;
  if (days >= 180) return ` <span class="loy-badge loy-silver">🥈 Stálice</span>`;
  if (days >= 30) return ` <span class="loy-badge loy-bronze">🥉 Člen</span>`;
  return ` <span class="loy-badge loy-new">🌱 Nováček</span>`;
}
async function pageHallOfFame() {
  const view = $("#view");
  view.innerHTML = `<div class="page-head"><h1>📣 Síň slávy</h1><p class="muted">Lidi co táhnou komunitu — věrnost, podpora, aktivita. Díky vám! 🌾</p></div><div id="hofBox"></div>`;
  const box = $("#hofBox");
  box.innerHTML = skeletonCards(2);
  try {
    const d = await api("/hall-of-fame");
    const ageMs = (iso) => { try { return Date.now() - new Date(iso).getTime(); } catch (e) { return 0; } };
    const row = (u, i, mfn, frac) => `<div class="hof-row">
      <span class="hof-rank${i < 3 ? " g" + (i + 1) : ""}">${i + 1}</span>
      ${avatarHTML(u.username, u.avatar_url, "hof-av")}
      <div class="hof-mid">
        <div class="hof-name">${uLink(u.username)} ${roleBadge(u.role)}</div>
        <div class="hof-track"><span style="width:${Math.max(4, Math.round((frac || 0) * 100))}%"></span></div>
      </div>
      <span class="hof-metric">${mfn(u)}</span>
    </div>`;
    const board = (icon, title, hint, list, mfn, valFn) => {
      const max = Math.max(1, ...list.map((u) => valFn ? valFn(u) : 0));
      return `<div class="panel hof-card">
      <div class="hof-head">${icon} ${title} <span class="faint" style="font-size:12px;font-weight:400">— ${hint}</span></div>
      <div class="hof-list">${list.length ? list.map((u, i) => row(u, i, mfn, (valFn ? valFn(u) : 0) / max)).join("") : `<div class="faint" style="font-size:13px;padding:6px 0">Zatím nikdo.</div>`}</div>
    </div>`;
    };
    box.innerHTML = `<div class="hof-grid">
      ${board("🏆", "Nejvěrnější", "nejdéle v komunitě", d.loyal, (u) => "🎂 " + memberSince(u.created_at), (u) => ageMs(u.created_at))}
      ${board("💜", "Subscribeři", "naši subové", d.subs, () => "💜 sub", (u) => ageMs(u.created_at))}
      ${board("🎁", "Nejštědřejší", "nejvíc gift subů", d.gifters, (u) => { const n = u.subs || 0; const w = n === 1 ? "sub" : (n >= 2 && n <= 4 ? "suby" : "subů"); return `🎁 <b>${n}</b> ${w} <span class="faint" style="font-weight:400">· ${fmtPts(u.metric || 0)}</span>`; }, (u) => u.subs || 0)}
      ${board("🔥", "Nejaktivnější", "nejvíc v chatu", d.active, (u) => "💬 " + (u.metric || 0), (u) => u.metric || 0)}
    </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* --- Admin: Dropy (závod o kód) --- */
function happyHourCardHTML(h) {
  const on = !!h.livehappy_enabled;
  let active = false;
  try { active = h.active_until && new Date(h.active_until) > new Date(); } catch (e) {}
  return `<div class="panel" style="margin-bottom:18px;border-color:${on ? "var(--accent-2)" : "var(--border)"}">
    <div class="row-between"><div class="section-title" style="margin:0">🔴 Happy Hour (start streamu)</div>
      <span class="badge ${on ? "badge-sub" : ""}">${active ? "⚡ PRÁVĚ BĚŽÍ" : (on ? "🟢 ZAPNUTO" : "⚫ vypnuto")}</span></div>
    <p class="form-hint" style="margin:8px 0 12px"><b>▶️ Spustit TEĎ</b> = ručně hned (countdown <b>${h.livehappy_minutes} min</b>, <b>×${h.livehappy_mult}</b> za sledování i chat, bot to oznámí v chatu). <b>🔔 Auto na startu</b> = web spustí HH sám, jakmile začneš streamovat. 🌾⚡</p>
    <div class="field-row">
      <div class="field"><label>Násobič (×)</label><input class="input" id="hh_mult" type="number" min="1" max="10" step="0.5" value="${h.livehappy_mult}"></div>
      <div class="field"><label>Trvání (min)</label><input class="input" id="hh_min" type="number" min="1" max="720" value="${h.livehappy_minutes}"></div>
    </div>
    <div class="toolbar" style="margin-top:12px;flex-wrap:wrap">
      ${active
        ? `<button class="btn btn-danger" data-action="happy-stop">⏹️ Ukončit happy hour</button>`
        : `<button class="btn btn-primary" data-action="happy-start">▶️ Spustit TEĎ</button>`}
      <button class="btn btn-sm" data-action="happy-save">💾 Uložit</button>
      <button class="btn btn-ghost btn-sm" data-action="happy-toggle" data-on="${on ? 1 : 0}">${on ? "🔕 Vypnout auto na startu" : "🔔 Auto na startu streamu"}</button>
    </div>
  </div>`;
}
async function saveHappyHour(enable) {
  const body = {
    livehappy_mult: parseFloat($("#hh_mult").value || "1.5"),
    livehappy_minutes: parseInt($("#hh_min").value || "5", 10),
  };
  if (enable !== undefined) body.livehappy_enabled = enable;
  try {
    await api("/admin/live-happy", { method: "POST", body });
    toast(enable === 1 ? "Auto na startu zapnuto 🔔" : enable === 0 ? "Auto na startu vypnuto 🔕" : "Uloženo 💾", "success");
    adminDrops();
  } catch (e) { toast(e.message, "error"); }
}
async function startHappyNow() {
  if (!confirm("Spustit Happy Hour TEĎ? Oznámí to v Kick chatu. 🔥")) return;
  try {
    await api("/admin/live-happy", { method: "POST", body: {   // ulož aktuální mult/min než spustíš
      livehappy_mult: parseFloat($("#hh_mult").value || "2"),
      livehappy_minutes: parseInt($("#hh_min").value || "5", 10),
    } });
    const r = await api("/admin/live-happy/start", { method: "POST" });
    toast(`🔥 Happy Hour běží! ×${r.mult} na ${r.minutes} min — bot to hlásí v chatu.`, "success");
    adminDrops();
  } catch (e) { toast(e.message, "error"); }
}
async function stopHappyNow() {
  try {
    await api("/admin/live-happy/stop", { method: "POST" });
    toast("Happy Hour ukončen ⏹️", "info");
    adminDrops();
  } catch (e) { toast(e.message, "error"); }
}
function subGoalCardHTML(g) {
  const on = !!g.enabled;
  const step = g.step != null ? g.step : g.target;
  const rewardStep = g.reward_step != null ? g.reward_step : g.reward;
  const tierMax = g.tier_max != null ? g.tier_max : 0;
  const unlimited = !tierMax || tierMax <= 0;          // 0 = nekonečný žebříček
  const tierMaxTxt = unlimited ? "∞" : tierMax;
  const tier = g.tier != null ? g.tier : 0;
  const badge = g.maxed ? "🏆 MAX TIER" : (on ? "🟢 ZAPNUTO" : "⚫ vypnuto");
  return `<div class="panel" style="margin-bottom:18px;border-color:${on ? "var(--accent-2)" : "var(--border)"}">
    <div class="row-between"><div class="section-title" style="margin:0">🟣 Komunitní SUB cíl <span class="faint" style="font-weight:600;font-size:12px">(eskalující)</span></div>
      <span class="badge ${on ? "badge-sub" : ""}">${badge}</span></div>
    <p class="form-hint" style="margin:8px 0 12px">Kick suby (sub/resub +1, gift sub +počet) plní lištu. <b>Eskaluje:</b> každý dosažený tier (po <b>${step}</b> subech) dostane <b>každý gifter</b> odměnu ve výši tieru — tier 1 = <b>+${fmtPts(rewardStep)}</b>, tier 2 = <b>+${fmtPts(rewardStep * 2)}</b>, … ${unlimited ? "<b>donekonečna</b> (cíl roste po " + step + " bez stropu)" : "až do max <b>" + tierMax + "</b>"}. Pak se cíl zvedne na další tier. Gifter bere odměnu za <b>každý tier od svého příchodu</b> — early <b>kumulativně</b> (tier 1 + 2 + …), kdo přijde pozdějc, bere jen od svého tieru výš. Bot to oznámí v chatu. <b>Reset na konci streamu.</b> 🟣🎁🌾<br>Teď: <b>tier ${tier}${unlimited ? "" : "/" + tierMax}</b> · <b>${g.progress} / ${g.target}</b> subů do dalšího${g.gifters != null ? ` · <b>${g.gifters}</b> ${g.gifters === 1 ? "gifter" : "gifterů"} dnes` : ""}.</p>
    <div style="margin:0 0 12px;padding:9px 11px;background:rgba(145,71,255,.1);border:1px solid rgba(145,71,255,.35);border-radius:9px;font-size:12.5px">
      🖥️ <b>OBS overlay</b> (živě synced s webem): <code style="user-select:all">${location.origin}/overlay/subgoal.html</code>
      <button class="btn btn-ghost btn-sm" data-action="copy-url" data-url="${location.origin}/overlay/subgoal.html" style="margin-left:6px">📋 Kopírovat</button>
      <div class="faint" style="margin-top:5px">Přidej v OBS jako <b>Browser Source</b> (např. 640×150, průhledné pozadí). Subne někdo → lišta se hne na webu i na streamu.</div>
    </div>
    <div class="field-row">
      <div class="field"><label>Krok (subů / tier)</label><input class="input" id="sg_target" type="number" min="1" value="${step}"></div>
      <div class="field"><label>Odměna za tier (sedláků)</label><input class="input" id="sg_reward" type="number" min="0" value="${rewardStep}"></div>
      <div class="field"><label>Max tier (0 = ∞)</label><input class="input" id="sg_tier_max" type="number" min="0" value="${tierMax}"></div>
    </div>
    <div class="toolbar" style="margin-top:12px">
      <button class="btn ${on ? "btn-danger" : "btn-primary"}" data-action="subgoal-toggle" data-on="${on ? 1 : 0}">${on ? "⏸️ Vypnout SUB cíl" : "▶️ Zapnout SUB cíl"}</button>
      <button class="btn btn-sm" data-action="subgoal-save">💾 Uložit nastavení</button>
    </div>
  </div>`;
}
async function saveSubGoal(enable) {
  const body = {
    target: parseInt($("#sg_target").value || "10", 10),
    reward: parseInt($("#sg_reward").value || "1000", 10),
    tier_max: parseInt($("#sg_tier_max").value || "0", 10),    // 0 = nekonečno
  };
  if (enable !== undefined) body.enabled = enable;
  try {
    await api("/admin/sub-goal", { method: "POST", body });
    toast(enable === 1 ? "SUB cíl zapnut ▶️" : enable === 0 ? "SUB cíl vypnut ⏸️" : "Uloženo 💾", "success");
    adminDrops();
  } catch (e) { toast(e.message, "error"); }
}
function autoDropCardHTML(a) {
  const on = !!a.autodrop_enabled;
  const rng = (lo, hi) => (hi > lo ? `${lo}–${hi}` : `${lo}`);            // „20–40" / „30"
  const iMin = a.autodrop_interval_min, iMax = Math.max(a.autodrop_interval_max || iMin, iMin);
  const pMin = a.autodrop_points, pMax = Math.max(a.autodrop_points_max || pMin, pMin);
  const wMin = a.autodrop_winners, wMax = Math.max(a.autodrop_winners_max || wMin, wMin);
  const nextIn = a.next_interval > 0 ? ` Příští drop ~za <b>${a.next_interval} min</b>.` : "";
  return `<div class="panel" style="margin-bottom:18px;border-color:${on ? "var(--accent)" : "var(--border)"}">
    <div class="row-between"><div class="section-title" style="margin:0">⏰ Auto-drop (spouští se sám)</div>
      <span class="badge ${on ? "badge-sub" : ""}">${on ? "🟢 ZAPNUTO" : "⚫ vypnuto"}</span></div>
    <p class="form-hint" style="margin:8px 0 12px">Web sám spustí drop v <b>náhodném</b> intervalu <b>${rng(iMin, iMax)} min</b>${a.autodrop_only_live ? " (jen když jsi 🔴 LIVE)" : ""}, pokaždé s náhodnými <b>${rng(pMin, pMax)}</b> body a <b>${rng(wMin, wMax)}</b> výherci — diváci to nenačasují. 🎲 Bot ho oznámí v chatu, nestackuje.${nextIn}</p>
    <div class="field-row">
      <div class="field"><label>Interval OD (min)</label><input class="input" id="ad_interval" type="number" min="1" value="${iMin}"></div>
      <div class="field"><label>Interval DO (min)</label><input class="input" id="ad_interval_max" type="number" min="1" value="${iMax}"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>Body OD</label><input class="input" id="ad_points" type="number" min="1" value="${pMin}"></div>
      <div class="field"><label>Body DO</label><input class="input" id="ad_points_max" type="number" min="1" value="${pMax}"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>Výherců OD</label><input class="input" id="ad_winners" type="number" min="1" value="${wMin}"></div>
      <div class="field"><label>Výherců DO</label><input class="input" id="ad_winners_max" type="number" min="1" value="${wMax}"></div>
    </div>
    <label class="check"><input type="checkbox" id="ad_live" ${a.autodrop_only_live ? "checked" : ""}> Jen když je stream 🔴 LIVE</label>
    <div class="form-hint" style="margin:6px 0 0;font-size:12px">💡 Nech „DO” = „OD” a hodnota bude fixní (bez náhody).</div>
    <div class="toolbar" style="margin-top:12px">
      <button class="btn ${on ? "btn-danger" : "btn-primary"}" data-action="autodrop-toggle" data-on="${on ? 1 : 0}">${on ? "⏸️ Vypnout auto-drop" : "▶️ Zapnout auto-drop"}</button>
      <button class="btn btn-sm" data-action="autodrop-save">💾 Uložit nastavení</button>
    </div>
  </div>`;
}
async function saveAutoDrop(enable) {
  const body = {
    autodrop_interval_min: parseInt($("#ad_interval").value || "20", 10),
    autodrop_interval_max: parseInt($("#ad_interval_max").value || "40", 10),
    autodrop_points: parseInt($("#ad_points").value || "300", 10),
    autodrop_points_max: parseInt($("#ad_points_max").value || "800", 10),
    autodrop_winners: parseInt($("#ad_winners").value || "3", 10),
    autodrop_winners_max: parseInt($("#ad_winners_max").value || "7", 10),
    autodrop_only_live: $("#ad_live").checked ? 1 : 0,
  };
  if (enable !== undefined) body.autodrop_enabled = enable;
  try {
    await api("/admin/drops/auto", { method: "POST", body });
    toast(enable === 1 ? "Auto-drop zapnut ▶️" : enable === 0 ? "Auto-drop vypnut ⏸️" : "Nastavení uloženo 💾", "success");
    adminDrops();
  } catch (e) { toast(e.message, "error"); }
}
async function adminDrops() {
  const box = $("#adminContent");
  try {
    const [list, auto, happy, sgoal] = await Promise.all([api("/admin/drops"), api("/admin/drops/auto"), api("/admin/live-happy"), api("/sub-goal")]);
    box.innerHTML = happyHourCardHTML(happy) + subGoalCardHTML(sgoal) + autoDropCardHTML(auto) + `
      <div class="panel" style="margin-bottom:18px">
        <div class="section-title">🎁 Spustit drop (závod o kód)</div>
        <form class="form" data-submit="create-drop">
          <div class="field-row">
            <div class="field"><label>Body pro výherce</label><input class="input" id="dr_points" type="number" min="1" value="100"></div>
            <div class="field"><label>Počet nejrychlejších výherců</label><input class="input" id="dr_winners" type="number" min="1" value="1"></div>
            <div class="field"><label>Vlastní kód (volitelné)</label><input class="input" id="dr_code" placeholder="prázdné = vygeneruje" style="text-transform:uppercase"></div>
          </div>
          <button class="btn btn-primary" type="submit">🚀 Spustit drop</button>
        </form>
        <div class="form-hint" style="margin-top:8px">Po spuštění dostaneš kód → napiš ho do Kick chatu. Diváci ho zadají na webu a nejrychlejší získají body.</div>
      </div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Kód</th><th>Body</th><th>Výherci</th><th>Stav</th><th></th></tr></thead><tbody>
      ${list.length ? list.map((d) => `<tr>
        <td><span class="code-pill">${esc(d.code)}</span></td>
        <td><b>${fmtPts(d.points)}</b></td>
        <td>${d.winners.length}/${d.max_winners}${d.winners.length ? `<br><span class="faint" style="font-size:12px">${d.winners.map((w) => `#${w.position} ${esc(w.username)}`).join(", ")}</span>` : ""}</td>
        <td>${d.active ? `<span class="tag-done">🔴 LIVE</span>` : `<span class="faint">ukončen</span>`}</td>
        <td>${d.active ? `<button class="btn btn-danger btn-sm" data-action="end-drop" data-id="${d.id}">Ukončit</button>` : ""}</td>
      </tr>`).join("") : `<tr><td colspan="5"><div class="empty">Zatím žádné dropy.</div></td></tr>`}
      </tbody></table></div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function createDrop() {
  const body = { points: parseInt($("#dr_points").value || "0", 10), max_winners: parseInt($("#dr_winners").value || "1", 10), code: $("#dr_code").value.trim() || null };
  if (!body.points) { toast("Zadej body pro výherce.", "error"); return; }
  try {
    const r = await api("/admin/drops", { method: "POST", body });
    const posted = r.bot && r.bot.sent && r.bot.real;
    const head = posted
      ? `<div style="color:#46d369;font-weight:800;margin:10px 0 4px">✅ Bot už kód napsal do chatu!</div><p class="muted" style="margin:2px 0 10px;font-size:13px">Kód (pro kontrolu):</p>`
      : `<p class="muted" style="margin:10px 0">⚠️ Bot kód neposlal — napiš ho do chatu ručně:</p>`;
    const errLine = (!posted && r.bot && r.bot.error)
      ? `<div class="faint" style="font-size:11px;margin-top:8px;color:#e07a7a">Důvod: ${esc(String(r.bot.error).slice(0, 140))}</div>` : "";
    openModal(`<div class="modal-body" style="text-align:center">
      <div style="font-size:54px">🎁</div><h2>Drop spuštěn!</h2>
      ${head}
      <div class="code-pill" style="font-size:24px;display:inline-block;padding:10px 18px">${esc(r.code)}</div>
      <div class="muted" style="margin-top:14px">${fmtPts(r.points)} pro ${r.max_winners} nejrychlejších.</div>
      ${errLine}
      <button class="btn btn-primary" data-action="close-modal" style="margin-top:18px">OK</button></div>`);
    toast(posted ? "Drop je živý — bot poslal kód! 🔴" : "Drop je živý! 🔴", "success");
    adminDrops();
  } catch (e) { toast(e.message, "error"); }
}
async function endDrop(id) {
  try { await api(`/admin/drops/${id}/end`, { method: "POST" }); toast("Drop ukončen.", "info"); adminDrops(); }
  catch (e) { toast(e.message, "error"); }
}

/* ============================================================
   MODAL
============================================================ */
function openModal(html, extraClass = "") {
  $("#modalRoot").innerHTML = `<div class="modal-backdrop" data-action="close-modal"></div><div class="modal ${extraClass}"><button class="modal-close" data-action="close-modal">✕</button>${html}</div>`;
  $("#modalRoot").classList.add("open");
}
function closeModal() { $("#modalRoot").classList.remove("open"); $("#modalRoot").innerHTML = ""; }

/* Uvítací průvodce – „jak to funguje" (1× automaticky přes localStorage, znovu z patičky) */
function welcomeGuide() {
  const u = state.user;
  const steps = [
    ["📺", "Sleduj stream", "Za sledování Zurys streamu ti automaticky naskakují <b>sedláci</b> 🌾 — stačí mít web otevřený."],
    ["🛒", "Utrať v Shopu", "Sedláky vyměníš za <b>skiny a odměny</b> — instantní odměny i tomboly o velké ceny."],
    ["🎟️", "Plň Battle Pass", "Vše co naděláš tě posouvá v <b>sezónním Battle Passu</b> — denní bonus, kolo štěstí i odměny za tiery."],
    ["🎮", "Hraj a sázej", "Miny, duely, blackjack, predikce — zariskuj o <b>velké výhry</b> (férově, provably-fair)."],
    ["🏆", "Stoupej výš", "Farmařením rosteš v <b>levelu</b> a šplháš v žebříčku. Staň se #1 sedlákem! 👑"],
  ];
  const cards = steps.map(([ic, t, d]) => `<div class="wg-step"><span class="wg-ico">${ic}</span><div><b>${t}</b><div class="wg-d">${d}</div></div></div>`).join("");
  const cta = u
    ? `<button class="btn btn-primary btn-block" data-action="close-modal">Pojď farmařit! 🌾</button>`
    : `<button class="btn btn-kick btn-block" data-action="connect">🟢 Připoj se přes Kick a začni</button>`;
  openModal(`<div class="wg">
    <div class="wg-head"><img class="wg-mascot" src="/sedlak-cut.png" alt="">
      <div><h2 class="wg-title">Vítej na <span style="color:var(--accent)">Zurys farmě</span>! 🌾</h2>
      <p class="wg-sub">${u ? "Sbírej sedláky, ozdob si farmu a vyšplhej na vrchol." : "Sbírej sedláky za sledování streamu a vyměň je za skiny a odměny."}</p></div></div>
    <div class="wg-steps">${cards}</div>
    ${cta}
  </div>`, "modal-wg");
}

/* ============================================================
   EVENT DELEGACE
============================================================ */
function handleAction(action, el) {
  const id = el.dataset.id ? parseInt(el.dataset.id, 10) : null;
  switch (action) {
    case "nav": navigate(el.dataset.href); break;
    case "connect": openConnect(); break;
    case "spin-wheel": doSpinWheel(); break;
    case "mines-start": minesStart(); break;
    case "mines-reveal": minesReveal(parseInt(el.dataset.tile, 10)); break;
    case "mines-cashout": minesCashout(); break;
    case "mines-new": pageMines(); break;
    case "bio-edit": editBio(); break;
    case "bio-save": saveBio(); break;
    case "bio-cancel": loadMyBio(); break;
    case "prestige-buy": prestigeBuy(parseInt(el.dataset.cost, 10), parseInt(el.dataset.lvl, 10)); break;
    case "wl-save": saveWagerLimit(); break;
    case "bp-claim": claimBpTier(el.dataset.tier); break;
    case "bp-claim-premium": claimBpTier(el.dataset.tier, true); break;
    case "lp-claim": claimLevelPass(el.dataset.level); break;
    case "user-sort": setUserSort(el.dataset.sort); break;
    case "bp-daily": claimBpDaily(); break;
    case "grd-pick": grdPick(el.dataset.crop); break;
    case "grd-plant": grdPlant(el.dataset.plot); break;
    case "grd-plant-all": grdPlantAll(); break;
    case "grd-harvest": grdHarvest(el.dataset.plot); break;
    case "grd-harvest-all": grdHarvestAll(); break;
    case "grd-rescue": grdRescue(el.dataset.plot); break;
    case "decor-buy": buyDecor(el.dataset.key); break;
    case "claim-partner": claimPartnerLink(el.dataset.id, el.dataset.url); break;
    case "claim-quest": claimQuest(el.dataset.key); break;
    case "cos-buy": buyCosmetic(el.dataset.key); break;
    case "cos-equip": equipCosmetic(el.dataset.key); break;
    case "dm-send": dmSend(el.dataset.mode, el.dataset.id); break;
    case "shop-disc-save": saveShopDiscount(); break;
    case "ban-cluster": banCluster(el.dataset.ids, el.dataset.label); break;
    case "gift-approve": giftDecide(el.dataset.id, true, el.dataset.label); break;
    case "gift-reject": giftDecide(el.dataset.id, false, el.dataset.label); break;
    case "fair-rotate": fairRotate(); break;
    case "fair-verify": fairVerify(); break;
    case "self-excl": selfExclude(el.dataset.dur); break;
    case "maint-on": doMaintOn(el); break;
    case "maint-on-time": doMaintOnTime(); break;
    case "maint-off": doMaintOff(); break;
    case "maint-extend": doMaintExtend(el); break;
    case "toggle-mobile": $("#mobilenav").classList.toggle("open"); break;
    case "close-drawer": closeDrawer(); break;
    case "logout": doLogout(); break;
    case "filter-type": shopState.type = el.dataset.type; shopState.page = 1; pageShop(); break;
    case "toggle-subs": shopState.subs = !shopState.subs; shopState.page = 1; pageShop(); break;
    case "toggle-afford": shopState.afford = !shopState.afford; renderFilters(); renderGrid(); break;
    case "sort-price": shopState.sort = shopState.sort === "price_asc" ? "price_desc" : "price_asc"; renderFilters(); renderGrid(); break;
    case "toggle-vip": shopState.vip = !shopState.vip; shopState.page = 1; pageShop(); break;
    case "load-more": shopState.page++; loadProducts(false); break;
    case "row-scroll": { const t = document.getElementById(el.dataset.target); if (t) t.scrollBy({ left: parseInt(el.dataset.dir, 10) * 460, behavior: "smooth" }); break; }
    case "open-product": openProduct(id); break;
    case "buy": buyProduct(id); break;
    case "buy-confirm": confirmBuyModal(id); break;
    case "add-cart": {
      const p = shopState.items.find((x) => x.id === id) || adminState.products.find((x) => x.id === id);
      if (p) { addToCart(p); toast("Přidáno do košíku 🛒", "success"); } else { api("/shop/products/" + id).then((pp) => { addToCart(pp); toast("Přidáno do košíku 🛒", "success"); }); }
      break;
    }
    case "qty": changeQty(id, parseInt(el.dataset.d, 10)); pageCart(); break;
    case "cart-remove": removeFromCart(id); pageCart(); break;
    case "cart-clear": clearCart(); pageCart(); break;
    case "checkout": doCheckout(); break;
    case "acc-toggle": { const item = document.querySelector(`.acc-item[data-acc="${el.dataset.i}"]`); item.classList.toggle("open"); break; }
    case "prof-tab": loadProfTab(el.dataset.tab); break;
    case "save-trade": saveTradeUrl(); break;
    case "copy-trade": navigator.clipboard && navigator.clipboard.writeText(el.dataset.url).then(() => toast("Trade link zkopírován ✓", "success")); break;
    case "copy-url": navigator.clipboard && navigator.clipboard.writeText(el.dataset.url).then(() => toast("Odkaz zkopírován ✓", "success")); break;
    case "close-modal": closeModal(); break;
    /* hry (piškvorky) */
    case "game-open": navigate("games/" + id); break;
    case "game-join": joinGame(id); break;
    case "game-move": makeMove(el.dataset.id, el.dataset.cell); break;
    case "game-cancel": cancelGame(id); break;
    case "game-claim": claimTimeout(id); break;
    case "duel-type": window._duelType = el.dataset.t; renderDuelLobby(); break;
    case "duel-create": createDuel(); break;
    case "duel-join": joinDuel(id); break;
    case "duel-cancel": cancelDuel(id); break;
    case "bjr-create": bjrCreate(); break;
    case "bjr-join": bjrJoin(); break;
    case "bjr-bet": bjrBet(); break;
    case "bjr-deal": bjrAct("deal"); break;
    case "bjr-hit": bjrAct("hit"); break;
    case "bjr-stand": bjrAct("stand"); break;
    case "bjr-double": bjrAct("double"); break;
    case "bjr-split": bjrAct("split"); break;
    case "my-wrapped": showWrapped(); break;
    case "bjr-next": bjrAct("next"); break;
    case "bjr-leave": bjrLeave(); break;
    case "bjr-chat": bjrChat(); break;
    case "bjr-copy": bjrCopy(el.dataset.code); break;
    case "bjr-chip": bjrChip(el.dataset.amt); break;
    case "bjr-mute": bjrMute(); break;
    case "game-back": navigate("games"); break;
    case "open-news": openNewsPanel(); break;
    case "open-welcome": welcomeGuide(); break;
    case "close-news": closeNewsPanel(); break;
    case "open-notifs": openNotifs(); break;
    case "close-notifs": closeNotifs(); break;
    case "notif-go": closeNotifs(); if (el.dataset.link) location.hash = el.dataset.link; break;
    /* admin */
    case "admin-tab": renderAdminTab(el.dataset.tab); break;
    case "modapp-accept": modAppDecide(el.dataset.id, true, el.dataset.name); break;
    case "modapp-reject": modAppDecide(el.dataset.id, false, el.dataset.name); break;
    case "modapp-toggle": modAppToggle(); break;
    case "product-new": productForm(null); break;
    case "product-edit": { const p = adminState.products.find((x) => x.id === id); productForm(p); break; }
    case "product-delete": deleteProduct(id); break;
    case "skin-lookup": lookupSkinImage(); break;
    case "skin-picker": toggleSkinPicker(); break;
    case "upload-image": uploadImageClick(); break;
    case "coin-upload": coinUploadClick(); break;
    case "skin-search": searchSkins(); break;
    case "skin-pick": pickSkin(el); break;
    case "user-points": changeUserPoints(id, parseInt(el.dataset.sign, 10), el.dataset.name); break;
    case "pts-confirm": confirmUserPoints(el); break;
    case "user-flag-toggle": toggleUserFlag(el); break;
    case "user-watch": toggleUserWatch(el); break;
    case "user-note": openUserNote(el); break;
    case "user-note-save": saveUserNote(id); break;
    case "overview-refresh": adminOverview(); break;
    case "order-filter": adminState.orderFilter = el.dataset.f; adminOrders(); break;
    case "order-fulfill": fulfillOrder(id); break;
    case "order-delete": deleteOrder(id); break;
    case "orders-clear-fulfilled": clearFulfilledOrders(); break;
    case "orders-fulfill-all": fulfillAllOrders(); break;
    case "order-manual-add": openManualOrder(); break;
    case "order-manual-submit": submitManualOrder(); break;
    case "order-bulk-submit": submitBulkOrders(); break;
    case "mo-mode": moMode(el.dataset.mode); break;
    case "mo-pick-user": moPickUser(el); break;
    case "user-ticket": openManualOrder(el.dataset.name); break;
    case "game-admin-cancel": cancelGameAdmin(id); break;
    case "game-refund": refundGame(el.dataset.kind, parseInt(el.dataset.id, 10)); break;
    case "topchat-pay": payTopchatter(); break;
    case "games-refresh": adminGames(); break;
    case "mines-hist-filter": loadMinesHistory(($("#minesHistQ").value || "").trim()); break;
    case "mines-hist-reset": loadMinesHistory(""); break;
    case "mines-ban-add": minesBanAdd(); break;
    case "mines-unban": minesUnban(el.dataset.username); break;
    case "subs-refresh": adminSubs(); break;
    case "raffle-draw": drawRaffle(id); break;
    case "raffle-undo": undoRaffleDraw(id); break;
    case "pred-bet": predBet(parseInt(el.dataset.pid, 10), parseInt(el.dataset.oid, 10)); break;
    case "pred-lock": predLock(parseInt(el.dataset.pid, 10)); break;
    case "pred-unlock": predUnlock(parseInt(el.dataset.pid, 10)); break;
    case "pred-resolve": predResolve(parseInt(el.dataset.pid, 10), parseInt(el.dataset.oid, 10)); break;
    case "pred-reresolve": predReresolve(parseInt(el.dataset.pid, 10), parseInt(el.dataset.oid, 10)); break;
    case "pred-cancel": predCancel(parseInt(el.dataset.pid, 10)); break;
    case "code-delete": deleteCode(id); break;
    case "ban-user": banUser(id, true); break;
    case "unban-user": banUser(id, false); break;
    case "end-drop": endDrop(id); break;
    case "autodrop-save": saveAutoDrop(); break;
    case "autodrop-toggle": saveAutoDrop(el.dataset.on === "1" ? 0 : 1); break;
    case "happy-save": saveHappyHour(); break;
    case "happy-start": startHappyNow(); break;
    case "happy-stop": stopHappyNow(); break;
    case "happy-toggle": saveHappyHour(el.dataset.on === "1" ? 0 : 1); break;
    case "subgoal-save": saveSubGoal(); break;
    case "subgoal-toggle": saveSubGoal(el.dataset.on === "1" ? 0 : 1); break;
    case "news-delete": deleteNote(id); break;
    case "broadcast-send": sendBroadcast(); break;
    case "garden-push-on": enableGardenPush(); break;
    case "garden-push-off": disableGardenPush(); break;
    case "rule-toggle": toggleRule(el.dataset.key, el.dataset.on !== "1"); break;
    case "ip-unban": unbanIp(el.dataset.ip); break;
    case "ddos-autoban": toggleAutoban(el.dataset.on !== "1"); break;
    case "ban-ip-quick": quickBanIp(el.dataset.ip); break;
    case "sec-refresh": adminSecurity(); break;
    case "sec-tab":
      adminState.secTab = el.dataset.tab;
      document.querySelectorAll(".sec-pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === adminState.secTab));
      document.querySelectorAll(".sec-pill").forEach((p) => p.classList.toggle("active", p.dataset.tab === adminState.secTab));
      break;
    case "pf-filter":
      adminState.pfQuery = ($("#pfQuery")?.value || "").trim();
      adminState.pfFlow = $("#pfFlow")?.value || "";
      adminState.pfMin = parseInt($("#pfMin")?.value || "0", 10) || 0;
      adminState.pfReason = ($("#pfReason")?.value || "").trim();
      adminState.pfOffset = 0; adminState.secTab = "points";
      adminSecurity(); break;
    case "pf-reset":
      adminState.pfQuery = ""; adminState.pfFlow = ""; adminState.pfMin = 0; adminState.pfReason = ""; adminState.pfOffset = 0;
      adminState.secTab = "points"; adminSecurity(); break;
    case "pf-page":
      adminState.pfOffset = Math.max(0, adminState.pfOffset + (el.dataset.dir === "next" ? adminState.pfLimit : -adminState.pfLimit));
      adminState.secTab = "points"; adminSecurity(); break;
    case "bot-connect-real": window.location.href = "/api/auth/kick/bot/login"; break;
    case "bot-demo-connect": botDemoConnect(); break;
    case "bot-disconnect": botDisconnect(); break;
    case "bot-auto-post": botToggleAutoPost(el.dataset.on !== "1"); break;
    case "bot-subscribe-events": botSubscribeEvents(); break;
    case "bot-sim-chat": botSimChat(); break;
    case "eco-save": saveEconomy(); break;
    case "games-rake-save": saveGamesRake(); break;
    case "eco-toggle": el.classList.toggle("on"); break;
    case "eco-live-mode": setLiveMode(el.dataset.mode); break;
    case "eco-live-refresh": adminEconomy(); break;
    case "pl-add": addPartnerLink(); break;
    case "pl-save": savePartnerLink(el.dataset.id); break;
    case "pl-toggle": togglePartnerLink(el.dataset.id, el.dataset.on === "1"); break;
    case "pl-mode": modePartnerLink(el.dataset.id, el.dataset.mode); break;
    case "pl-del": delPartnerLink(el.dataset.id); break;
    case "pf-save": savePartnerFlash(); break;
    case "pf-trigger": triggerPartnerFlash(); break;
    case "copy-code": navigator.clipboard && navigator.clipboard.writeText(el.dataset.code).then(() => toast("Kód zkopírován: " + el.dataset.code, "success")); break;
    case "audit-filter": {
      adminState.auditAction = ($("#auditAction")?.value || "").trim();
      adminState.auditAdmin = ($("#auditAdmin")?.value || "").trim();
      adminState.auditOffset = 0; adminSecurity(); break;
    }
    case "audit-reset":
      adminState.auditAction = ""; adminState.auditAdmin = ""; adminState.auditOffset = 0;
      adminSecurity(); break;
    case "audit-page":
      adminState.auditOffset = Math.max(0, adminState.auditOffset
        + (el.dataset.dir === "next" ? adminState.auditLimit : -adminState.auditLimit));
      adminSecurity(); break;
    case "logins-filter": {
      adminState.loginsQuery = ($("#loginsQuery")?.value || "").trim();
      adminState.loginsIp = ($("#loginsIp")?.value || "").trim();
      adminState.loginsOffset = 0; adminSecurity(); break;
    }
    case "logins-reset":
      adminState.loginsQuery = ""; adminState.loginsIp = ""; adminState.loginsOffset = 0;
      adminSecurity(); break;
    case "logins-page":
      adminState.loginsOffset = Math.max(0, adminState.loginsOffset
        + (el.dataset.dir === "next" ? adminState.loginsLimit : -adminState.loginsLimit));
      adminSecurity(); break;
  }
}

document.addEventListener("click", (e) => {
  const actEl = e.target.closest("[data-action]");
  if (!actEl) return;  // běžné odkazy (#/...) projdou přirozeně přes hashchange
  e.preventDefault();
  handleAction(actEl.dataset.action, actEl);
});

// (easter egg „tajný klas" zrušen 25.6.2026)

/* Service worker pro Web Push (notifikace do mobilu). Registruje se 1× na pozadí. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}

document.addEventListener("change", (e) => {
  const op = e.target.closest('[data-action="order-product-filter"]');
  if (op) { adminState.orderProduct = op.value || null; adminOrders(); return; }
  const sel = e.target.closest('[data-action="user-role"]');
  if (sel) { setUserRole(parseInt(sel.dataset.id, 10), sel.value); return; }
  const flag = e.target.closest('[data-action="user-flag"]');
  if (flag) { setUserFlag(parseInt(flag.dataset.id, 10), flag.dataset.flag, flag.checked); return; }
  const gb = e.target.closest('[data-action="user-gamble"]');
  if (gb) { if (gb.value) adminGambleBlock(parseInt(gb.dataset.id, 10), gb.value, gb.dataset.name); gb.value = ""; return; }
  const to = e.target.closest('[data-action="user-timeout"]');
  if (to) { if (to.value) adminTimeout(parseInt(to.dataset.id, 10), to.value, to.dataset.name); to.value = ""; }
});

async function adminGambleBlock(id, dur, name) {
  const lbl = { "1d": "1 den", "7d": "7 dní", "30d": "30 dní", "perm": "napořád", "off": "ODEMKNOUT" }[dur];
  const msg = dur === "off" ? `Odemknout sázení uživateli ${name}?` : `Zablokovat sázení (${lbl}) uživateli ${name}?`;
  if (!confirm(msg)) return;
  try {
    await api(`/admin/users/${id}/gamble-block`, { method: "POST", body: { duration: dur } });
    toast(dur === "off" ? "Sázení odemčeno" : `Sázení zablokováno (${lbl}) 🔒`, "success");
  } catch (e) { toast(e.message, "error"); }
}

async function adminTimeout(id, dur, name) {
  const lbl = { "5m": "5 min", "15m": "15 min", "1h": "1 hodina", "6h": "6 hodin", "24h": "24 hodin", "7d": "7 dní", "off": "ZRUŠIT" }[dur];
  const msg = dur === "off"
    ? `Zrušit timeout uživateli ${name}?`
    : `Dát timeout (${lbl}) uživateli ${name}?\n\nPo celou dobu nebude moct používat web (žádné farmění/sázky/shop) ani psát do Kick chatu.`;
  if (!confirm(msg)) return;
  try {
    const r = await api(`/admin/users/${id}/timeout`, { method: "POST", body: { duration: dur } });
    const k = r.kick || {};
    const kickNote = k.ok ? " + Kick chat" : (k.skipped ? " (bez Kicku)" : " (Kick chat selhal)");
    toast(dur === "off" ? "Timeout zrušen" : `Timeout ${lbl} ⏳${kickNote}`, "success");
    adminUsers();
  } catch (e) { toast(e.message, "error"); }
}

// Po návratu na záložku hned dotáhni stav hry (jinak by se čekalo na další tik pollingu)
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && gameTimer && currentGameId) refreshGame(currentGameId);
});

document.addEventListener("submit", (e) => {
  const form = e.target.closest("[data-submit]");
  if (!form) return;
  e.preventDefault();
  const map = { "kick-connect": doKickConnect, redeem: doRedeem, "claim-drop": doClaimDrop, "save-product": saveProduct, "user-search": () => { adminState.userQuery = $("#userSearch").value.trim(); adminUsers(); }, "gen-code": genCodes, "create-drop": createDrop, "bot-send": botSend, "bot-sim-chat": botSimChat, "game-create": createGame, "ip-ban": banIp, "pred-create": createPrediction, "gift": doGift, "news-create": createNote, "mod-apply": doModApply };
  const fn = map[form.dataset.submit]; if (fn) fn();
});

/* ============================================================
   HRY – piškvorky 1v1 o body
============================================================ */
async function refreshMe() {
  try { const r = await api("/auth/me"); if (r.user) { state.user = r.user; renderHeader(); } } catch (e) {}
}

/* ---------------- 🃏 Karta (sdílí ji multiplayer stůl) ---------------- */
function bjCard(code, cls, idx) {
  const a = cls ? (" " + cls) : "";
  const v = (cls && idx != null) ? (";--i:" + idx) : "";
  const base = "display:inline-flex;align-items:center;justify-content:center;min-width:40px;height:56px;padding:0 7px;border-radius:7px;font-weight:700;font-size:17px;box-shadow:0 2px 6px rgba(0,0,0,.35)";
  if (!code || code === "??") return `<span class="bjc${a}" style="${base};background:linear-gradient(135deg,#3a2d6e,#281f4d);border:1px solid #5a4db0${v}"></span>`;
  const rank = code[0] === "T" ? "10" : code[0];
  const sym = ({ S: "♠", H: "♥", D: "♦", C: "♣" })[code[1]] || "?";
  const red = code[1] === "H" || code[1] === "D";
  return `<span class="bjc${a}" style="${base};background:#fff;color:${red ? "#d4233a" : "#1a1a2e"}${v}"><b style="margin-right:1px">${rank}</b>${sym}</span>`;
}

/* ---------------- 🃏 Soukromý sdílený stůl (multiplayer + chat) ---------------- */
let _bjRoom = null, _bjRoomId = null, _bjPoll = null;
/* 🃏 BlackJack „juice" (Tier 1): animace-na-diff + zvuk. Reuse beep()/confettiBurst()/audioCtx()/sndWin/sndLose (def níž, hoisted). */
let _bjAnim = { round: -1, dealerLen: 0, dealerHidden: true, youActing: false, seats: {} };
let _bjMuted = (() => { try { return localStorage.getItem("bj_muted") === "1"; } catch (e) { return false; } })();
let _bjTick = null, _bjSkew = 0, _bjCount = null;   // auto-flow odpočet (Tier 2): tiká lokálně mezi polly
function _bjSnd(fn) { if (!_bjMuted) try { fn(); } catch (e) {} }
function sndBjDeal() { beep(740, 0.04, "square", 0, 0.045); }
function sndBjFlip() { beep(520, 0.07, "triangle", 0, 0.10); beep(360, 0.10, "sine", 0.05, 0.08); }
function sndBjBlackjack() { [659, 880, 1047, 1319, 1568].forEach((f, i) => beep(f, 0.18, "triangle", i * 0.08, 0.16)); }
function sndBjDing() { beep(1175, 0.12, "triangle", 0, 0.13); }
function _bjAnimReset() { _bjAnim = { round: -1, dealerLen: 0, dealerHidden: true, youActing: false, seats: {} }; _bjCount = null; }
function _bjStopTimers() { if (_bjPoll) { clearTimeout(_bjPoll); _bjPoll = null; } if (_bjTick) { clearInterval(_bjTick); _bjTick = null; } }
function _bjSchedule() { if (_bjPoll) clearTimeout(_bjPoll); _bjPoll = setTimeout(bjPollRoom, (_bjRoom && _bjRoom.can_act) ? 900 : 2200); }
function bjTickCountdown() {
  const el = document.getElementById("bjCountdown");
  if (!el) return;
  if (!_bjCount) { el.style.display = "none"; return; }
  const rem = Math.max(0, Math.round((_bjCount.target - (Date.now() + _bjSkew)) / 1000));
  el.style.display = "";
  el.innerHTML = `${_bjCount.label} <b>${rem}s</b>`;
}
/* hand s animací: animFrom = index od kterého jsou karty NOVÉ (bj-deal); flipIdx = která se otočí (reveal hole-karty) */
function _bjHand(hand, animFrom, flipIdx) {
  hand = hand || [];
  if (!hand.length) return '<span class="faint" style="font-size:12px">—</span>';
  return hand.map((c, i) => {
    if (flipIdx != null && i === flipIdx) return bjCard(c, "bj-flip", 0);
    if (animFrom != null && i >= animFrom) return bjCard(c, "bj-deal", i - animFrom);
    return bjCard(c);
  }).join("");
}
async function pageBjRoom(param) {
  if (!state.user) { navigate("connect"); return; }
  _bjStopTimers();
  _bjAnimReset();
  $("#view").innerHTML = `<div class="page-head"><h1>🃏 Soukromý stůl</h1><p class="muted">Blackjack mezi kamarády — jen na pozvánku. 🔒</p></div><div id="bjRoomWrap">${skeletonCards(1)}</div>`;
  _bjRoom = null; _bjRoomId = null;
  try {
    if (param && /^BJ/i.test(param)) {
      const st = await api("/blackjack/room/join", { method: "POST", body: { code: param.toUpperCase() } });
      _bjRoom = st; _bjRoomId = st.room_id;
    } else if (param && /^\d+$/.test(param)) {
      _bjRoomId = parseInt(param, 10);
      _bjRoom = await api("/blackjack/room/" + _bjRoomId + "/state");
    } else {
      const mine = await api("/blackjack/room/mine");
      if (mine.room_id) { _bjRoomId = mine.room_id; _bjRoom = await api("/blackjack/room/" + _bjRoomId + "/state"); }
    }
  } catch (e) { toast(e.message, "error"); _bjRoom = null; _bjRoomId = null; }
  renderBjRoom();
  if (_bjRoomId) { _bjSchedule(); _bjTick = setInterval(bjTickCountdown, 500); }
}
async function bjPollRoom() {
  if (_bjPoll) { clearTimeout(_bjPoll); _bjPoll = null; }
  if (!document.getElementById("bjRoomWrap")) { _bjStopTimers(); return; }   // odešel ze stránky → stop
  if (_bjRoomId && !document.hidden) {
    // granulární update – vstupní pole (sázka/chat) se nepřepisují, takže psaní/focus drží
    try { _bjRoom = await api("/blackjack/room/" + _bjRoomId + "/state"); renderBjRoom(); } catch (e) {}
  }
  _bjSchedule();   // adaptivně: tvůj tah → 900 ms, jinak 2200 ms
}
function _bjHandStatus(state, result, payout, bet, g) {
  if (g.status === "betting") return state === "ready" ? `vsadil ${fmtPts(bet)} ✔` : '<span class="faint">čeká na sázku…</span>';
  if (state === "resolved" || g.status === "done") {
    const rmap = { blackjack: "🃏 BLACKJACK!", win: "✅ výhra", push: "🤝 remíza", lose: "❌ prohra", bust: "💥 přebral" };
    const col = (result === "win" || result === "blackjack") ? "#39d98a" : (result === "push" ? "var(--text)" : "#ff6b6b");
    const net = (payout || 0) - (bet || 0);
    return result ? `<span style="color:${col};font-weight:600">${rmap[result] || result} ${net >= 0 ? "+" : ""}${fmtPts(net)}</span>` : '<span class="faint">—</span>';
  }
  return state === "acting" ? "🎴 hraje…" : (state === "stood" ? "✋ stojí" : (state === "bust" ? "💥 přebral" : '<span class="faint">sedí</span>'));
}
function _bjHandBlock(cards, value, state, result, payout, bet, g, active, animFrom) {
  const ring = active ? "box-shadow:0 0 0 2px var(--gold);border-radius:9px;padding:5px" : "";
  return `<div style="${ring}">
    <div style="display:flex;gap:5px;flex-wrap:wrap;margin:6px 0;min-height:34px">${_bjHand(cards, animFrom, null)}</div>
    <div style="font-size:12.5px">${cards && cards.length ? "<b>" + value + "</b> · " : ""}${_bjHandStatus(state, result, payout, bet, g)}</div>
  </div>`;
}
function _bjSeat(s, g, hint) {
  const crown = s.is_host ? "👑 " : "";
  const tag = s.is_you ? ' · <span style="color:var(--accent)">TY</span>' : "";
  const onTurn = (s.state === "acting" || s.state2 === "acting") && g.status === "playing";
  const cls = "bj-spot" + (onTurn ? " turn" : (s.is_you ? " you" : "")) + (hint && hint.justWin ? " win" : "") + (hint && hint.justBust ? " bust" : "");
  const animFrom = hint && hint.animFrom != null ? hint.animFrom : null;
  let body;
  if (s.split) {
    const a1 = s.active_hand === 1 && s.state === "acting" && g.status === "playing";
    const a2 = s.active_hand === 2 && s.state2 === "acting" && g.status === "playing";
    body = `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
      <div style="flex:1;min-width:96px">${_bjHandBlock(s.hand, s.value, s.state, s.result, s.payout, s.bet, g, a1, animFrom)}</div>
      <div style="flex:1;min-width:96px">${_bjHandBlock(s.hand2, s.value2, s.state2, s.result2, s.payout2, s.bet2, g, a2, null)}</div>
    </div>`;
  } else {
    body = _bjHandBlock(s.hand, s.value, s.state, s.result, s.payout, s.bet, g, false, animFrom);
  }
  return `<div class="${cls}">
    <div style="font-weight:700;font-size:13px">${crown}${esc(s.username)}${tag} <span class="faint" style="font-weight:400">${s.bet ? "· " + fmtPts(s.bet) + (s.split ? " ×2" : "") : ""}</span></div>
    ${body}
  </div>`;
}
function renderBjRoom() {
  const wrap = document.getElementById("bjRoomWrap"); if (!wrap) return;
  const g = _bjRoom;
  if (!g || !g.room_id) {
    wrap.innerHTML = `<div class="panel">
      <div class="section-title" style="margin-top:0">🃏 Soukromý stůl</div>
      <p class="muted" style="font-size:12.5px">Založ stůl a pošli pozvaným <b>link</b>, nebo se připoj <b>kódem</b>. Není ve veřejné Herně — dovnitř jen s kódem. 🔒</p>
      ${(state.user && state.user.role === "admin") ? `<div class="toolbar" style="margin-top:10px"><button class="btn btn-accent" data-action="bjr-create">➕ Vytvořit stůl</button></div>` : ""}
      <div class="toolbar" style="margin-top:10px;gap:8px">
        <input class="input" id="bjrCode" placeholder="Kód stolu (BJ…)" maxlength="20" style="max-width:200px;text-transform:uppercase">
        <button class="btn btn-success" data-action="bjr-join">Připojit ⚔️</button>
      </div>
    </div>`;
    return;
  }
  // Kostra se postaví JEN jednou. Pak už jen aktualizujeme dynamické sekce → vstupní pole (sázka/chat)
  // se nikdy nepřepíšou, takže psaní i kurzor zůstanou (oprava bugu "nejde psát/sázet při pollingu").
  if (!document.getElementById("bjTable")) {
    wrap.innerHTML = `<div id="bjTable">
      <div class="panel" style="margin-bottom:12px"><div class="row-between" id="bjHeader" style="flex-wrap:wrap;gap:8px"></div></div>
      <div class="bj-felt">
        <div class="bj-rules"><div class="r1">BLACKJACK PAYS 3 TO 2</div><div class="r2">Dealer stojí na 17 · blackjack platí 3:2</div></div>
        <div id="bjCountdown" class="bj-countdown" style="display:none"></div>
        <div class="bj-dealer">
          <div class="bj-lbl" id="bjDealerLbl">DEALER</div>
          <div id="bjDealerCards" class="bj-cards-row"></div></div>
        <div id="bjSeats" class="bj-seats"></div>
      </div>
      <div class="panel" id="bjBetBar" style="margin-bottom:12px;display:none">
        <div class="toolbar" style="gap:6px;flex-wrap:wrap;align-items:center">
          <input class="input" id="bjrBet" type="number" min="10" max="2000" step="10" placeholder="Sázka 10–2000" style="max-width:150px">
          <button class="btn btn-sm btn-ghost" data-action="bjr-chip" data-amt="50">+50</button>
          <button class="btn btn-sm btn-ghost" data-action="bjr-chip" data-amt="100">+100</button>
          <button class="btn btn-sm btn-ghost" data-action="bjr-chip" data-amt="250">+250</button>
          <button class="btn btn-sm btn-ghost" data-action="bjr-chip" data-amt="500">+500</button>
          <button class="btn btn-sm btn-ghost" data-action="bjr-chip" data-amt="max">Max</button>
          <button class="btn btn-sm btn-ghost" data-action="bjr-chip" data-amt="clear">✖</button>
          <button class="btn btn-accent" data-action="bjr-bet">💰 Vsadit</button>
        </div></div>
      <div class="panel" id="bjActions" style="margin-bottom:12px;display:none"></div>
      <div class="panel"><div class="section-title" style="margin-top:0;font-size:14px">💬 Chat u stolu</div>
        <div id="bjrChatBox" style="max-height:160px;overflow:auto;font-size:13px;margin-bottom:8px;line-height:1.5"></div>
        <div class="toolbar" style="gap:6px"><input class="input" id="bjrChatMsg" maxlength="200" placeholder="Napiš zprávu… (Enter pošle) 💬" style="flex:1"><button class="btn btn-sm btn-accent" data-action="bjr-chat">Poslat</button></div></div>
    </div>`;
    const be = document.getElementById("bjrBet");
    if (be) be.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); bjrBet(); } });
    const ce = document.getElementById("bjrChatMsg");
    if (ce) ce.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); bjrChat(); } });
  }
  updateBjRoom(g);
}
function updateBjRoom(g) {
  if (g.round_no !== _bjAnim.round) _bjAnim = { round: g.round_no, dealerLen: 0, dealerHidden: true, youActing: false, seats: {} };
  const statusTxt = { betting: "💰 Sázení", playing: "🃏 Hra běží", done: "✅ Vyhodnoceno", closed: "Zavřeno" }[g.status] || g.status;
  const hdr = document.getElementById("bjHeader");
  if (hdr) hdr.innerHTML = `<div><b>Kód: <span style="color:var(--accent)">${esc(g.code)}</span></b> <span class="faint">· ${statusTxt} · kolo ${g.round_no}</span></div>
    <div class="toolbar" style="gap:6px"><button class="btn btn-sm btn-ghost" data-action="bjr-mute" title="Zvuk u stolu">${_bjMuted ? "🔇" : "🔊"}</button><button class="btn btn-sm btn-ghost" data-action="bjr-copy" data-code="${esc(g.code)}">📋 Link</button>
      <button class="btn btn-sm btn-danger" data-action="bjr-leave">Odejít</button></div>`;
  const dealerVal = g.dealer && g.dealer.length ? (g.dealer_hidden ? g.dealer_value + "+" : g.dealer_value) : "";
  const dl = document.getElementById("bjDealerLbl");
  if (dl) dl.textContent = "DEALER" + (dealerVal !== "" ? " (" + dealerVal + ")" : "");
  // DEALER diff: nové dobírané karty = bj-deal; otočení hole-karty při revealu = bj-flip
  const dealer = g.dealer || [];
  const prevDLen = _bjAnim.dealerLen, nowHidden = !!g.dealer_hidden;
  let flipIdx = null;
  if (_bjAnim.dealerHidden && !nowHidden && prevDLen > 0) flipIdx = prevDLen - 1;
  const dAnim = dealer.length > prevDLen ? prevDLen : null;
  const dc = document.getElementById("bjDealerCards");
  if (dc) dc.innerHTML = dealer.length ? _bjHand(dealer, dAnim, flipIdx) : '<span class="faint">—</span>';
  if (flipIdx != null) _bjSnd(sndBjFlip);
  else if (dAnim != null && dealer.length) _bjSnd(sndBjDeal);
  _bjAnim.dealerLen = dealer.length; _bjAnim.dealerHidden = nowHidden;
  // SEATY diff: nové karty = bj-deal; přechod na výsledek = glow/shake + zvuk/confetti (jen jednou, jen pro tebe)
  const se = document.getElementById("bjSeats");
  if (se) se.innerHTML = g.seats.map((s, i) => {
    const key = s.username || ("i" + i);
    const prev = _bjAnim.seats[key] || { len: 0, result: null };
    const hand = s.hand || [];
    const animFrom = hand.length > prev.len ? prev.len : null;
    const resultNow = (s.state === "resolved" || g.status === "done") ? (s.result || null) : null;
    const justResolved = !!resultNow && resultNow !== prev.result;
    const hint = {
      animFrom,
      justWin: justResolved && (resultNow === "win" || resultNow === "blackjack"),
      justBust: justResolved && (resultNow === "bust" || resultNow === "lose"),
    };
    if (justResolved && s.is_you) {
      if (resultNow === "blackjack") { _bjSnd(sndBjBlackjack); try { confettiBurst(); } catch (e) {} }
      else if (resultNow === "win") _bjSnd(sndWin);
      else if (resultNow === "bust" || resultNow === "lose") _bjSnd(sndLose);
    } else if (animFrom != null && s.is_you && g.status === "playing") _bjSnd(sndBjDeal);
    _bjAnim.seats[key] = { len: hand.length, result: resultNow };
    return _bjSeat(s, g, hint);
  }).join("");
  // „jsi na tahu" ding (jen na přechodu)
  const youAct = !!(g.you && g.you.state === "acting" && g.status === "playing");
  if (youAct && !_bjAnim.youActing) _bjSnd(sndBjDing);
  _bjAnim.youActing = youAct;
  // auto-flow odpočet: server pošle phase_until + server_now; přepočti na lokální čas a nech tikat
  _bjSkew = (g.server_now ? Date.parse(g.server_now) : Date.now()) - Date.now();
  const youSeat = (g.seats || []).find((s) => s.is_you);
  if (g.status === "betting" && g.phase_until) _bjCount = { target: Date.parse(g.phase_until), label: "🃏 Rozdání za" };
  else if (g.status === "done" && g.phase_until) _bjCount = { target: Date.parse(g.phase_until), label: "🔄 Nové kolo za" };
  else if (youAct && youSeat && youSeat.turn_until) _bjCount = { target: Date.parse(youSeat.turn_until), label: "⏳ Tvůj tah" };
  else _bjCount = null;
  bjTickCountdown();
  const bb = document.getElementById("bjBetBar");
  if (bb) bb.style.display = g.can_bet ? "" : "none";
  let controls = "";
  if (!g.can_bet && g.status === "betting" && g.you && g.you.state === "ready") controls = '<div class="faint">Vsazeno ✔ — kolo se rozdá po odpočtu (nebo dřív, když host klikne).</div>';
  if (g.can_deal) controls += `<button class="btn btn-success btn-block" data-action="bjr-deal"${controls ? ' style="margin-top:8px"' : ""}>🃏 Rozdat hned</button>`;
  if (g.can_act) controls = `<div class="toolbar" style="gap:8px"><button class="btn btn-success" data-action="bjr-hit">Hit 🎴</button><button class="btn btn-primary" data-action="bjr-stand">Stand ✋</button>${g.can_double ? '<button class="btn btn-accent" data-action="bjr-double">Double ✖2</button>' : ""}${g.can_split ? '<button class="btn btn-ghost" data-action="bjr-split">Split ✂️</button>' : ""}</div>`;
  else if (g.status === "playing" && g.you && g.you.state !== "acting") controls = controls || '<div class="faint">Čeká se na ostatní hráče… 🃏</div>';
  if (g.can_next) controls += `<button class="btn btn-accent btn-block" data-action="bjr-next" style="margin-top:8px">🔄 Nové kolo</button>`;
  const ab = document.getElementById("bjActions");
  if (ab) { ab.innerHTML = controls; ab.style.display = controls ? "" : "none"; }
  const cbx = document.getElementById("bjrChatBox");
  if (cbx) {
    const atBottom = cbx.scrollHeight - cbx.scrollTop - cbx.clientHeight < 50;
    cbx.innerHTML = (g.chat || []).length
      ? g.chat.map((m) => `<div style="margin-bottom:3px"><b style="color:var(--accent)">${esc(m.username)}:</b> ${esc(m.msg)}</div>`).join("")
      : '<div class="faint">Zatím žádné zprávy. Napiš něco! 💬</div>';
    if (atBottom) cbx.scrollTop = cbx.scrollHeight;
  }
}
function bjrChip(amt) {
  const el = document.getElementById("bjrBet"); if (!el) return;
  const cap = Math.min(2000, (state.user && state.user.points) || 0);
  if (amt === "clear") { el.value = ""; el.focus(); return; }
  if (amt === "max") { el.value = cap; el.focus(); return; }
  const cur = parseInt(el.value, 10) || 0;
  el.value = Math.max(0, Math.min(cap, cur + (parseInt(amt, 10) || 0)));
  el.focus();
}
async function bjrCreate() {
  try { const st = await api("/blackjack/room/create", { method: "POST" }); navigate("bj/" + st.code); }
  catch (e) { toast(e.message, "error"); }
}
function bjrJoin() {
  const code = ((document.getElementById("bjrCode") || {}).value || "").trim().toUpperCase();
  if (!code) { toast("Zadej kód stolu.", "error"); return; }
  navigate("bj/" + code);
}
async function bjrAct(path, body) {
  if (!_bjRoomId) return;
  try { _bjRoom = await api("/blackjack/room/" + _bjRoomId + "/" + path, { method: "POST", body }); await refreshMe(); renderBjRoom(); }
  catch (e) { toast(e.message, "error"); }
}
function bjrBet() {
  const bet = parseInt((document.getElementById("bjrBet") || {}).value, 10);
  if (!bet || bet < 10) { toast("Sázka min. 10 sedláků.", "error"); return; }
  bjrAct("bet", { amount: bet });
}
async function bjrChat() {
  const el = document.getElementById("bjrChatMsg");
  const msg = el ? (el.value || "").trim() : "";
  if (!msg || !_bjRoomId) return;
  if (el) el.value = "";
  try { await api("/blackjack/room/" + _bjRoomId + "/chat", { method: "POST", body: { msg } }); bjPollRoom(); }
  catch (e) { toast(e.message, "error"); }
}
async function bjrLeave() {
  _bjStopTimers();
  const rid = _bjRoomId; _bjRoom = null; _bjRoomId = null;
  if (rid) { try { await api("/blackjack/room/" + rid + "/leave", { method: "POST" }); } catch (e) {} }
  navigate("games");
}
function bjrCopy(code) {
  const link = location.origin + "/#/bj/" + code;
  if (navigator.clipboard) navigator.clipboard.writeText(link).then(() => toast("Link zkopírován! 📋", "success"));
  else toast(link, "info");
}
function bjrMute() {
  _bjMuted = !_bjMuted;
  try { localStorage.setItem("bj_muted", _bjMuted ? "1" : "0"); } catch (e) {}
  if (!_bjMuted) audioCtx();   // resume audio kontextu na klik (autoplay policy)
  if (_bjRoom) updateBjRoom(_bjRoom);
  toast(_bjMuted ? "🔇 Zvuk u stolu vypnut" : "🔊 Zvuk u stolu zapnut", "info");
}

async function pageGames() {
  if (!state.user) { navigate("connect"); return; }
  const param = parseRoute().param;
  if (param === "duely") { navigate("games"); return; }   // duely jsou teď inline na Herně
  if (param) { gameView(parseInt(param, 10)); return; }   // číslo = konkrétní piškvorková hra
  $("#view").innerHTML = `
    <div class="page-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap"><div class="ph-mascotgroup"><img class="page-mascot" src="/sedlak-cut.png" alt=""><h1>🎮 Herna</h1></div><a class="btn btn-ghost btn-sm" href="#/staty">📊 Moje herní staty</a></div>
    ${gambleBlockBanner()}
    <div class="panel" style="margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
      <div><b style="font-size:17px">💣 Mines</b> <span class="faint">— odkrývej pole, vyhni se bombám, cashni kdykoliv. Provably-fair, single-player.</span></div>
      <button class="btn btn-accent" data-action="nav" data-href="mines">Hrát Mines →</button>
    </div>
    ${(state.user && state.user.role === "admin") ? `<div style="margin-bottom:18px"><button class="btn btn-ghost btn-sm" data-action="nav" data-href="bj">🃏 Soukromý stůl (jen admin) →</button></div>` : ""}
    <div id="duelWrap">${skeletonCards(1)}</div>
    <div class="section-title" style="margin-top:26px">⚔️ Piškvorky 1v1</div>
    <div class="panel" style="margin-bottom:18px">
      <form class="toolbar" data-submit="game-create">
        <input class="input" id="gameStake" type="number" min="1" step="1" placeholder="Sázka v sedlácích" style="max-width:200px">
        <button class="btn btn-accent" type="submit">Vytvořit a čekat na soupeře ➤</button>
        <span class="faint" style="font-size:12px;margin-left:auto">Zůstatek: <b>${fmtPts(state.user.points)}</b></span>
      </form>
      <div class="faint" style="font-size:12px;margin-top:8px">Hraje se 5 v řadě na ploše <b>9×9</b>. Soupeř musí vsadit stejně. Remíza = vklady zpět.</div>
    </div>
    <div class="section-title">🟢 Otevřené piškvorky</div>
    <div id="gamesLobby">${skeletonCards(1)}</div>
    <div id="duelRecent"></div>`;
  // duely inline (stejný setup jako mívala samostatná stránka)
  window._duelType = "coinflip";
  _seenDuels = null;
  try { const m = await api("/games/duels/mine"); _seenDuels = new Set(m.filter((d) => d.status !== "open").map((d) => d.id)); }
  catch (e) { _seenDuels = new Set(); }
  renderDuelLobby();
  if (duelTimer) clearInterval(duelTimer);
  duelTimer = setInterval(() => { if (!document.hidden) pollDuels(); }, 3500);
  loadGamesLobby();
}

/* ---------------- 💣 Mines (provably-fair, single-player) ---------------- */
let _minesBet = 100, _minesMines = 4;
async function pageMines() {
  if (!state.user) { navigate("connect"); return; }
  $("#view").innerHTML = `<div class="page-head"><h1>💣 Mines</h1><p class="muted">Odkrývej pole, vyhni se bombám, cashni kdykoliv. Provably-fair · mřížka 5×5 · max sázka 1000.</p></div>${gambleBlockBanner()}<div id="minesWrap">${skeletonCards(1)}</div>`;
  try { renderMines(await api("/mines/state")); }
  catch (e) { const w = $("#minesWrap"); if (w) w.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function minesTile(i, g, ended) {
  const rev = g && g.revealed && g.revealed.includes(i);
  const isMine = ended && g && g.layout && g.layout.includes(i);
  const hit = g && g.hit === i;
  let cls = "mines-tile", inner = "";
  if (rev) { cls += " safe"; inner = "💎"; }
  else if (isMine) { cls += hit ? " bomb hit" : " bomb"; inner = "💣"; }
  else if (ended) { cls += " dim"; }
  else { cls += " hidden"; }
  const clickable = g && g.status === "active" && !rev;
  return `<button class="${cls}" ${clickable ? `data-action="mines-reveal" data-tile="${i}"` : "disabled"}>${inner}</button>`;
}
function renderMines(d) {
  const box = $("#minesWrap"); if (!box) return;
  const g = d.game, active = d.active && g && g.status === "active", ended = g && g.status !== "active";
  const grid = (cls) => `<div class="mines-grid ${cls || ""}">${Array.from({ length: 25 }, (_, i) => minesTile(i, g, ended)).join("")}</div>`;
  if (active) {
    box.innerHTML = `
      <div class="mines-hud panel">
        <div><b>Sázka ${fmtPts(g.bet)}</b> · 💣 ${g.mines} bomb · odkryto ${g.safe_count}</div>
        <div class="mines-mult">×${g.mult.toFixed(2)} <span class="faint" style="font-size:13px">další ×${g.next_mult.toFixed(2)}</span></div>
        <button class="btn btn-accent" data-action="mines-cashout">💰 Cashout ${fmtPts(g.cashout)}</button>
      </div>
      ${grid()}
      <div class="faint" style="font-size:12px;margin-top:10px;text-align:center">💎 bezpečno (násobič roste) · 💣 konec · cashni kdykoliv 🌾</div>`;
  } else {
    let banner = "";
    if (ended) {
      banner = g.status === "cashed"
        ? `<div class="panel ok" style="margin-bottom:14px">💰 Vyhrál jsi <b>${fmtPts(g.payout)}</b>! (×${g.mult.toFixed(2)})</div>`
        : `<div class="panel bad" style="margin-bottom:14px">💥 Bomba! Přišel jsi o sázku ${fmtPts(g.bet)}. Příště to vyjde! 🌾</div>`;
    }
    const opts = Array.from({ length: 21 }, (_, i) => i + 4).map((n) => `<option value="${n}" ${n === _minesMines ? "selected" : ""}>${n} 💣</option>`).join("");
    box.innerHTML = `
      ${banner}
      <div class="panel mines-setup">
        <div class="mines-setup-row">
          <label>Sázka<input class="input" id="minesBet" type="number" min="1" max="${d.max_bet || 1000}" value="${_minesBet}"></label>
          <label>Počet bomb<select class="input" id="minesMines">${opts}</select></label>
          <button class="btn btn-accent" data-action="mines-start">▶ Spustit hru</button>
        </div>
        <div class="faint" style="font-size:12px;margin-top:8px">Zůstatek: <b>${fmtPts(d.balance)}</b> · víc bomb = vyšší násobič, ale větší riziko. Min 4 bomby · max výhra 10 000.</div>
      </div>
      ${ended ? grid("ended") : `<div class="mines-grid preview">${Array.from({ length: 25 }, () => `<button class="mines-tile hidden" disabled></button>`).join("")}</div>`}
      ${ended ? `<div style="text-align:center;margin-top:14px"><button class="btn btn-ghost" data-action="mines-new">🔄 Nová hra</button></div>` : ""}`;
  }
}
async function minesStart() {
  const betEl = document.getElementById("minesBet"), mEl = document.getElementById("minesMines");
  _minesBet = Math.max(1, Math.min(1000, parseInt(betEl && betEl.value, 10) || 0));
  _minesMines = Math.max(4, Math.min(24, parseInt(mEl && mEl.value, 10) || 4));
  if (_minesBet < 1) { toast("Zadej sázku.", "error"); return; }
  try {
    const d = await api("/mines/start", { method: "POST", body: { bet: _minesBet, mines: _minesMines } });
    if (state.user) { state.user.points = d.balance; renderHeader(); }
    renderMines({ active: true, game: d.game, max_bet: 5000, balance: d.balance });
  } catch (e) { toast(e.message, "error"); }
}
async function minesReveal(tile) {
  try {
    const d = await api("/mines/reveal", { method: "POST", body: { tile } });
    const bal = typeof d.balance === "number" ? d.balance : (state.user && state.user.points);
    if (typeof d.balance === "number" && state.user) { state.user.points = d.balance; renderHeader(); }
    if (d.cashed) { try { confettiBurst(); } catch (e) {} toast(`Full clear! +${fmtPts(d.payout)} sedláků 🌾`, "success"); }
    renderMines({ active: d.game.status === "active", game: d.game, max_bet: 5000, balance: bal });
  } catch (e) { toast(e.message, "error"); }
}
async function minesCashout() {
  try {
    const d = await api("/mines/cashout", { method: "POST" });
    if (state.user) { state.user.points = d.balance; renderHeader(); }
    try { confettiBurst(); } catch (e) {}
    toast(`Cashout +${fmtPts(d.payout)} sedláků! 💰`, "success");
    renderMines({ active: false, game: d.game, max_bet: 5000, balance: d.balance });
  } catch (e) { toast(e.message, "error"); }
}

/* ---------------- ⚔️ Duely 1v1 (coinflip / kostky) ---------------- */
async function pageDuels() {
  window._duelType = "coinflip";
  _seenDuels = null;
  $("#view").innerHTML = `<div id="duelWrap">${skeletonCards(1)}</div>`;
  // seedni stávající DOHRANÉ duely (ať se nepřehrávají staré), otevřené nech – ty se po vyhodnocení ukážou
  try { const m = await api("/games/duels/mine"); _seenDuels = new Set(m.filter((d) => d.status !== "open").map((d) => d.id)); }
  catch (e) { _seenDuels = new Set(); }
  renderDuelLobby();
  if (duelTimer) clearInterval(duelTimer);
  duelTimer = setInterval(() => { if (!document.hidden) pollDuels(); }, 3500);
}
async function pollDuels() {
  if (!_seenDuels) return;
  let mine;
  try { mine = await api("/games/duels/mine"); } catch (e) { return; }
  const fresh = mine.filter((d) => d.status === "finished" && !_seenDuels.has(d.id));
  fresh.forEach((d) => _seenDuels.add(d.id));
  const stakeEl = document.getElementById("duelStake");
  const stakeVal = stakeEl ? stakeEl.value : null;          // zachovej rozepsanou sázku přes překreslení
  await renderDuelLobby();
  if (stakeVal != null) { const s2 = document.getElementById("duelStake"); if (s2) s2.value = stakeVal; }
  if (fresh.length) { refreshMe(); showDuelReveal(fresh[0], renderDuelLobby); }   // zakladateli přehraj animaci výsledku
}
function duelIco(t) { return t === "coinflip" ? "🪙" : "🎲"; }
function duelDetail(d) {
  if (!d.state) return "";
  return d.type === "coinflip" ? (d.state.coin === "heads" ? "Panna" : "Orel") : `${d.state.roll1} vs ${d.state.roll2}`;
}
async function renderDuelLobby() {
  const wrap = $("#duelWrap"); if (!wrap) return;
  let open = [], mine = [];
  try { [open, mine] = await Promise.all([api("/games/duels/open"), api("/games/duels/mine")]); }
  catch (e) { wrap.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const t = window._duelType || "coinflip";
  const others = open.filter((d) => !d.is_mine);
  const myOpen = open.filter((d) => d.is_mine);
  const recent = mine.filter((d) => d.status === "finished").slice(0, 6);
  const typeBtn = (k, l) => `<button class="cf-side ${t === k ? "active" : ""}" data-action="duel-type" data-t="${k}">${l}</button>`;
  const rowOpen = (d) => `<div class="game-row"><div>${duelIco(d.type)} <b>${uLink(d.p1.username)}</b> · ${esc(d.label)} · vklad <b>${fmtPts(d.stake)}</b> <span class="faint">· ${d.wait_s}s</span></div>
      <button class="btn btn-sm btn-success" data-action="duel-join" data-id="${d.id}">Přijmout ⚔️</button></div>`;
  const rowMine = (d) => `<div class="game-row"><div>${duelIco(d.type)} ${esc(d.label)} · sázka <b>${fmtPts(d.stake)}</b> · čeká na soupeře…</div>
      <button class="btn btn-sm btn-ghost" data-action="duel-cancel" data-id="${d.id}">Zrušit</button></div>`;
  const rowRes = (d) => {
    const won = d.me_player === d.winner;
    const opp = d.me_player === 1 ? (d.p2 && d.p2.username) : d.p1.username;
    return `<div class="game-row"><div>${duelIco(d.type)} vs <b>${uLink(opp)}</b> <span class="faint">· ${duelDetail(d)}</span></div>
      <b style="color:${won ? "var(--success)" : "#ff6b6b"}">${won ? "+" + fmtPts(d.pot) + " 🏆" : "−" + fmtPts(d.stake)}</b></div>`;
  };
  wrap.innerHTML = `
    <div class="section-title">⚔️ Duely 1v1 <span class="faint" style="font-size:12px;font-weight:400">— Coinflip = 50/50, Kostky = vyšší hod bere. Vítěz bere bank. 🏆</span></div>
    <div class="panel" style="margin-bottom:18px">
      <div class="cf-sides" style="margin-bottom:10px">${typeBtn("coinflip", "🪙 Coinflip")}${typeBtn("dice", "🎲 Kostky")}</div>
      <div class="cf-bet-row">
        <input class="input" id="duelStake" type="number" min="10" step="10" value="100" placeholder="Sázka" style="flex:1">
        <button class="btn btn-accent" data-action="duel-create">Vytvořit výzvu ⚔️</button>
      </div>
      <div class="faint" style="font-size:12px;margin-top:6px">Zůstatek: <b>${fmtPts(state.user.points)}</b> · soupeř vsadí stejně</div>
    </div>
    ${myOpen.length ? `<div class="section-title">⏳ Tvoje výzvy</div>${myOpen.map(rowMine).join("")}<div style="height:14px"></div>` : ""}
    <div class="section-title">🟢 Otevřené výzvy</div>
    ${others.length ? others.map(rowOpen).join("") : `<div class="empty">Zatím žádná výzva. Vytvoř první a počkej na soupeře! ⚔️</div>`}`;
  // historii duelů dáme úplně dolů (do #duelRecent), ať jsou herní formuláře nahoře
  const rec = $("#duelRecent");
  if (rec) rec.innerHTML = recent.length
    ? `<div class="section-title" style="margin-top:26px">📜 Tvé poslední duely</div>${recent.map(rowRes).join("")}`
    : "";
}
async function createDuel() {
  const stake = parseInt($("#duelStake")?.value, 10);
  if (!stake || stake < 10) { toast("Minimální sázka je 10 sedláků.", "error"); return; }
  if (stake > state.user.points) { toast("Nemáš tolik sedláků.", "error"); return; }
  try {
    await api("/games/duels/create", { method: "POST", body: { type: window._duelType || "coinflip", stake } });
    await refreshMe();
    renderDuelLobby();
    toast("Výzva vytvořena — čeká na soupeře! ⚔️", "success");
  } catch (e) { toast(e.message, "error"); }
}
async function joinDuel(id) {
  try {
    const d = await api(`/games/duels/${id}/join`, { method: "POST" });
    if (_seenDuels) _seenDuels.add(d.id);   // já jako přijímající už animaci vidím – ať ji poll neopakuje
    await refreshMe();
    showDuelReveal(d, renderDuelLobby);   // animovaný reveal → po zavření překreslí lobby
  } catch (e) { toast(e.message, "error"); }
}
function duelOppName(d) {
  return (d.me_player === 1 ? (d.p2 && d.p2.username) : (d.p1 && d.p1.username)) || "soupeř";
}
function showDuelReveal(d, onClose) {
  const won = d.me_player === d.winner;
  const isCoin = d.type === "coinflip";
  const ov = document.createElement("div");
  ov.className = "duel-reveal";
  ov.innerHTML = `<div class="dr-box">
      <div class="dr-head">${isCoin ? "🪙 Coinflip" : "🎲 Kostky"} duel · vs <b>${esc(duelOppName(d))}</b></div>
      ${isCoin
        ? `<div class="dr-coin spin" id="drCoin">🪙</div>`
        : `<div class="dr-dice"><div class="dd"><span class="dd-l">TY</span><b id="dd1">0</b></div><div class="dd-vs">vs</div><div class="dd"><span class="dd-l">SOUPEŘ</span><b id="dd2">0</b></div></div>`}
      <div class="dr-result" id="drRes">Vyhodnocuju…</div>
      <button class="btn btn-ghost btn-sm" id="drClose" style="display:none">Zavřít</button>
    </div>`;
  document.body.appendChild(ov);
  const close = () => { ov.remove(); if (onClose) onClose(); };
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  const finish = () => {
    ov.querySelector("#drRes").innerHTML = won
      ? `<span class="dr-win">🏆 VYHRÁL JSI! +${fmtPts(d.pot)} sedláků</span>`
      : `<span class="dr-lose">💀 Prohrál jsi ${fmtPts(d.stake)}</span>`;
    if (won) { sndWin(); confettiBurst(); } else { sndLose(); }
    const b = ov.querySelector("#drClose"); b.style.display = ""; b.addEventListener("click", close);
  };
  if (isCoin) {
    const coin = ov.querySelector("#drCoin");
    let ct = 0;
    const tk = setInterval(() => { sndTick(); if (++ct > 9) clearInterval(tk); }, 150);
    setTimeout(() => {
      clearInterval(tk);
      coin.classList.remove("spin");
      coin.textContent = d.state && d.state.coin === "heads" ? "P" : "O";
      coin.classList.add(won ? "win" : "lose");
      finish();
    }, 1500);
  } else {
    const my = d.me_player === 1 ? d.state.roll1 : d.state.roll2;
    const opp = d.me_player === 1 ? d.state.roll2 : d.state.roll1;
    const e1 = ov.querySelector("#dd1"), e2 = ov.querySelector("#dd2");
    let t = 0;
    const iv = setInterval(() => {
      sndTick();
      e1.textContent = 1 + Math.floor(Math.random() * 100);
      e2.textContent = 1 + Math.floor(Math.random() * 100);
      if (++t > 16) {
        clearInterval(iv);
        e1.textContent = my; e2.textContent = opp;
        e1.classList.add(my > opp ? "win" : "lose");
        e2.classList.add(opp > my ? "win" : "lose");
        finish();
      }
    }, 80);
  }
}
/* ---------- Zvuky (Web Audio, syntetizované – bez souborů) + konfety (canvas) ---------- */
let _duelAC = null;
function audioCtx() {
  try {
    _duelAC = _duelAC || new (window.AudioContext || window.webkitAudioContext)();
    if (_duelAC.state === "suspended") _duelAC.resume();
    return _duelAC;
  } catch (e) { return null; }
}
function beep(freq, dur, type, when, vol) {
  const ac = audioCtx(); if (!ac) return;
  const t = ac.currentTime + (when || 0);
  const o = ac.createOscillator(), g = ac.createGain();
  o.type = type || "sine"; o.frequency.value = freq;
  g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol || 0.12, t + 0.008);
  g.gain.exponentialRampToValueAtTime(0.0001, t + (dur || 0.15));
  o.connect(g).connect(ac.destination); o.start(t); o.stop(t + (dur || 0.15) + 0.02);
}
function sndTick() { beep(820, 0.045, "square", 0, 0.05); }
function sndWin() { [523, 659, 784, 1047].forEach((f, i) => beep(f, 0.2, "triangle", i * 0.1, 0.16)); }
function sndLose() { beep(320, 0.22, "sawtooth", 0, 0.14); beep(200, 0.34, "sawtooth", 0.16, 0.14); }
function sndFound() {  // „nalezen soupeř" – výrazný stoupavý signál, ať si toho hráč všimne
  beep(660, 0.12, "triangle", 0, 0.18); beep(880, 0.12, "triangle", 0.12, 0.18);
  beep(1175, 0.22, "triangle", 0.24, 0.20); beep(1568, 0.20, "triangle", 0.40, 0.17);
}
let _titleFlashTimer = null, _titleOrig = null, _gameVisHandler = null;
function flashTitle(msg) {  // bliká titulkem v liště tabu, dokud se hráč nevrátí (kdyby koukal na stream)
  try {
    if (_titleOrig === null) _titleOrig = document.title;
    clearInterval(_titleFlashTimer);
    let on = true;
    _titleFlashTimer = setInterval(() => { document.title = on ? msg : (_titleOrig || "ZURYS"); on = !on; }, 800);
    const stop = () => {
      clearInterval(_titleFlashTimer); _titleFlashTimer = null;
      if (_titleOrig !== null) { document.title = _titleOrig; _titleOrig = null; }
      window.removeEventListener("focus", stop);
      document.removeEventListener("visibilitychange", onVis);
    };
    const onVis = () => { if (!document.hidden) stop(); };
    window.addEventListener("focus", stop);
    document.addEventListener("visibilitychange", onVis);
    setTimeout(stop, 30000);
  } catch (e) {}
}
function onOpponentFound(g) {  // p1 čekal a teď se přidal soupeř → zvuk + upozornění + blik titulku
  try { sndFound(); } catch (e) {}
  toast("⚔️ Soupeř nalezen — jsi na tahu, rychle táhni!", "success");
  flashTitle("⚔️ SOUPEŘ NALEZEN — HRAJ!");
}
function confettiBurst() {
  try {
    const c = document.createElement("canvas"); c.className = "confetti-c";
    c.width = window.innerWidth; c.height = window.innerHeight;
    document.body.appendChild(c);
    const ctx = c.getContext("2d");
    const cols = ["#ffd24a", "#ff9d2e", "#46e08a", "#7c5cff", "#34e0ff", "#ff5b5b"];
    const ps = [];
    for (let i = 0; i < 150; i++) ps.push({ x: c.width / 2 + (Math.random() - .5) * 200, y: c.height / 2 - 30, vx: (Math.random() - .5) * 13, vy: Math.random() * -13 - 4, g: .3 + Math.random() * .2, w: 6 + Math.random() * 6, h: 8 + Math.random() * 8, rot: Math.random() * 6.3, vr: (Math.random() - .5) * .4, col: cols[i % cols.length], life: 1 });
    let t0 = null, raf;
    const frame = (ts) => {
      if (!t0) t0 = ts; const dt = ts - t0;
      ctx.clearRect(0, 0, c.width, c.height);
      let alive = false;
      for (const p of ps) {
        p.vy += p.g; p.x += p.vx; p.y += p.vy; p.rot += p.vr; p.vx *= .99;
        if (dt > 1300) p.life -= .035;
        if (p.life > 0 && p.y < c.height + 40) { alive = true; ctx.save(); ctx.globalAlpha = Math.max(0, p.life); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.col; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore(); }
      }
      if (alive && dt < 4000) raf = requestAnimationFrame(frame); else { cancelAnimationFrame(raf); c.remove(); }
    };
    raf = requestAnimationFrame(frame);
  } catch (e) { /* konfety nikdy nesmí shodit hru */ }
}
async function cancelDuel(id) {
  try { await api(`/games/duels/${id}/cancel`, { method: "POST" }); await refreshMe(); toast("Výzva zrušena, vklad vrácen.", "info"); renderDuelLobby(); }
  catch (e) { toast(e.message, "error"); }
}

async function loadGamesLobby() {
  try {
    const [open, mine] = await Promise.all([api("/games/open"), api("/games/mine")]);
    const box = $("#gamesLobby"); if (!box) return;
    let html = "";
    if (mine.length) {
      html += `<div class="muted" style="font-size:13px;margin:2px 0 8px">Tvoje hry:</div>`;
      html += mine.map((g) => `<div class="game-row">
        <div>🎮 #${g.id} · ${g.status === "open" ? "čeká na soupeře" : `hraje se proti <b>${esc((g.me_player===1?g.p2:g.p1)?.username||"?")}</b>`} · bank <b>${fmtPts(g.pot)}</b></div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-sm btn-accent" data-action="game-open" data-id="${g.id}">${g.status === "active" ? "▶ Hrát" : "Otevřít"}</button>
          ${g.status === "open" ? `<button class="btn btn-sm btn-ghost" data-action="game-cancel" data-id="${g.id}">Zrušit</button>` : ""}
        </div></div>`).join("");
      html += `<div style="height:16px"></div>`;
    }
    const others = open.filter((g) => !g.is_mine);
    if (others.length) {
      html += others.map((g) => `<div class="game-row">
        <div>⚔️ <b>${uLink(g.creator)}</b> sází <b>${fmtPts(g.stake)}</b> <span class="faint">· čeká ${g.wait_s}s</span></div>
        <button class="btn btn-sm btn-success" data-action="game-join" data-id="${g.id}">Přijmout výzvu ⚔️</button>
      </div>`).join("");
    } else if (!mine.length) {
      html += `<div class="empty">Zatím nikdo nevyzývá. Vytvoř první hru a počkej na soupeře! 🎯</div>`;
    }
    box.innerHTML = html;
  } catch (e) { const b = $("#gamesLobby"); if (b) b.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function createGame() {
  try { audioCtx(); } catch (e) {}                       // odemkni zvuk (gesto kliku) pro pozdější „soupeř nalezen"
  const stake = parseInt($("#gameStake")?.value, 10);
  if (!stake || stake < 1) { toast("Zadej sázku (kladné číslo).", "error"); return; }
  if (stake > state.user.points) { toast("Nemáš tolik bodů.", "error"); return; }
  try {
    const g = await api("/games/create", { method: "POST", body: { stake } });
    await refreshMe();
    navigate("games/" + g.id);
  } catch (e) { toast(e.message, "error"); }
}

async function joinGame(id) {
  try { audioCtx(); } catch (e) {}
  try {
    await api(`/games/${id}/join`, { method: "POST" });
    await refreshMe();
    navigate("games/" + id);
  } catch (e) { toast(e.message, "error"); }
}

async function cancelGame(id) {
  try { await api(`/games/${id}/cancel`, { method: "POST" }); await refreshMe(); toast("Hra zrušena, vklad vrácen.", "info"); loadGamesLobby(); }
  catch (e) { toast(e.message, "error"); }
}

function gameView(gid) {
  finishedGameId = null; lastBoardKey = ""; currentGameId = gid; gameState = null;
  $("#view").innerHTML = `<div id="gameWrap">${skeletonCards(1)}</div>`;
  refreshGame(gid);
  if (gameTimer) clearInterval(gameTimer);
  // poll i na skryté kartě – zakladatel typicky kouká na stream v jiném tabu a musíme ho
  // upozornit (zvuk/blik titulku), jakmile se soupeř přidá. Zátěž = 1 dotaz / 1,3 s.
  gameTimer = setInterval(() => refreshGame(gid), 1300);
  if (gameClockTimer) clearInterval(gameClockTimer);
  gameClockTimer = setInterval(tickGameClock, 1000);
  if (!_gameVisHandler) {   // po návratu do tabu načti hned aktuální stav (okamžité upozornění)
    _gameVisHandler = () => { if (!document.hidden && currentGameId && document.getElementById("gameWrap")) refreshGame(currentGameId); };
    document.addEventListener("visibilitychange", _gameVisHandler);
  }
}

async function refreshGame(gid) {
  try {
    const g = await api("/games/" + gid);
    const wasOpen = gameState && gameState.status === "open";
    gameState = g;
    if (wasOpen && g.status === "active") onOpponentFound(g);   // soupeř se právě přidal → upozorni
    syncGameClock(g);
    renderBoard(g);
    if (g.status === "finished" || g.status === "cancelled") {
      if (gameTimer) { clearInterval(gameTimer); gameTimer = null; }
      onGameFinished(g);
    }
  } catch (e) {
    if (gameTimer) { clearInterval(gameTimer); gameTimer = null; }
    const w = $("#gameWrap"); if (w) w.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function syncGameClock(g) {
  gameClockBase = (g && g.status === "active")
    ? { moveLeft: g.move_left_s || 0, gameLeft: g.game_left_s || 0, at: Date.now(), yourTurn: !!g.your_turn }
    : null;
}
function tickGameClock() {
  const el = $("#gameClock"); if (!el) return;
  if (!gameClockBase) { el.textContent = ""; return; }
  const elapsed = Math.floor((Date.now() - gameClockBase.at) / 1000);
  const moveLeft = Math.max(0, gameClockBase.moveLeft - elapsed);
  const gameLeft = Math.max(0, gameClockBase.gameLeft - elapsed);
  const who = gameClockBase.yourTurn ? "tvůj tah" : "soupeř";
  el.innerHTML = `<span class="gclock-move${moveLeft <= 5 ? " low" : ""}">⏱️ ${who}: ${moveLeft}s</span> <span class="faint">· hra ${gameLeft}s</span>`;
}
function renderBoard(g) {
  const wrap = $("#gameWrap"); if (!wrap) return;
  const key = g.board.join("") + "|" + g.turn + "|" + g.status + "|" + g.can_claim_timeout + "|" + (g.p2 ? g.p2.id : 0);
  if (key === lastBoardKey && $("#gboard")) return;  // beze změny → nepřekresluj (anti-flicker)
  lastBoardKey = key;
  const me = g.me_player;
  let banner;
  if (g.status === "open") {
    banner = `<div class="panel gold">⏳ Čekáš na soupeře… Pošli kamarádům odkaz nebo počkej, až se někdo přidá z lobby. <button class="btn btn-sm btn-ghost" data-action="game-cancel" data-id="${g.id}" style="margin-left:8px">Zrušit (vrátit vklad)</button></div>`;
  } else if (g.status === "finished") {
    const txt = g.winner === 0 ? "🤝 Remíza — vklady vráceny."
      : g.winner === me ? `🏆 Vyhrál jsi! Bereš ${fmtPts(g.pot - Math.floor(g.pot * g.rake_pct / 100))}.`
        : "😢 Prohrál jsi tuhle partii.";
    banner = `<div class="panel ${g.winner === me ? "ok" : (g.winner === 0 ? "" : "bad")}">${txt} <button class="btn btn-sm btn-accent" data-action="game-back" style="margin-left:8px">Zpět do arény</button></div>`;
  } else if (g.status === "cancelled") {
    banner = `<div class="panel">Hra byla zrušena. <button class="btn btn-sm btn-accent" data-action="game-back">Zpět do arény</button></div>`;
  } else {
    const opp = (me === 1 ? g.p2 : g.p1);
    const turnLbl = g.your_turn ? `<b style="color:var(--accent)">Jsi na tahu!</b>` : `Hraje <b>${esc(opp ? opp.username : "soupeř")}</b>…`;
    banner = `<div class="panel"><div class="row-between">
      <div>${turnLbl} <span class="faint">· bank ${fmtPts(g.pot)} · ty hraješ ${me === 1 ? "✕" : "◯"}</span></div>
      <div style="display:flex;gap:10px;align-items:center"><span id="gameClock" class="gclock"></span>
      ${g.can_claim_timeout ? `<button class="btn btn-sm btn-success" data-action="game-claim" data-id="${g.id}">Soupeř nehraje → Nárokovat výhru</button>` : ""}</div>
    </div></div>`;
  }
  const cells = g.board.map((v, i) => {
    const cls = v === 1 ? "x" : (v === 2 ? "o" : "");
    const mark = v === 1 ? "✕" : (v === 2 ? "◯" : "");
    const live = g.your_turn && v === 0;
    return `<button class="gcell ${cls} ${live ? "live" : ""}" data-action="game-move" data-id="${g.id}" data-cell="${i}" ${live ? "" : "disabled"}>${mark}</button>`;
  }).join("");
  wrap.innerHTML = `
    <div class="page-head" style="margin-bottom:12px"><h1 style="font-size:24px">🎮 Piškvorky #${g.id}</h1>
      <p class="muted">${esc(g.p1 ? g.p1.username : "?")} (✕) vs ${esc(g.p2 ? g.p2.username : "…")} (◯) · 5 v řadě</p></div>
    ${banner}
    <div class="gboard" id="gboard" style="grid-template-columns:repeat(${g.size},1fr)">${cells}</div>`;
}

async function makeMove(id, cell) {
  try {
    const g = await api(`/games/${id}/move`, { method: "POST", body: { cell: parseInt(cell, 10) } });
    gameState = g; syncGameClock(g); renderBoard(g);
    if (g.status === "finished") { if (gameTimer) { clearInterval(gameTimer); gameTimer = null; } onGameFinished(g); }
  } catch (e) { toast(e.message, "error"); refreshGame(id); }
}

async function claimTimeout(id) {
  try {
    const g = await api(`/games/${id}/claim-timeout`, { method: "POST" });
    gameState = g; syncGameClock(g); renderBoard(g);
    if (g.status === "finished") { if (gameTimer) { clearInterval(gameTimer); gameTimer = null; } onGameFinished(g); }
  } catch (e) { toast(e.message, "error"); }
}

function onGameFinished(g) {
  if (finishedGameId === g.id) return;  // jen jednou
  finishedGameId = g.id;
  refreshMe();
  if (g.status === "cancelled") return;
  const me = g.me_player;
  if (g.winner === 0) toast("🤝 Remíza — vklady vráceny.", "info");
  else if (g.winner === me) toast(`🏆 Vyhrál jsi! +${fmtPts(g.pot - Math.floor(g.pot * g.rake_pct / 100))}`, "success");
  else toast("😢 Prohra. Příště to vyjde!", "error");
}

/* ============================================================
   RANK-UP OSLAVA (konfety + toast)
============================================================ */
const LEAGUE_LABELS = { bronze: "Nádeník", silver: "Hospodář", gold: "Rychtář", elite: "Zeman", unreal: "Král" };
function celebrateConfetti() {
  const colors = ["#ff9d2e", "#46d369", "#ff5b5b", "#7c5cff", "#ffd23e", "#2ee6c5"];
  const wrap = document.createElement("div");
  wrap.className = "confetti-wrap";
  for (let i = 0; i < 90; i++) {
    const c = document.createElement("i");
    c.className = "confetti-bit";
    c.style.left = Math.random() * 100 + "vw";
    c.style.background = colors[i % colors.length];
    c.style.animationDelay = (Math.random() * 0.5).toFixed(2) + "s";
    c.style.animationDuration = (1.6 + Math.random() * 1.6).toFixed(2) + "s";
    wrap.appendChild(c);
  }
  document.body.appendChild(wrap);
  setTimeout(() => wrap.remove(), 4000);
}
function celebrateRankup(league) {
  celebrateConfetti();
  toast(`🎉 Postoupil jsi do ligy ${LEAGUE_LABELS[league] || league}! 🎉`, "success");
  api("/auth/seen-rankup", { method: "POST", body: {} }).catch(() => {});
  if (state.user) state.user.pending_rankup = "";
}
function nudgeOvertake(raw) {
  let o; try { o = JSON.parse(raw); } catch (e) { return; }
  if (!o || !o.by) return;
  toast(`👀 Klesl jsi na #${o.rank}! ${esc(o.by)} je teď před tebou — zaber zpátky nahoru! 💪`, "info");
  api("/auth/seen-overtake", { method: "POST", body: {} }).catch(() => {});
  if (state.user) state.user.pending_overtake = "";
}

/* ============================================================
   INIT
============================================================ */
async function init() {
  try { const r = await api("/auth/me"); state.user = r.user; } catch (e) { state.user = null; }
  if (state.user && state.user.pending_rankup) setTimeout(() => celebrateRankup(state.user.pending_rankup), 600);
  if (state.user && state.user.pending_overtake) setTimeout(() => nudgeOvertake(state.user.pending_overtake), 900);
  if (state.user) {
    // anticheat: klientský signál (headless/webdriver)
    api("/auth/fingerprint", { method: "POST", body: { webdriver: navigator.webdriver === true, fp: deviceFingerprint() } }).catch(() => {});
  }
  if (location.hash.includes("bot=connected")) {   // návrat z OAuth připojení bota
    adminState.tab = "bot";
    setTimeout(() => toast("✅ Bot připojen přes Kick (chat:write).", "success"), 300);
  }
  if (!location.hash) location.hash = "#/shop";
  render();
  refreshBonusDot();   // rozsvítí tečku na „Bonusy", pokud je co vyzvednout
  if (!localStorage.getItem("zurys_welcome_v1") && !(state.user && (state.user.pending_rankup || state.user.pending_overtake))) {
    localStorage.setItem("zurys_welcome_v1", "1");   // uvítací průvodce 1× (nováčci + stávající po redesignu); znovu z patičky
    setTimeout(welcomeGuide, 700);
  }
  if (!window._dmBadgeTimer) window._dmBadgeTimer = setInterval(pollDmBadge, 20000);   // live ✉️ badge (šetrný interval)
  if (!window._notifBadgeTimer) window._notifBadgeTimer = setInterval(pollNotifBadge, 20000);   // live 🔔 badge
}
window.addEventListener("hashchange", render);

/* ---- Pasivní výdělek: heartbeat za sledování (jen přihlášený + aktivní záložka) ---- */
let hbTimer = null;
async function activityHeartbeat() {
  if (!state.user || document.visibilityState !== "visible") return;
  try {
    const r = await api("/activity/heartbeat", { method: "POST", body: {} });
    if (r && r.awarded > 0) {
      state.user.points = r.balance;
      renderHeader();
      const m = r.summary && r.summary.mult > 1 ? ` (×${r.summary.mult})` : "";
      toast(`+${r.awarded} sedláků za sledování${m} ⏱️`, "success");
    }
  } catch (e) { /* tiše – host/anti-bot/strop */ }
}
function startHeartbeat() {
  if (hbTimer) clearInterval(hbTimer);
  hbTimer = setInterval(activityHeartbeat, 60000);  // ~1×/min
  setTimeout(activityHeartbeat, 8000);              // první po 8 s
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") activityHeartbeat(); });

/* Delegované listenery místo inline on*= atributů v innerHTML → SPA nemá žádný inline JS,
   takže CSP script-src smí být 'self' (tvrdší obrana proti XSS). Delegace na document funguje
   i pro prvky vykreslené později (upload coin/profil obrázku, hledání skinů, ban nicku v Mines). */
document.addEventListener("change", (e) => {
  const id = e.target && e.target.id;
  if (id === "coinFile") uploadCoinIcon();
  else if (id === "pf_file") uploadImageFile();
});
document.addEventListener("input", (e) => {
  if (e.target && e.target.id === "skinQ") debouncedSkinSearch();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const t = e.target;
  if (!t) return;
  if (t.id === "skinQ") { e.preventDefault(); searchSkins(); }
  else if (t.id === "minesBanNick" && t.nextElementSibling) t.nextElementSibling.click();
});

init().then(startHeartbeat);
