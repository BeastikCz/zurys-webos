"""Deploy brana pro zurys.live: pre-flight -> bump cache -> sync na Contabo -> health -> CF purge.

Od 12.7.2026 bezi produkce na Contabo VPS (169.58.8.1), NE na Fly. Deploy = tar
pracovniho stromu (app/ web/ requirements.txt) pres SSH + restart systemd service.
Zabaluje rucni kroky co se pri deployi zapominaji (maintenance gate, bump cache verze)
a nechava ostry deploy za explicitni pojistkou.

  python deploy.py            # DRY-RUN: overi udrzbu + predeploy, ukaze pristi cache verzi. NIC nenasadi.
  python deploy.py --deploy   # OSTRY: bump cache -> sync na server -> health -> CF Purge Everything
  python deploy.py --selftest # jen self-test bump logiky (bez site)

Exit 0 = OK. Exit 1 = neco je spatne / STOP.

Proc skript a ne /deploy slash-command: desktop app custom commandy nenacita.
Cache bump zustava jako necommitnuta zmena web/index.html -> commitni ji sam.
"""
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # Windows konzole umi shodit print s diakritikou (charmap) -> vynut utf-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HEALTHZ = "https://zurys.live/api/monitor/healthz"
SERVER = "root@169.58.8.1"                                     # Contabo VPS
SSH_KEY = str(Path.home() / ".ssh" / "hetzner_zurys")
SSH = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", SERVER]
DEPLOY_DIRS = ["app", "web", "requirements.txt"]               # co se syncuje na server
INDEX = Path(__file__).parent / "web" / "index.html"
CF_TOKEN_FILE = Path(__file__).parent / "cf_purge_token.txt"   # gitignored; viz purge_cloudflare()
CF_ZONE = "zurys.live"
CF_API = "https://api.cloudflare.com/client/v4"


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
    print("NOK  produkce po deployi neodpovida zdrave - zkontroluj: ssh " + SERVER + " 'journalctl -u webos -n 50'")
    return False


def deploy_contabo():
    """Zabali app/ web/ requirements.txt do tar.gz, posle na server, rozbali
    do /opt/webos/app a restartne webos.service. Stary kod na serveru prepise,
    smazane soubory NEmaze (neva - server je jinak disposable, viz contabo_setup.sh)."""
    root = Path(__file__).parent
    fd, tmp = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        with tarfile.open(tmp, "w:gz") as tf:
            for name in DEPLOY_DIRS:
                tf.add(root / name, arcname=name,
                       filter=lambda ti: None if "__pycache__" in ti.name else ti)
        if subprocess.run(["scp", "-i", SSH_KEY, tmp, SERVER + ":/tmp/deploy.tar.gz"]).returncode != 0:
            print("NOK  scp selhal")
            return False
        cmd = ("tar xzf /tmp/deploy.tar.gz -C /opt/webos/app && rm /tmp/deploy.tar.gz"
               " && chown -R webos:webos /opt/webos/app && systemctl restart webos")
        if subprocess.run(SSH + [cmd]).returncode != 0:
            print("NOK  rozbaleni/restart na serveru selhal")
            return False
        print("OK   kod nasazen + webos.service restartovan")
        return True
    finally:
        os.unlink(tmp)


def _cf(path, token, payload=None):
    req = urllib.request.Request(CF_API + path, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def purge_cloudflare():
    """CF cachuje app.js a IGNORUJE ?v= v cache klici -> bez purge dostavaji divaci
    stary frontend i po deployi. Token: dash.cloudflare.com/profile/api-tokens
    -> Create Token -> Custom -> Zone / Cache Purge / Purge, zona zurys.live;
    uloz retezec do cf_purge_token.txt (je v .gitignore, NIKDY necommitovat)."""
    if not CF_TOKEN_FILE.exists():
        print("NOK  CF purge preskocen - chybi %s. Purgni RUCNE v dashboardu!" % CF_TOKEN_FILE.name)
        return False
    try:
        token = CF_TOKEN_FILE.read_text(encoding="utf-8").strip()
        z = _cf("/zones?name=" + CF_ZONE, token)
        if not z.get("result"):
            print("NOK  CF purge - zona nenalezena (token/permissions?). Purgni RUCNE!")
            return False
        r = _cf("/zones/%s/purge_cache" % z["result"][0]["id"], token, {"purge_everything": True})
        if r.get("success"):
            print("OK   Cloudflare Purge Everything")
            return True
        print("NOK  CF purge selhal: %r. Purgni RUCNE!" % r.get("errors"))
    except Exception as e:
        print("NOK  CF purge selhal (%s). Purgni RUCNE!" % type(e).__name__)
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
    # sw.js drzi APP_SHELL urls + jmeno cache, app.js registraci sw.js -> stejny bump,
    # jinak by offline shell po deployi cachoval stare soubory.
    for f in (INDEX.parent / "sw.js", INDEX.parent / "app.js"):
        t = f.read_text(encoding="utf-8")
        f.write_text(t.replace("?v=" + old, "?v=" + new)
                      .replace("zurys-shell-" + old, "zurys-shell-" + new), encoding="utf-8")

    print("\n[4/4] Sync na Contabo + restart...")
    if not deploy_contabo():
        return 1

    print("\nOveruji health po deployi...")
    ok = wait_healthy()

    print("\n[5/5] Cloudflare purge...")
    ok = purge_cloudflare() and ok

    print("\nHotovo. Cache bump ve web/index.html neni commitnuty - commitni ho sam.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
