import os
import csv
import logging
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ---------------- DEBUG helper ----------------
def debug(msg):
    # Activa/desactiva con DEBUG_TELEGRAM=true/false (GitHub Actions → Variables)
    if os.getenv("DEBUG_TELEGRAM", "true").lower() == "true":
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": USER_ID, "text": f"🛠 {msg}"}
            )
        except Exception:
            pass

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0") or "0")
KEYWORDS_INCLUDE = [k.strip() for k in os.getenv("KEYWORDS_INCLUDE", "").split(",") if k.strip()]
KEYWORDS_EXCLUDE = [k.strip().lower() for k in os.getenv("KEYWORDS_EXCLUDE", "").split(",") if k.strip()]
LOCATIONS_INCLUDE = [l.strip() for l in os.getenv("LOCATIONS_INCLUDE", "").split(",") if l.strip()]
REMOTE_ALLOWED = os.getenv("REMOTE_ALLOWED", "true").lower() == "true"
HOURS_BACK = int(os.getenv("HOURS_BACK", "24") or "24")
RUN_MODE = os.getenv("RUN_MODE", "cron")  # cron o bot
HISTORIC_FILE = "ofertas_historico.csv"
LOG_FILE = "log.txt"

# InfoJobs (opcional): si no hay API key, se omite
INFOJOBS_API_KEY = os.getenv("INFOJOBS_API_KEY", "").strip()

# ---------------- LOGGING ----------------
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s - %(message)s")

# ---------------- UTILIDADES ----------------
def oferta_valida(oferta):
    titulo = (oferta.get("titulo") or "")
    empresa = (oferta.get("empresa") or "")
    ubicacion = (oferta.get("ubicacion") or "")
    blob_lc = f"{titulo} {empresa}".lower()
    ubic_lc = ubicacion.lower()

    # include/exclude
    inc = [k.lower() for k in KEYWORDS_INCLUDE]
    if inc and not any(k in blob_lc for k in inc):
        return False
    if KEYWORDS_EXCLUDE and any(k in blob_lc for k in KEYWORDS_EXCLUDE):
        return False

    # remoto/local
    es_remoto = any(x in (blob_lc + " " + ubic_lc) for x in ["remote", "remoto", "teletrabajo", "hybrid", "híbrido"])
    if es_remoto and REMOTE_ALLOWED:
        pass
    else:
        locs = [l.lower() for l in LOCATIONS_INCLUDE]
        if locs and not any(l in ubic_lc for l in locs):
            return False

    # fecha -> nuestros scrapers ponen fecha=now (dentro de la ventana)
    return True

def ya_en_historico(link):
    if not os.path.exists(HISTORIC_FILE):
        return False
    with open(HISTORIC_FILE, newline='', encoding="utf-8") as f:
        return any(row[5] == link for row in csv.reader(f))

def guardar_historico(ofertas):
    existe = os.path.exists(HISTORIC_FILE)
    with open(HISTORIC_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["fecha_extraccion", "portal", "titulo", "empresa", "ubicacion", "link"])
        for o in ofertas:
            writer.writerow([datetime.now().isoformat(), o["portal"], o["titulo"], o["empresa"], o["ubicacion"], o["link"]])

def formatear_mensaje(ofertas):
    if not ofertas:
        return "⚠️ Hoy no se han encontrado ofertas que cumplan tus requisitos."
    msg = "📢 *Ofertas encontradas:*\n\n"
    for o in ofertas:
        msg += f"📌 *{o['titulo']}*\n"
        msg += f"🏢 {o['empresa']}\n"
        msg += f"📍 {o['ubicacion']}\n"
        msg += f"🗓 {o['fecha'].strftime('%d/%m/%Y')}\n"
        msg += f"🔗 [Ver oferta]({o['link']})\n\n"
    return msg

# ---------------- SCRAPERS ----------------
def scrape_linkedin():
    """
    Para cada keyword en KEYWORDS_INCLUDE:
      - busca remoto (si REMOTE_ALLOWED) con location=Spain y f_TPR (últimas X horas)
      - busca por cada ubicación de LOCATIONS_INCLUDE
    """
    ofertas = []
    from urllib.parse import quote
    secs = max(1, HOURS_BACK) * 3600
    f_tpr = f"r{secs}"

    searches = []
    if REMOTE_ALLOWED:
        for kw in KEYWORDS_INCLUDE:
            searches.append({"keywords": kw, "location": "Spain", "f_TPR": f_tpr, "f_WT": "2"})  # 2=remote
    for loc in LOCATIONS_INCLUDE:
        for kw in KEYWORDS_INCLUDE:
            searches.append({"keywords": kw, "location": loc, "f_TPR": f_tpr})

    def build_url(p):
        base = "https://www.linkedin.com/jobs/search/"
        qs = "&".join([f"{k}={quote(str(v))}" for k, v in p.items() if v])
        return f"{base}?{qs}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(60000)

            total = 0
            for s in searches:
                url = build_url(s)
                page.goto(url)
                # Carga más resultados
                for _ in range(6):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(700)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select("li[data-occludable-job-id], div.base-card")
                for c in cards:
                    title_el = c.select_one("h3") or c.select_one("a")
                    title = title_el.get_text(strip=True) if title_el else ""
                    comp_el = c.select_one(".base-search-card__subtitle a, .hidden-nested-link, .base-search-card__subtitle")
                    company = comp_el.get_text(strip=True) if comp_el else ""
                    loc_el = c.select_one(".job-search-card__location, .base-search-card__metadata span")
                    ubic = loc_el.get_text(strip=True) if loc_el else s.get("location", "")
                    link_el = c.select_one("a.base-card__full-link") or c.select_one("a")
                    href = (link_el.get("href") or "") if link_el else ""
                    link = href.split("?")[0]
                    if not title or not link:
                        continue
                    ofertas.append({
                        "portal": "LinkedIn",
                        "titulo": title,
                        "empresa": company,
                        "ubicacion": ubic,
                        "fecha": datetime.now(),
                        "link": link
                    })
                    total += 1
            browser.close()
            debug(f"LinkedIn: {total} tarjetas crudas")
    except Exception as e:
        logging.error(f"LinkedIn scrape error: {e}")
        debug(f"LinkedIn error: {e}")
    return ofertas

