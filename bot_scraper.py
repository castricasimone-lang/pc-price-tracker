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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"
}

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def simplify_query(query):
    # Rimuove parole troppo lunghe che bloccano i motori di ricerca interni degli store
    stop_words = ["ATX", "AM5", "EXPO", "PCIE5", "PCIe 5.0", "(senza dissipatore)", "Dual", "MAX WIFI"]
    q = query
    for w in stop_words:
        q = re.sub(re.escape(w), "", q, flags=re.IGNORECASE)
    q = re.sub(r"\s+", " ", q).strip()
    return q

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
        return val if val > 15 else None
    except ValueError:
        return None

def scrape_trovaprezzi(query):
    offers = []
    q_clean = simplify_query(query)
    q_enc = urllib.parse.quote_plus(q_clean)
    url = f"https://www.trovaprezzi.it/categoria.aspx?id=-1&libera={q_enc}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        for item in soup.select("li.listing_item, .item_wrapper, li[data-price]")[:15]:
            title_el = item.select_one(".item_name, h2, .title")
            price_el = item.select_one(".item_total_price, .item_price, .price")
            store_el = item.select_one(".merchant_name, .merchant, img[alt]")
            link_el = item.select_one("a[href]")

            title = title_el.get_text(strip=True) if title_el else q_clean
            price = parse_price(price_el.get_text(strip=True)) if price_el else None
            store = ""
            if store_el:
                store = store_el.get("alt", "").strip() if store_el.name == "img" else store_el.get_text(strip=True)
            href = link_el["href"] if link_el else url
            if href.startswith("/"):
                href = "https://www.trovaprezzi.it" + href

            if price and store:
                offers.append({
                    "store": store,
                    "product_title": title[:85],
                    "price": price,
                    "url": href
                })
    except Exception as e:
        print(f"Trovaprezzi errore ({q_clean}): {str(e)[:60]}")
    return offers

def scrape_idealo(query):
    offers = []
    q_clean = simplify_query(query)
    q_enc = urllib.parse.quote(q_clean)
    url = f"https://www.idealo.it/risultati.html?q={q_enc}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        for item in soup.select(".sr-resultList__item, .offerList-item")[:10]:
            title_el = item.select_one(".sr-productSummary__title, [data-testid='productSummary__title']")
            price_el = item.select_one(".sr-detailedPriceInfo__price, [data-testid='detailedPriceInfo__price']")
            link_el = item.select_one("a[href]")
            if title_el and price_el:
                price = parse_price(price_el.get_text(strip=True))
                href = link_el["href"] if link_el else url
                if href.startswith("/"):
                    href = "https://www.idealo.it" + href
                if price:
                    offers.append({
                        "store": "Idealo.it (Miglior Store)",
                        "product_title": title_el.get_text(strip=True)[:85],
                        "price": price,
                        "url": href
                    })
    except Exception as e:
        print(f"Idealo errore ({q_clean}): {str(e)[:60]}")
    return offers

def scrape_ldlc(query):
    offers = []
    q_clean = simplify_query(query)
    q_enc = urllib.parse.quote(q_clean)
    url = f"https://www.ldlc.com/it-it/cerca/{q_enc}/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
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
                        "product_title": title_el.get_text(strip=True)[:85],
                        "price": price,
                        "url": href
                    })
    except Exception as e:
        print(f"LDLC errore ({q_clean}): {str(e)[:60]}")
    return offers

def search_real_offers_for_item(query, target_price):
    found = []
    found.extend(scrape_trovaprezzi(query))
    found.extend(scrape_idealo(query))
    found.extend(scrape_ldlc(query))

    # Filtra solo prezzi plausibili per evitare accessori/cavi/scatole vuote
    min_valid = target_price * 0.50
    max_valid = target_price * 1.55
    valid = [o for o in found if min_valid <= o["price"] <= max_valid]

    by_store = {}
    for o in valid:
        st = o["store"].strip()
        if st not in by_store or o["price"] < by_store[st]["price"]:
            by_store[st] = o

    return sorted(by_store.values(), key=lambda x: x["price"])[:5]

def call_openrouter(api_key, prompt):
    if not api_key:
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
            {"role": "system", "content": "Sei un esperto hardware PC. Rispondi solo con JSON valido."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=45)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"OpenRouter errore: {str(e)[:100]}")
        return None

