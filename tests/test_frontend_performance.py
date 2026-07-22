from pathlib import Path


def test_hot_polling_and_market_load_stay_lightweight():
    js = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")

    market = js.split("async function loadMarketListings()", 1)[1].split("function marketDealsHTML", 1)[0]
    auction_timer = js.split("function _startAuctionTimer()", 1)[1].split("function aucSetAmt", 1)[0]
    stream = js.split("async function refreshStreamDot()", 1)[1].split("function pageShop", 1)[0]
    partners = js.split("async function loadPartnerLinks()", 1)[1].split("async function claimPartnerLink", 1)[0]
    shop = js.split("function pageShop()", 1)[1].split("async function loadActivity", 1)[0]

    assert "const [d, mine] = await Promise.all([" in market
    assert 'api("/auctions")' in market and 'api("/auctions/my-sales")' in market
    assert "if (document.hidden) return;" in auction_timer
    assert "30000 + Math.random() * 15000" in auction_timer
    assert "_streamStatusLoading" in stream and "Date.now() - _streamStatusAt >= 120000" in stream
    assert "if (_partnerLoading) return;" in partners
    assert "if (_partnerTimer === timer) _partnerTimer = null;" in partners
    assert "if (document.getElementById(\"partnersCard\") !== box) return;" in partners
    assert 'loadProducts(true);\n  dropTimer = setTimeout(() => {' in shop
    assert 'currentRoute() !== "shop"' in shop
    assert "750 + Math.random() * 1250" in shop
    assert "60000 + Math.random() * 30000" in js
    assert "90000 + Math.random() * 30000" in js
    assert "const _apiGetPending = new Map();" in js
    assert "await loadCrewTags(true)" not in js
