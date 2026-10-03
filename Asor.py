import argparse, csv, datetime as dt, gzip, platform, re, sys, time, unicodedata
import urllib.robotparser
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.diariooficial.interior.gob.cl"
LISTADO = BASE + "/edicionelectronica/index.php?date={dd}-{mm}-{yyyy}&edition={ed}"
PDF_RE = re.compile(r"/publicaciones/(\d{4})/(\d{2})/(\d{2})/(\d+)/(\d{2})/(\d+)\.pdf")
INICIO, FIN = dt.date(2025, 1, 1), dt.date(2026, 9, 30)
ED_ANTERIOR_A_INICIO = 44037     
VENTANA = 3
PAUSA = 1.0
UA = "Mozilla/5.0 (investigacion academica; censo ANCI) requests"
MESES = {m: i for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(), 1)}
CLAVES_CIBER = ["ciber", "seguridad de la informacion", "infraestructura critica", "csirt"]
SES = requests.Session(); SES.headers["User-Agent"] = UA


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip().lower()


def ahora():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def get(url, intentos=2):
    err = ""
    for i in range(intentos):
        t0 = time.time()
        print(f"  -> consultando {url[-70:]} (intento {i + 1})...", flush=True)
        try:
            r = SES.get(url, timeout=(10, 25)); time.sleep(PAUSA)
            print(f"     HTTP {r.status_code} en {time.time() - t0:.1f}s", flush=True)
            if r.status_code >= 500:
                err = f"HTTP {r.status_code}"; time.sleep(3 * (i + 1)); continue
            return r.status_code, r.content, ""
        except requests.RequestException as e:
            err = f"{type(e).__name__}: {e}"
            print(f"     FALLÓ tras {time.time() - t0:.1f}s: {type(e).__name__}", flush=True)
            time.sleep(3 * (i + 1))
    return None, b"", err


def url_listado(fecha, ed):
    return LISTADO.format(dd=f"{fecha:%d}", mm=f"{fecha:%m}", yyyy=f"{fecha:%Y}", ed=ed)


def parsear_listado(html_bytes):
    """Devuelve dict(edicion, fecha, filas). Cada fila: poder, ministerio, organismo, titulo, cve, url_pdf."""
    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    texto = soup.get_text(" ", strip=True)
    me = re.search(r"Edici[óo]n\s+N[úu]m\.?\s*([\d\.]+)", texto)
    mf = re.search(r"(?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\s+(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})",
                   texto, re.I)
    edicion = me.group(1).replace(".", "") if me else ""
    fecha = None
    if mf and norm(mf.group(2)) in MESES:
        fecha = dt.date(int(mf.group(3)), MESES[norm(mf.group(2))], int(mf.group(1)))
    filas, poder, ministerio, organismo = [], "", "", ""
    for tr in soup.find_all("tr"):
        a = next((x for x in tr.find_all("a", href=True) if PDF_RE.search(x["href"])), None)
        celdas = tr.find_all(["td", "th"])
        if a:
            m = PDF_RE.search(a["href"])
            if m.group(5) != "01":
                continue
            titulo = celdas[0].get_text(" ", strip=True) if celdas else tr.get_text(" ", strip=True)
            filas.append({"poder": poder, "ministerio": ministerio, "organismo": organismo,
                          "titulo": titulo, "cve": m.group(6),
                          "url_pdf": requests.compat.urljoin(BASE, a["href"])})
            continue
        t = tr.get_text(" ", strip=True)
        if not t or norm(t) in ("sumario", "normas generales"):
            continue
        if t.upper().startswith("PODER "):
            poder, ministerio, organismo = t, "", ""
        elif t == t.upper():
            ministerio, organismo = t, ""
        else:
            organismo = t
    links = set()
    for m in PDF_RE.finditer(html):
        try:
            links.add((dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(4)))
        except ValueError:
            pass
    return {"edicion": edicion, "fecha": fecha, "filas": filas, "links": links}


def coincide(p, dia, ed):
    """La página es la edición (dia, ed) SOLO si sus enlaces a PDF lo dicen.
    El encabezado NO es confiable: el sitio repite la fecha y el número que van en la URL,
    aunque esa combinación no exista (por eso antes se aceptaban ediciones falsas)."""
    return (dia, str(ed)) in p["links"]


def desglosar_titulo(titulo):
    m = re.match(r"^(?P<tipo>.*?)\s+n[úu]mero\s+(?P<num>[\d\.]+)(?:,\s*de\s+(?P<anio>\d{4}))?\.-\s*(?P<resto>.*)$",
                 titulo, re.S)
    if not m:
        return "", "", "", titulo
    return m.group("tipo"), m.group("num").replace(".", ""), m.group("anio") or "", m.group("resto")


def es_anci(f):
    return "agencia nacional de ciberseguridad" in norm(f["organismo"] + " " + f["ministerio"])


def es_ciber(f):
    t = norm(f["titulo"])
    return any(k in t for k in CLAVES_CIBER)


