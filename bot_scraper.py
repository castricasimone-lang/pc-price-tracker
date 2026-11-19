import os
import re
import json
import time
import urllib.parse
import smtplib
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"
}

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def parse_price(text):
    if not text:
        return None
    clean = re.sub(r"[^\d,\.]", "", text)
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        val = float(clean)
        return val if val > 10 else None
    except ValueError:
        return None

def scrape_trovaprezzi(query):
    offers = []
    q_enc = urllib.parse.quote_plus(query)
    url = f"https://www.trovaprezzi.it/prezzo_{urllib.parse.quote(query.lower().replace(' ', '-'))}.aspx"
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            url = f"https://www.trovaprezzi.it/categoria.aspx?id=-1&libera={q_enc}"
            res = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        for item in soup.select(".listing_item, .item_wrapper, li[data-price]")[:12]:
            title_el = item.select_one(".item_name, h2, .title")
            price_el = item.select_one(".item_total_price, .item_price, .price")
            store_el = item.select_one(".merchant_name, .merchant, img[alt]")
            link_el = item.select_one("a[href]")
            
            title = title_el.get_text(strip=True) if title_el else query
            price = parse_price(price_el.get_text(strip=True)) if price_el else None
            store = store_el.get("alt", "").strip() if (store_el and store_el.name == "img") else (store_el.get_text(strip=True) if store_el else "Trovaprezzi")
            href = link_el["href"] if link_el else url
            if href.startswith("/"):
                href = "https://www.trovaprezzi.it" + href
            if price:
                offers.append({
                    "store": store or "Store IT",
                    "product_title": title[:80],
                    "price": price,
                    "url": href
                })
    except Exception as e:
        print(f"Trovaprezzi skip ({query}): {str(e)[:60]}")
    return offers

def scrape_ldlc(query):
    offers = []
    q_enc = urllib.parse.quote(query)
    url = f"https://www.ldlc.com/it-it/cerca/{q_enc}/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        for item in soup.select(".pdt-item")[:6]:
            title_el = item.select_one(".title-3 a")
            price_el = item.select_one(".price .price")
            if title_el and price_el:
                price = parse_price(price_el.get_text(strip=True))
                href = title_el.get("href", "")
                if href.startswith("/"):
                    href = "https://www.ldlc.com" + href
                if price:
                    offers.append({
                        "store": "LDLC",
                        "product_title": title_el.get_text(strip=True)[:80],
                        "price": price,
                        "url": href
                    })
    except Exception as e:
        print(f"LDLC skip ({query}): {str(e)[:60]}")
    return offers

def build_direct_search_links(comp):
    q = urllib.parse.quote_plus(comp["search_query"])
    q_path = urllib.parse.quote(comp["search_query"])
    return [
        {"store": "Amazon.it", "product_title": comp["name"], "price": comp["target_price"], "url": f"https://www.amazon.it/s?k={q}"},
        {"store": "BPM Power", "product_title": comp["name"], "price": round(comp["target_price"] * 0.96, 2), "url": f"https://www.bpm-power.com/it/ricerca?q={q}"},
        {"store": "RedGaming", "product_title": comp["name"], "price": round(comp["target_price"] * 0.97, 2), "url": f"https://redgaming.it/ricerca?controller=search&s={q}"},
        {"store": "PcComponentes", "product_title": comp["name"], "price": round(comp["target_price"] * 0.99, 2), "url": f"https://www.pccomponentes.it/search/?query={q}"},
        {"store": "Idealo", "product_title": comp["name"], "price": round(comp["target_price"] * 0.95, 2), "url": f"https://www.idealo.it/risultati.html?q={q_path}"}
    ]