def get_real_alternatives(api_key, config, collected_components):
    max_extra = config["max_extra_budget_eur"]
    summary = [
        {"category": c["category"], "name": c["name"], "target": c["target_price"]}
        for c in collected_components
    ]
    prompt = f"""
Per ciascuno di questi componenti PC, indica SOLO il nome esatto di 1 modello alternativo o superiore in commercio (es. GPU equivalente/superiore, CPU alternativa, ecc.) senza inventare prezzi.
Componenti:
{json.dumps(summary, ensure_ascii=False)}

Rispondi SOLO con una lista JSON:
[
  {{
    "for_component": "Nome componente originale",
    "category": "Categoria",
    "original_target": 270,
    "alt_name": "Nome modello alternativo esatto",
    "is_superior": true,
    "reason": "Perché conviene rispetto all'originale (max 10 parole)"
  }}
]
"""
    raw = call_openrouter(api_key, prompt)
    if not raw:
        return []

    real_alts = []
    try:
        match = re.search(r'\[.*\]', raw.strip(), re.DOTALL)
        if not match:
            return []
        suggestions = json.loads(match.group(0))
        for s in suggestions:
            alt_name = s.get("alt_name", "").strip()
            orig_target = float(s.get("original_target", 0))
            if not alt_name or orig_target <= 0:
                continue
            # Cerca il PREZZO REALE dell'alternativa sugli store!
            offers = search_real_offers_for_item(alt_name, orig_target)
            if offers:
                best = offers[0]
                if best["price"] <= orig_target + max_extra:
                    real_alts.append({
                        "for_component": s.get("for_component", ""),
                        "category": s.get("category", ""),
                        "original_target": orig_target,
                        "name": alt_name,
                        "store": best["store"],
                        "price": best["price"],
                        "url": best["url"],
                        "reason": s.get("reason", ""),
                        "is_better_for_less": bool(s.get("is_superior")) and (best["price"] <= orig_target)
                    })
            time.sleep(1)
    except Exception as e:
        print(f"Errore elaborazione alternative reali: {e}")
    return real_alts

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
        print(f"Ricerca prezzi reali per: {comp['name']}...")
        real_offers = search_real_offers_for_item(comp["search_query"], comp["target_price"])
        print(f" -> Trovate {len(real_offers)} offerte reali verificate")
        results["components"].append({
            "id": comp["id"],
            "category": comp["category"],
            "name": comp["name"],
            "target_price": comp["target_price"],
            "offers": real_offers
        })
        time.sleep(1)

    results["alternatives"] = get_real_alternatives(api_key, config, results["components"])
    print(f" -> Trovate {len(results['alternatives'])} alternative con prezzo reale")
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
                <td style="padding:8px;border:1px solid #ddd;"><b>{comp['category']}</b><br>{comp['name']}<br><small style="color:#666;">{best.get('product_title','')}</small></td>
                <td style="padding:8px;border:1px solid #ddd;color:#16a34a;font-weight:bold;">{float(best['price']):.2f} €</td>
                <td style="padding:8px;border:1px solid #ddd;"><a href="{best['url']}">{best['store']}</a></td>
            </tr>
            """
        else:
            html_rows += f"""
            <tr>
                <td style="padding:8px;border:1px solid #ddd;"><b>{comp['category']}</b><br>{comp['name']}</td>
                <td style="padding:8px;border:1px solid #ddd;color:#999;">Nessuna offerta valida</td>
                <td style="padding:8px;border:1px solid #ddd;">—</td>
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
        <h2>🖥️ Aggiornamento Prezzi Reali Build PC ({data['last_updated']})</h2>
        <p>Totale stimato miglior combinazione attuale: <strong style="font-size:18px;color:#16a34a;">{total_min:.2f} €</strong></p>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f3f4f6;">
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Componente</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Miglior Prezzo Reale</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Store</th>
            </tr>
            {html_rows}
        </table>
        <h3>Alternative & Upgrade con Prezzi Reali (max +150€):</h3>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f3f4f6;">
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Proposta</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Prezzo Reale</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Store</th>
            </tr>
            {alt_rows}
        </table>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🖥️ Prezzi Reali PC Aggiornati: Totale {total_min:.2f} €"
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
