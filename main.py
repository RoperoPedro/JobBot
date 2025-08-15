import os
import csv
import logging
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))

requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    data={"chat_id": USER_ID, "text": "🚀 El bot se ha iniciado correctamente desde GitHub Actions"}
)

KEYWORDS_INCLUDE = [k.strip().lower() for k in os.getenv("KEYWORDS_INCLUDE", "").split(",")]
KEYWORDS_EXCLUDE = [k.strip().lower() for k in os.getenv("KEYWORDS_EXCLUDE", "").split(",")]
LOCATIONS_INCLUDE = [l.strip().lower() for l in os.getenv("LOCATIONS_INCLUDE", "").split(",")]
REMOTE_ALLOWED = os.getenv("REMOTE_ALLOWED", "true").lower() == "true"
HOURS_BACK = int(os.getenv("HOURS_BACK", "24"))
RUN_MODE = os.getenv("RUN_MODE", "cron")  # cron o bot
HISTORIC_FILE = "ofertas_historico.csv"
LOG_FILE = "log.txt"

# ---------------- LOGGING ----------------
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s - %(message)s")

# ---------------- UTILIDADES ----------------
def oferta_valida(oferta):
    titulo = oferta["titulo"].lower()
    ubicacion = oferta["ubicacion"].lower()
    fecha_pub = oferta["fecha"]

    # Palabras incluidas
    if KEYWORDS_INCLUDE and not any(k in titulo for k in KEYWORDS_INCLUDE):
        return False

    # Palabras excluidas
    if any(k in titulo for k in KEYWORDS_EXCLUDE):
        return False

    # Ubicación
    if not REMOTE_ALLOWED:
        if not any(loc in ubicacion for loc in LOCATIONS_INCLUDE):
            return False

    # Fecha últimas X horas
    if datetime.now() - fecha_pub > timedelta(hours=HOURS_BACK):
        return False

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
    ofertas = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.linkedin.com/jobs/search/?f_TPR=r86400&keywords=data&location=Spain")
        page.wait_for_timeout(3000)
        soup = BeautifulSoup(page.content(), "html.parser")
        for job in soup.select(".base-card"):
            titulo = job.select_one(".base-search-card__title").get_text(strip=True)
            empresa = job.select_one(".base-search-card__subtitle").get_text(strip=True)
            ubicacion = job.select_one(".job-search-card__location").get_text(strip=True)
            link = job.select_one("a")["href"].split("?")[0]
            fecha = datetime.now()  # LinkedIn muestra solo "hace X horas"
            ofertas.append({"portal": "LinkedIn", "titulo": titulo, "empresa": empresa, "ubicacion": ubicacion, "fecha": fecha, "link": link})
        browser.close()
    return ofertas

def scrape_infojobs():
    ofertas = []
    url = "https://api.infojobs.net/api/7/offer?maxResults=20&maxDaysOld=1"
    headers = {"Authorization": "Basic TU_API_KEY"}  # Sustituir con API Key si se quiere usar InfoJobs real
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        for job in r.json().get("offers", []):
            ofertas.append({"portal": "InfoJobs", "titulo": job["title"], "empresa": job["author"]["name"], "ubicacion": job["city"], "fecha": datetime.now(), "link": job["link"]})
    return ofertas

def scrape_indeed():
    ofertas = []
    url = "https://es.indeed.com/jobs?q=data&fromage=1&l=España"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    for job in soup.select("td.resultContent"):
        titulo = job.select_one("h2 span").get_text(strip=True)
        empresa = job.select_one(".companyName").get_text(strip=True) if job.select_one(".companyName") else ""
        ubicacion = job.select_one(".companyLocation").get_text(strip=True) if job.select_one(".companyLocation") else ""
        link = "https://es.indeed.com" + job.select_one("a")["href"]
        fecha = datetime.now()
        ofertas.append({"portal": "Indeed", "titulo": titulo, "empresa": empresa, "ubicacion": ubicacion, "fecha": fecha, "link": link})
    return ofertas

# ---------------- BOT ----------------
def buscar_y_enviar(update: Update = None, context: CallbackContext = None):
    try:
        todas = scrape_linkedin() + scrape_infojobs() + scrape_indeed()
        nuevas = [o for o in todas if oferta_valida(o) and not ya_en_historico(o["link"])]
        guardar_historico(nuevas)
        msg = formatear_mensaje(nuevas)
        if update:
            update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", params={"chat_id": USER_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True})
        logging.info(f"{len(nuevas)} ofertas enviadas.")
    except Exception as e:
        logging.error(f"Error en búsqueda: {e}")

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