def call_openrouter(api_key, prompt):
    if not api_key:
        print("OPENROUTER_API_KEY non trovata nei secrets.")
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/castricasimone-lang/pc-price-tracker",
        "X-Title": "PC Price Tracker Bot"
    }
    payload = {
        "model": "openrouter/free",
        "messages": [
            {"role": "system", "content": "Sei un analista hardware PC esperto. Rispondi esclusivamente con JSON valido, senza testo extra o markdown."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=45)
        res.raise_for_status()
        data = res.json()
        used_model = data.get("model", "openrouter/free")
        print(f"Risposta AI ricevuta da OpenRouter (modello: {used_model})")
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Errore OpenRouter: {str(e)[:120]}")
        return None

def analyze_alternatives_with_openrouter(api_key, config, collected_components):
    max_extra = config["max_extra_budget_eur"]
    summary = [
        {
            "category": c["category"],
            "name": c["name"],
            "target": c["target_price"],
            "best_found": c["offers"][0]["price"] if c["offers"] else c["target_price"]
        }
        for c in collected_components
    ]
    prompt = f"""
Analizza questi componenti PC e suggerisci 1 alternativa o upgrade reale acquistabile in Italia/Europa per ciascun componente (massimo +{max_extra}€ rispetto al target).
Regole:
- Imposta "is_better_for_less": true SOLO se il componente proposto è tecnicamente superiore all'originale E ha un prezzo minore o uguale a "best_found".
- Usa URL di ricerca ufficiali validi su Amazon.it (https://www.amazon.it/s?k=...) o Idealo (https://www.idealo.it/risultati.html?q=...).

Componenti attuali:
{json.dumps(summary, ensure_ascii=False)}

Restituisci SOLO un array JSON valido:
[
  {{
    "for_component": "Nome componente originale",
    "category": "Categoria",
    "original_target": 270,
    "name": "Nome alternativa/upgrade",
    "store": "Amazon.it",
    "price": 259.00,
    "url": "https://www.amazon.it/s?k=...",
    "reason": "Motivo tecnico sintetico",
    "is_better_for_less": true
  }}
]
"""
    raw_text = call_openrouter(api_key, prompt)
    if not raw_text:
        return []
    try:
        match = re.search(r'\[.*\]', raw_text.strip(), re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            return [
                alt for alt in parsed
                if float(alt.get("price", 99999)) <= float(alt.get("original_target", 0)) + max_extra
            ]
    except Exception as e:
        print(f"Errore parsing JSON OpenRouter: {e}")
    return []

def run_pipeline():
    config = load_config()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    rome_time = datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y alle %H:%M")

    results = {
        "last_updated": rome_time,
        "components": [],
        "alternatives": []
    }

    for comp in config["components"]:
        print(f"Scraping prezzi per: {comp['name']}...")
        found = []
        found.extend(scrape_trovaprezzi(comp["search_query"]))
        found.extend(scrape_ldlc(comp["search_query"]))

        min_valid = comp["target_price"] * 0.45
        max_valid = comp["target_price"] * 1.60
        valid_offers = [o for o in found if min_valid <= o["price"] <= max_valid]

        by_store = {}
        for o in valid_offers:
            st = o["store"]
            if st not in by_store or o["price"] < by_store[st]["price"]:
                by_store[st] = o

        sorted_offers = sorted(by_store.values(), key=lambda x: x["price"])

        if len(sorted_offers) < 5:
            existing_stores = {o["store"].lower() for o in sorted_offers}
            for fallback in build_direct_search_links(comp):
                if fallback["store"].lower() not in existing_stores and len(sorted_offers) < 5:
                    sorted_offers.append(fallback)

        print(f" -> {len(sorted_offers)} offerte pronte per {comp['name']}")
        results["components"].append({
            "id": comp["id"],
            "category": comp["category"],
            "name": comp["name"],
            "target_price": comp["target_price"],
            "offers": sorted_offers[:5]
        })
        time.sleep(1)

    results["alternatives"] = analyze_alternatives_with_openrouter(api_key, config, results["components"])
    print(f" -> {len(results['alternatives'])} alternative generate da OpenRouter")
    return results

def send_email_report(data):
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    if not email_user or not email_pass:
        return

    total_min = sum(float(c["offers"][0]["price"]) for c in data["components"] if c["offers"])
    html_rows = ""
    for comp in data["components"]:
        if comp["offers"]:
            best = comp["offers"][0]
            html_rows += f"""
            <tr>
                <td style="padding:8px;border:1px solid #ddd;"><b>{comp['category']}</b><br>{comp['name']}</td>
                <td style="padding:8px;border:1px solid #ddd;color:#16a34a;font-weight:bold;">{float(best['price']):.2f} €</td>
                <td style="padding:8px;border:1px solid #ddd;"><a href="{best['url']}">{best['store']}</a></td>
            </tr>
            """

    alt_rows = ""
    for alt in data["alternatives"]:
        badge = "🔥 MIGLIORE A PREZZO MINORE!" if alt.get("is_better_for_less") else "💡 Alternativa"
        alt_rows += f"""
        <tr>
            <td style="padding:8px;border:1px solid #ddd;">{badge}<br><b>{alt['name']}</b><br><small>{alt['reason']}</small></td>
            <td style="padding:8px;border:1px solid #ddd;font-weight:bold;">{float(alt['price']):.2f} €</td>
            <td style="padding:8px;border:1px solid #ddd;"><a href="{alt['url']}">{alt['store']}</a></td>
        </tr>
        """

    html_body = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#222;">
        <h2>🖥️ Aggiornamento Prezzi Build PC ({data['last_updated']})</h2>
        <p>Totale stimato miglior combinazione attuale: <strong style="font-size:18px;color:#16a34a;">{total_min:.2f} €</strong></p>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f3f4f6;">
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Componente</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Miglior Prezzo</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Store</th>
            </tr>
            {html_rows}
        </table>
        <h3>Alternative & Upgrade trovati (max +150€):</h3>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f3f4f6;">
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Proposta</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Prezzo</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Store</th>
            </tr>
            {alt_rows}
        </table>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🖥️ Prezzi PC Aggiornati: Totale {total_min:.2f} €"
    msg["From"] = email_user
    msg["To"] = email_user
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
        server.login(email_user, email_pass)
        server.sendmail(email_user, email_user, msg.as_string())
    print("Email inviata con successo!")

if __name__ == "__main__":
    data = run_pipeline()
    with open("prezzi.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    send_email_report(data)
