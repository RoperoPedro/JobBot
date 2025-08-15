# Bot de Ofertas de Empleo

## Descripción
Bot que extrae ofertas de **LinkedIn**, **InfoJobs** e **Indeed**, filtra según palabras clave y ubicación, y las envía por **Telegram** cada mañana.  
Guarda histórico en **CSV** y **log**.

---

## Variables de entorno
Configura en Railway las siguientes variables:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
KEYWORDS_INCLUDE=Data Scientist,Científico de Datos,Machine Learning Engineer,IA,Artificial Intelligence,AI Engineer,Estadístico
KEYWORDS_EXCLUDE=Data Engineer,BI Developer,ETL,Prácticas,Beca
LOCATIONS_INCLUDE=Badajoz,Extremadura
REMOTE_ALLOWED=true
HOURS_BACK=24
RUN_MODE=cron
```

---

## Instalación en Railway

1. **Subir el código** a un repositorio en GitHub (`main.py` y `requirements.txt`).
2. **Conectar el repositorio** a Railway.
3. **Configurar las variables** de entorno anteriores.
4. **Instalar Playwright** ejecutando en la consola de Railway:
   ```
   python -m playwright install --with-deps chromium
   ```
5. **Crear un Cron Job** en Railway:
   - Hora: `0 7 * * *` (07:00 todos los días).
   - Comando:
     ```
     python main.py
     ```

---

## Modo manual
Si quieres pedir ofertas en cualquier momento:
1. Duplica el servicio en Railway.
2. Cambia la variable:
   ```
   RUN_MODE=bot
   ```
3. Envía `/hoy` en Telegram a tu bot.
