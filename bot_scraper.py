import os
import json
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai
from google.genai import types

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def fetch_prices_with_ai(client, config):
    stores_str = ", ".join(config["trusted_stores"])
    max_extra = config["max_extra_budget_eur"]
    
    results = {
        "last_updated": datetime.now().strftime("%d/%m/%Y alle %H:%M"),
        "components": [],
        "alternatives": []
    }

    for comp in config["components"]:
        print(f"Ricerca prezzi per: {comp['name']}...")
        max_alt_price = comp["target_price"] + max_extra
        
        prompt = f"""
        Sei un esperto analista hardware e cercatore di prezzi in Europa.
        Cerca online i prezzi attuali in Euro (€) inclusa spedizione in Italia per il componente:
        "{comp['name']}" (Specifiche chiave: {comp['specs_notes']}, prezzo di riferimento: {comp['target_price']}€).

        Controlla esclusivamente tra questi store affidabili: {stores_str}.
        
        Compiti:
        1. Trova almeno 4-6 offerte reali e disponibili per questo esatto componente (o sue varianti dirette identiche per specifiche) su diversi store tra quelli elencati, ordinati dal prezzo più basso al più alto.
        2. Trova 2-3 prodotti ALTERNATIVI o UPGRADE nella stessa categoria ({comp['category']}) che costino al massimo {max_alt_price}€ (quindi massimo +{max_extra}€ rispetto al riferimento di {comp['target_price']}€).
           - Se trovi un componente SUPERIORE per prestazioni/qualità a un prezzo INFERIORE o uguale a {comp['target_price']}€, imposta "is_better_for_less": true.
           - Altrimenti imposta "is_better_for_less": false.

        Restituisci SOLO un oggetto JSON valido con questa esatta struttura (nessun testo fuori dal JSON):
        {{
          "offers": [
            {{
              "store": "Nome Store",
              "product_title": "Nome esatto prodotto sullo store",
              "price": 259.90,
              "url": "URL diretto o URL di ricerca sullo store",
              "condition": "Nuovo"
            }}
          ],
          "alternatives": [
            {{
              "name": "Nome componente alternativo/upgrade",
              "store": "Nome Store",
              "price": 249.00,
              "url": "URL diretto o di ricerca",
              "reason": "Breve motivo (es. +15% prestazioni a prezzo inferiore / Ottimo equivalente)",
              "is_better_for_less": true
            }}
          ]
        }}
        """

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[{"google_search": {}}],
                    temperature=0.1,
                ),
            )
            
            raw_text = response.text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            
            parsed = json.loads(raw_text.strip())
            
            # Ordina offerte per prezzo crescente
            offers = sorted(parsed.get("offers", []), key=lambda x: float(x.get("price", 99999)))
            
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
            print(f"Errore durante l'elaborazione di {comp['name']}: {e}")
            results["components"].append({
                "id": comp["id"],
                "category": comp["category"],
                "name": comp["name"],
                "target_price": comp["target_price"],
                "offers": []
            })

    return results

def send_email_report(data):
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    
    if not email_user or not email_pass:
        print("Credenziali email mancanti, salto invio.")
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
    data = fetch_prices_with_ai(client, config)
    
    with open("prezzi.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    send_email_report(data)
