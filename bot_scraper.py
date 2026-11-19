import os
import re
import json
import time
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from ddgs import DDGS
from google import genai
from google.genai import types

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def extract_json_from_text(text):
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)

def web_search_offers(query):
    snippets = []
    try:
        with DDGS() as ddgs:
            search_q = f"{query} prezzo euro acquista"
            results = list(ddgs.text(search_q, region="it-it", max_results=12))
            for r in results:
                snippets.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", "")
                })
    except Exception as e:
        print(f"Errore ricerca web per {query}: {e}")
    return snippets

def generate_with_fallback(client, prompt):
    models_to_try = [
        "gemini-2.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-2.0-flash-lite",
        "gemma-3-27b-it"
    ]
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1),
            )
            if response and response.text:
                return response.text
        except Exception as e:
            print(f"Modello {model_name} non disponibile ({str(e)[:80]}), provo il successivo...")
            time.sleep(1)
    raise RuntimeError("Nessun modello AI disponibile al momento.")

def fetch_prices(client, config):
    stores_str = ", ".join(config["trusted_stores"])
    max_extra = config["max_extra_budget_eur"]
    rome_time = datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y alle %H:%M")
    
    results = {
        "last_updated": rome_time,
        "components": [],
        "alternatives": []
    }

    for comp in config["components"]:
        print(f"Ricerca web per: {comp['name']}...")
        max_alt_price = comp["target_price"] + max_extra
        
        raw_results = web_search_offers(comp["search_query"])
        alt_results = web_search_offers(f"migliore alternativa {comp['category']} {comp['specs_notes']}")
        
        prompt = f"""
Sei un analista hardware. Analizza questi risultati di ricerca web reali e estrai i prezzi in Euro (€) per:
Componente: {comp['name']}
Specifiche: {comp['specs_notes']}
Prezzo target: {comp['target_price']} €
Store affidabili: {stores_str}

Risultati web trovati:
{json.dumps(raw_results, ensure_ascii=False)}

Risultati web per alternative:
{json.dumps(alt_results, ensure_ascii=False)}

Compiti:
1. "offers": Estrai fino a 5 offerte reali dai risultati web sopra (o dai principali store affidabili se il prezzo è presente nello snippet/titolo), ordinate dal prezzo più basso al più alto. Usa l'URL reale presente nei risultati. Se ci sono meno di 5 prezzi esatti nei risultati, aggiungi comunque i link di ricerca diretta sui principali store (Amazon.it, BPM Power, RedGaming, LDLC, PcComponentes) con prezzo stimato di mercato realistico.
2. "alternatives": Estrai 2-3 componenti alternativi o superiori nella categoria "{comp['category']}" con prezzo massimo {max_alt_price} €.
   - Imposta "is_better_for_less": true SOLO se il pezzo alternativo è tecnicamente superiore a "{comp['name']}" E costa meno o uguale a {comp['target_price']} €.

Restituisci SOLO un JSON valido:
{{
  "offers": [
    {{
      "store": "Nome Store",
      "product_title": "Titolo prodotto",
      "price": 259.90,
      "url": "https://..."
    }}
  ],
  "alternatives": [
    {{
      "name": "Nome componente alternativo",
      "store": "Nome Store",
      "price": 245.00,
      "url": "https://...",
      "reason": "Motivo breve",
      "is_better_for_less": true
    }}
  ]
}}
"""

        try:
            raw_text = generate_with_fallback(client, prompt)
            parsed = extract_json_from_text(raw_text)
            offers = sorted(parsed.get("offers", []), key=lambda x: float(x.get("price", 99999)))
            print(f" -> Trovate {len(offers)} offerte per {comp['name']}")
            
            results["components"].append({
                "id": comp["id"],
                "category": comp["category"],
                "name": comp["name"],
                "target_price": comp["target_price"],
                "offers": offers
            })

            for alt in parsed.get("alternatives", []):
                if float(alt.get("price", 99999)) <= max_alt_price:
                    alt["for_component"] = comp["name"]
                    alt["category"] = comp["category"]
                    alt["original_target"] = comp["target_price"]
                    results["alternatives"].append(alt)

        except Exception as e:
            print(f"Errore su {comp['name']}: {e}")
            results["components"].append({
                "id": comp["id"],
                "category": comp["category"],
                "name": comp["name"],
                "target_price": comp["target_price"],
                "offers": []
            })
        
        time.sleep(2)

    return results

def send_email_report(data):
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    
    if not email_user or not email_pass:
        return

    total_min = 0
    html_rows = ""
    for comp in data["components"]:
        if comp["offers"]:
            best = comp["offers"][0]
            price = float(best["price"])
            total_min += price
            html_rows += f"""
            <tr>
                <td style="padding:8px;border:1px solid #ddd;"><b>{comp['category']}</b><br>{comp['name']}</td>
                <td style="padding:8px;border:1px solid #ddd;color:#16a34a;font-weight:bold;">{price:.2f} €</td>
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
        <h3>Migliori prezzi per i tuoi componenti:</h3>
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

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(email_user, email_pass)
        server.sendmail(email_user, email_user, msg.as_string())
    print("Email inviata con successo!")

if __name__ == "__main__":
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    config = load_config()
    data = fetch_prices(client, config)
    
    with open("prezzi.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    send_email_report(data)