CAMPOS_ACTOS = ["fecha_edicion", "edicion", "cve", "ministerio", "organismo", "tipo", "numero", "anio_acto",
                "titulo_resumen", "titulo_listado", "url_pdf", "url_listado", "motivo", "consultado_utc"]
CAMPOS_REG = ["fecha_edicion", "consultado_utc", "intentos_edicion_url_status", "ediciones_aceptadas",
              "n_normas_generales", "n_anci", "n_ciber_otros", "estado", "error", "html_guardado"]


def leer_csv(r):
    if not r.exists():
        return []
    with open(r, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def abrir_csv(r, campos):
    nuevo = not r.exists()
    f = open(r, "a", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=campos)
    if nuevo:
        w.writeheader()
    return f, w


def _regla_a_regex(patron):
    fin = patron.endswith("$")
    rx = re.escape(patron.rstrip("$")).replace(r"\*", ".*")
    return re.compile(rx + ("$" if fin else ""))


def robots_ok(rutas=("/edicionelectronica/index.php", "/publicaciones/2025/02/12/44073/01/2608380.pdf")):
    """Lee robots.txt y aplica la regla del patrón MÁS ESPECÍFICO (RFC 9309; en empate gana Allow).
    Python urllib.robotparser no entiende comodines ni 'más específico', por eso no se usa."""
    try:
        r = requests.get(BASE + "/robots.txt", headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"No pude leer robots.txt ({e}). Por precaución me detengo.")
        return False
    grupos, agentes, reglas, previo_regla = [], [], [], False
    for linea in r.text.splitlines():
        linea = linea.split("#", 1)[0].strip()
        if ":" not in linea:
            continue
        k, v = (x.strip() for x in linea.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if previo_regla:
                grupos.append((agentes, reglas)); agentes, reglas, previo_regla = [], [], False
            agentes.append(v.lower())
        elif k in ("allow", "disallow"):
            previo_regla = True
            if v:
                reglas.append((k, v))
    grupos.append((agentes, reglas))
    ua = UA.lower()
    especifico = [g for g in grupos if any(a != "*" and a in ua for a in g[0])]
    elegido = especifico[0] if especifico else next((g for g in grupos if "*" in g[0]), ([], []))
    todo_ok = True
    for ruta in rutas:
        mejor = None
        for tipo, patron in elegido[1]:
            if _regla_a_regex(patron).match(ruta):
                clave = (len(patron), tipo == "allow")
                if mejor is None or clave > mejor[0]:
                    mejor = (clave, tipo, patron)
        permitido = mejor is None or mejor[1] == "allow"
        todo_ok &= permitido
        print(f"robots.txt: {ruta} -> {'PERMITIDO' if permitido else 'BLOQUEADO'}"
              + (f" (regla {mejor[1]}: {mejor[2]})" if mejor else " (sin regla aplicable)"))
    return todo_ok


def cmd_probe(args):
    print("Resultado robots.txt:", robots_ok())
    casos = [(dt.date(2025, 2, 12), 44073, True), (dt.date(2025, 1, 2), 44038, True),
             (dt.date(2025, 1, 6), 44041, True), (dt.date(2025, 1, 7), 44042, True),
             (dt.date(2025, 1, 11), 44046, True),
             (dt.date(2025, 1, 5), 44041, False)]   
    for fecha, ed, esperado in casos:
        u = url_listado(fecha, ed)
        st, c, err = get(u)
        print(f"\n=== {fecha} ed {ed} (debe coincidir: {esperado}) HTTP {st} {err}")
        if not c:
            continue
        p = parsear_listado(c)
        print(f"Encabezado: edición {p['edicion'] or '?'} fecha {p['fecha']} | enlaces: {sorted(p['links'])[:2]} "
              f"| filas: {len(p['filas'])}\n  encabezado dice coincidir: {p['fecha'] == fecha and p['edicion'] == str(ed)} (no confiable)"
              f" | ENLACES dicen coincidir (decide): {coincide(p, fecha, ed)}")
        if args.detalle or esperado:
            for f in p["filas"][:200]:
                if es_anci(f) or es_ciber(f):
                    marca = "ANCI " if es_anci(f) else "ciber"
                    print(f"  [{marca}] {f['organismo'][:34]:34} | CVE {f['cve']} | {f['titulo'][:70]}")
    print("\nEn la línea 'ENLACES dicen coincidir' los casos con 'debe coincidir: True' deben decir True y el domingo False.")
    print("Si alguno falla, pégame esta salida completa.")


def cmd_run(args):
    carpeta = Path(args.salida); (carpeta / "pdf").mkdir(parents=True, exist_ok=True)
    if not robots_ok() and not args.ignorar_robots:
        sys.exit("robots.txt NO permite el acceso automatizado. Me detengo (--ignorar-robots solo bajo tu responsabilidad).")
    with open(carpeta / "ejecucion.txt", "a", encoding="utf-8") as f:
        f.write(f"{ahora()} | python {platform.python_version()} | {' '.join(sys.argv)}\n")
    reg_prev = leer_csv(carpeta / "registro_ediciones.csv")
    hechas = {r["fecha_edicion"] for r in reg_prev if r["estado"] in ("ok", "sin_edicion")}
    eds_previas = [int(e) for r in reg_prev for e in r["ediciones_aceptadas"].split(";") if e]
    esperada = (max(eds_previas) + 1) if eds_previas else ED_ANTERIOR_A_INICIO
    if args.ed_inicial and not eds_previas:
        esperada = args.ed_inicial       
    f_reg, w_reg = abrir_csv(carpeta / "registro_ediciones.csv", CAMPOS_REG)
    f_anci, w_anci = abrir_csv(carpeta / "actos_anci.csv", CAMPOS_ACTOS)
    f_rel, w_rel = abrir_csv(carpeta / "actos_ciber_relacionados.csv", CAMPOS_ACTOS)

    dia = dt.date.fromisoformat(args.desde)
    hasta = dt.date.fromisoformat(args.hasta)
    while dia <= hasta:
        iso = dia.isoformat()
        if iso in hechas:
            dia += dt.timedelta(days=1); continue
        reg = {c: "" for c in CAMPOS_REG}
        reg.update(fecha_edicion=iso, consultado_utc=ahora(), estado="sin_edicion")
        intentos, aceptadas, filas_dia, error = [], [], [], ""
        ed, fallidos = esperada, 0
            ventana = 1 if dia.weekday() == 6 else VENTANA   
        while fallidos < (1 if aceptadas else ventana) and len(intentos) < 8:
            u = url_listado(dia, ed)
            st, c, err = get(u)
            intentos.append(f"{ed}:{st or err}")
            if err or (st and st >= 500):
                error += f" {ed}:{err or st};"
            p = parsear_listado(c) if (st == 200 and c) else None
            if p and coincide(p, dia, ed):
                aceptadas.append(ed)
                ruta = carpeta / "html" / f"{iso}_{ed}.html.gz"
                ruta.parent.mkdir(exist_ok=True)
                with gzip.open(ruta, "wb") as g:
                    g.write(c)
                reg["html_guardado"] += str(ruta) + ";"
                for fila in p["filas"]:
                    fila.update(edicion=str(ed), url_listado=u)
                    filas_dia.append(fila)
                fallidos = 0
            else:
                fallidos += 1
            ed += 1
        if aceptadas:
            esperada = max(aceptadas) + 1
            reg["estado"] = "error" if error else "ok"
        elif error:
            reg["estado"] = "error"
        n_a = n_r = 0
        for f in filas_dia:
            tipo, num, anio, resto = desglosar_titulo(f["titulo"])
            fila = {"fecha_edicion": iso, "edicion": f["edicion"], "cve": f["cve"], "ministerio": f["ministerio"],
                    "organismo": f["organismo"], "tipo": tipo, "numero": num, "anio_acto": anio,
                    "titulo_resumen": resto, "titulo_listado": f["titulo"], "url_pdf": f["url_pdf"],
                    "url_listado": f["url_listado"], "consultado_utc": ahora()}
            if es_anci(f):
                n_a += 1; w_anci.writerow({**fila, "motivo": "emisor ANCI"})
            elif es_ciber(f):
                n_r += 1; w_rel.writerow({**fila, "motivo": "palabra clave en título"})
            else:
                continue
            if args.bajar_pdf:
                s, pdf, _ = get(f["url_pdf"])
                if s == 200 and pdf:
                    (carpeta / "pdf" / f"{iso}_{f['edicion']}_{f['cve']}.pdf").write_bytes(pdf)
        reg.update(intentos_edicion_url_status=" ".join(intentos), ediciones_aceptadas=";".join(map(str, aceptadas)),
                   n_normas_generales=len(filas_dia), n_anci=n_a, n_ciber_otros=n_r, error=error.strip())
        w_reg.writerow(reg)
        for fh in (f_reg, f_anci, f_rel):
            fh.flush()
        print(f"{iso} ed={reg['ediciones_aceptadas'] or '-'} NG={len(filas_dia)} ANCI={n_a} ciber-otros={n_r} [{reg['estado']}]")
        dia += dt.timedelta(days=1)
    for fh in (f_reg, f_anci, f_rel):
        fh.close()
    print("\nActos ANCI:")
    for r in leer_csv(carpeta / "actos_anci.csv"):
        print(f"  {r['fecha_edicion']} ed.{r['edicion']} CVE {r['cve']} | {r['tipo']} {r['numero']}/{r['anio_acto']} | {r['titulo_resumen'][:80]}")
    print("\nRevisa a mano: fechas con estado=error y semanas con muchas fechas 'sin_edicion' seguidas.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="salida_censo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe"); pr.add_argument("--detalle", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--desde", default=INICIO.isoformat()); r.add_argument("--hasta", default=FIN.isoformat())
    r.add_argument("--ed-inicial", type=int, default=None, help="edición esperada para --desde (ej. 44038 si --desde 2025-01-02)")
    r.add_argument("--bajar-pdf", action="store_true"); r.add_argument("--ignorar-robots", action="store_true")
    a = ap.parse_args()
    {"probe": cmd_probe, "run": cmd_run}[a.cmd](a)
