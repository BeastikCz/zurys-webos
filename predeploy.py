"""Pre-deploy brána: než nasadíš, ověř že je vše OK.

Spustí:
  1) pytest smoke testy (import app, veřejné endpointy 200, auth 401, statika, odpočet)
  2) kontrolu frontendu (app.js backticky/závorky, styles.css složené závorky)

Použití (z kořene projektu):
  .venv/Scripts/python.exe predeploy.py

Exit 0 = můžeš deployovat. Exit 1 = něco je rozbité, NEDEPLOYUJ.
(Soubor se NEnasazuje – Dockerfile kopíruje jen app/ a web/.)
"""
import io
import subprocess
import sys

errs = []


def read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


# 1) pytest
print("== pytest ==")
r = subprocess.run([sys.executable, "-m", "pytest"], capture_output=True, text=True)
sys.stdout.write((r.stdout or "")[-800:])
sys.stdout.write((r.stderr or "")[-300:])
if r.returncode != 0:
    errs.append("pytest FAILED")

# 2) frontend balance (heuristika – chytí rozbitý template literal / závorku)
print("\n== frontend ==")
js = read("web/app.js")
css = read("web/styles.css")
if js.count("`") % 2 != 0:
    errs.append("app.js: lichý počet backticků (rozbitý template literal)")
for o, c, name in [("{", "}", "složená"), ("(", ")", "kulatá"), ("[", "]", "hranatá")]:
    if js.count(o) != js.count(c):
        errs.append("app.js: nevyvážená %s závorka %d/%d" % (name, js.count(o), js.count(c)))
if css.count("{") != css.count("}"):
    errs.append("styles.css: nevyvážené {} %d/%d" % (css.count("{"), css.count("}")))
print("app.js + styles.css OK")

print()
if errs:
    for e in errs:
        sys.stdout.write(("NOK  " + e + "\n").encode("ascii", "replace").decode("ascii"))
    sys.exit(1)
print("OK - vse proslo, muzes deployovat")
