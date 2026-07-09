"""Deploy brana pro zurys.live: pre-flight -> bump cache -> flyctl deploy -> health.

Zabaluje rucni kroky co se pri deployi zapominaji (maintenance gate, bump cache verze)
a nechava ostry deploy za explicitni pojistkou.

  python deploy.py            # DRY-RUN: overi udrzbu + predeploy, ukaze pristi cache verzi. NIC nenasadi.
  python deploy.py --deploy   # OSTRY: bump cache -> flyctl deploy -> overi health
  python deploy.py --selftest # jen self-test bump logiky (bez site)

Exit 0 = OK. Exit 1 = neco je spatne / STOP.

Proc skript a ne /deploy slash-command: desktop app custom commandy nenacita.
Cache bump zustava jako necommitnuta zmena web/index.html -> commitni ji sam.
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # Windows konzole umi shodit print s diakritikou (charmap) -> vynut utf-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HEALTHZ = "https://zurys.live/api/monitor/healthz"
INDEX = Path(__file__).parent / "web" / "index.html"


def _get(url, timeout=10):
    """(status, headers_dict, json_or_None). Stdlib urllib, zadne zavislosti."""
    req = urllib.request.Request(url, headers={"User-Agent": "deploy.py"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), _json(r.read())
    except urllib.error.HTTPError as e:  # 4xx/5xx nese telo i hlavicky
        return e.code, dict(e.headers), _json(e.read())


def _json(b):
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        return None


def check_production():
    """True = OK deployovat. False = STOP. Tiskne duvod. Read-only GET na produkci."""
    try:
        status, hdrs, data = _get(HEALTHZ)
    except Exception as e:
        print("NOK  health nedostupny (%s) - zkontroluj sit / Fly" % type(e).__name__)
        return False
    checks = (data or {}).get("checks", {})
    if hdrs.get("X-Maintenance") == "1" or checks.get("maintenance") == "on":
        print("NOK  PRODUKCE JE V UDRZBE (maintenance_mode=1).")
        print("     Deploy by ji reloadnul a zhasnul web VSEM. Vypni ji nejdriv:")
        print("     /api/admin/maintenance?to=off")
        return False
    if status != 200:
        print("NOK  healthz vraci %d (ne 200) - neco je spatne, nedeployuj" % status)
        return False
    if checks.get("db") != "ok":
        print("NOK  DB health = %r - nedeployuj" % checks.get("db"))
        return False
    print("OK   produkce zdrava (udrzba vypla, db ok)")
    return True


def bump_version(html):
    """Najde ?v=<cislo> a inkrementuje o 1. Vraci (new_html, old, new).
    Obe URL (styles.css + app.js) sdili tutez verzi -> replace zmeni obe."""
    m = re.search(r"\?v=(\d+)", html)
    if not m:
        raise ValueError("v index.html nenalezen ?v=<cislo>")
    old = m.group(1)
    new = str(int(old) + 1)
    return html.replace("?v=" + old, "?v=" + new), old, new


def run_predeploy():
    print("== predeploy (pytest + JS syntax) ==")
    return subprocess.run([sys.executable, "predeploy.py"]).returncode == 0


def wait_healthy(tries=6, gap=5):
    """Po deployi: stroj se restartoval, pockej az healthz vrati 200 + db ok."""
    for i in range(tries):
        try:
            status, _, data = _get(HEALTHZ)
            if status == 200 and (data or {}).get("checks", {}).get("db") == "ok":
                print("OK   produkce nabehla (200, db ok)")
                return True
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(gap)
    print("NOK  produkce po deployi neodpovida zdrave - zkontroluj Fly monitoring")
    return False


def _selftest():
    h = '<link href="/styles.css?v=2026070207"><script src="/app.js?v=2026070207"></script>'
    out, o, n = bump_version(h)
    assert (o, n) == ("2026070207", "2026070208"), (o, n)
    assert out.count("?v=2026070208") == 2 and "?v=2026070207" not in out
    try:
        bump_version("<html>no version</html>")
        assert False, "melo hodit ValueError"
    except ValueError:
        pass
    print("selftest OK")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        _selftest()
        return 0
    deploy = arg == "--deploy"

    print("[1/4] Kontrola produkce (udrzba + DB)...")
    if not check_production():
        return 1

    print("\n[2/4] Pre-deploy testy...")
    if not run_predeploy():
        print("NOK  predeploy selhal - NEDEPLOYUJ")
        return 1

    html = INDEX.read_text(encoding="utf-8")
    _, old, new = bump_version(html)

    if not deploy:
        print("\n[dry-run] Vse zelene. Cache by se bumpla %s -> %s." % (old, new))
        print("Ostry deploy: python deploy.py --deploy")
        return 0

    print("\n[3/4] Bump cache verze %s -> %s..." % (old, new))
    INDEX.write_text(bump_version(html)[0], encoding="utf-8")

    print("\n[4/4] flyctl deploy...")
    if subprocess.run(["flyctl", "deploy"]).returncode != 0:
        print("NOK  flyctl deploy selhal")
        return 1

    print("\nOveruji health po deployi...")
    ok = wait_healthy()
    print("\nHotovo. Cache bump ve web/index.html neni commitnuty - commitni ho sam.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