def scrape_infojobs():
    """
    Si no hay INFOJOBS_API_KEY, devolvemos 0 ofertas (fuente opcional).
    """
    ofertas = []
    if not INFOJOBS_API_KEY:
        debug("InfoJobs omitido (sin INFOJOBS_API_KEY)")
        return ofertas

    url = "https://api.infojobs.net/api/7/offer?maxResults=20&maxDaysOld=1"
    headers = {"Authorization": f"Basic {INFOJOBS_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            for job in r.json().get("offers", []):
                ofertas.append({
                    "portal": "InfoJobs",
                    "titulo": job.get("title", ""),
                    "empresa": (job.get("author") or {}).get("name", ""),
                    "ubicacion": job.get("city", ""),
                    "fecha": datetime.now(),
                    "link": job.get("link", "")
                })
        else:
            debug(f"InfoJobs status {r.status_code}")
    except Exception as e:
        logging.error(f"InfoJobs error: {e}")
        debug(f"InfoJobs error: {e}")
    return ofertas

def scrape_indeed():
    ofertas = []
    base = "https://es.indeed.com/jobs"
    headers = {"User-Agent": "Mozilla/5.0"}
    searches = []

    if REMOTE_ALLOWED:
        for kw in KEYWORDS_INCLUDE:
            searches.append({"q": kw, "l": "España", "fromage": "1"})
    for loc in LOCATIONS_INCLUDE:
        for kw in KEYWORDS_INCLUDE:
            searches.append({"q": kw, "l": loc, "fromage": "1"})

    total = 0
    for s in searches:
        try:
            r = requests.get(base, params=s, headers=headers, timeout=30)
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("a.tapItem, div.job_seen_beacon")
            for card in cards:
                t = card.select_one("h2.jobTitle span")
                title = t.get_text(strip=True) if t else ""
                ce = card.select_one("span.companyName")
                company = ce.get_text(strip=True) if ce else ""
                le = card.select_one("div.companyLocation")
                ubic = le.get_text(" ", strip=True) if le else s.get("l", "")
                href = card.get("href") if card.name == "a" else (card.select_one("a.tapItem") or {}).get("href", "")
                link = href if (href and href.startswith("http")) else (f"https://es.indeed.com{href}" if href else "")
                if not title or not link:
                    continue
                ofertas.append({
                    "portal": "Indeed",
                    "titulo": title,
                    "empresa": company,
                    "ubicacion": ubic,
                    "fecha": datetime.now(),
                    "link": link
                })
                total += 1
        except Exception as e:
            logging.error(f"Indeed error {s}: {e}")
            debug(f"Indeed error {s}: {e}")
    debug(f"Indeed: {total} tarjetas crudas")
    return ofertas

# ---------------- BOT ----------------
def buscar_y_enviar(update: Update = None, context: CallbackContext = None):
    try:
        # 1) Scrape
        todas = scrape_linkedin() + scrape_infojobs() + scrape_indeed()
        tot_li = len([o for o in todas if o["portal"] == "LinkedIn"])
        tot_ij = len([o for o in todas if o["portal"] == "InfoJobs"])
        tot_in = len([o for o in todas if o["portal"] == "Indeed"])
        debug(f"Crudas → LinkedIn:{tot_li} | InfoJobs:{tot_ij} | Indeed:{tot_in}")

        # 2) DEDUPLICACIÓN (título+empresa+link)
        vistos = set()
        sin_dupes = []
        for o in todas:
            key = (o.get("titulo","").strip().lower(),
                   o.get("empresa","").strip().lower(),
                   o.get("link","").strip().lower())
            if key in vistos:
                continue
            vistos.add(key)
            sin_dupes.append(o)
        todas = sin_dupes
        debug(f"Tras dedupe: {len(todas)}")

        # 3) Filtrar + histórico
        nuevas = [o for o in todas if oferta_valida(o) and not ya_en_historico(o["link"])]
        debug(f"Después de filtros: {len(nuevas)}")
        for o in nuevas[:3]:
            debug(f"✅ {o['portal']} · {o['titulo']} · {o['ubicacion']}")

        guardar_historico(nuevas)
        msg = formatear_mensaje(nuevas)

        # 4) Envío
        if update:
            update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                params={"chat_id": USER_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True}
            )
        logging.info(f"{len(nuevas)} ofertas enviadas.")
    except Exception as e:
        logging.error(f"Error en búsqueda: {e}")
        debug(f"Error en búsqueda: {e}")

def cmd_hoy(update: Update, context: CallbackContext):
    buscar_y_enviar(update, context)

if __name__ == "__main__":
    if RUN_MODE == "cron":
        buscar_y_enviar()
    elif RUN_MODE == "bot":
        updater = Updater(BOT_TOKEN)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("hoy", cmd_hoy))
        updater.start_polling()
        updater.idle()
