import os
import sys
import json
import asyncio
from datetime import datetime
from dateutil import parser
from icalendar import Calendar, Event, vText
from playwright.async_api import async_playwright
import nest_asyncio

nest_asyncio.apply()

def load_json_data(data):
    events = []
    if isinstance(data, list):
        for item in data:
            events.extend(load_json_data(item))
    elif isinstance(data, dict):
        if 'startDate' in data or 'start' in data:
            events.append(data)
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                events.extend(load_json_data(value))
    return events

def extract_label(val):
    if isinstance(val, dict):
        caption = val.get('caption')
        if isinstance(caption, dict):
            return caption.get('fr') or next(iter(caption.values()), '')
        return val.get('name') or val.get('label') or val.get('code') or ''
    return str(val) if val is not None else ''

def parse_resources(resources_list):
    teachers, rooms = [], []
    if not isinstance(resources_list, list): return teachers, rooms
    for res in resources_list:
        if not isinstance(res, dict): continue
        category = res.get('category', '')
        name = extract_label(res.get('resource')) or res.get('code', '')
        if category in ['INSTRUCTOR', 'TEACHER'] and name: teachers.append(name)
        elif category in ['ROOM', 'LOCATION'] and name: rooms.append(name)
    return teachers, rooms

def convert_json_to_ics(json_data, output_ics_path="emploi_du_temps.ics"):
    events_data = load_json_data(json_data)
    seen = set()
    unique_events = []
    for item in events_data:
        if not isinstance(item, dict): continue
        start_str = item.get('startDate') or item.get('start')
        summary = extract_label(item.get('course')) or item.get('summary') or "Cours"
        identifier = (start_str, summary)
        if identifier not in seen:
            seen.add(identifier)
            unique_events.append(item)

    if not unique_events: return False

    cal = Calendar()
    cal.add('prodid', '-//ESME Schedule Scraper//my.esme.fr//FR')
    cal.add('version', '2.0')

    count = 0
    for item in unique_events:
        summary = extract_label(item.get('course')) or extract_label(item.get('field')) or extract_label(item.get('activityType')) or item.get('summary') or "Cours"
        start_str = item.get('startDate') or item.get('start')
        end_str = item.get('endDate') or item.get('end')
        
        if not start_str or not end_str: continue

        try:
            dt_start, dt_end = parser.parse(start_str), parser.parse(end_str)
        except Exception: continue

        teachers, rooms = parse_resources(item.get('interventionResources', []))
        location = ", ".join(rooms) if rooms else item.get('location', '')
        teacher_str = ", ".join(teachers) if teachers else item.get('teacher', '')
        
        description = item.get('description') or item.get('memo') or ""
        if teacher_str: description = f"Enseignant : {teacher_str}\n" + description

        event = Event()
        event.add('summary', summary)
        event.add('dtstart', dt_start)
        event.add('dtend', dt_end)
        if location: event.add('location', vText(location))
        if description: event.add('description', description)
        event.add('dtstamp', datetime.now())
        
        cal.add_component(event)
        count += 1

    if count > 0:
        with open(output_ics_path, 'wb') as f:
            f.write(cal.to_ical())
        print(f"✅ Succès ! {count} cours exportés dans '{output_ics_path}'.")
        return True
    return False

async def main():
    email = os.environ.get("ESME_USER")
    password = os.environ.get("ESME_PASSWORD")
    if not email or not password:
        print("❌ Erreur : Les variables d'environnement ESME_USER et ESME_PASSWORD sont requises.")
        sys.exit(1)

    print("🚀 Lancement du navigateur de scraping...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()

        print("1. Connexion au portail Keycloak...")
        await page.goto("https://my.esme.fr/", wait_until="networkidle")
        await page.fill('#username', email)
        await page.fill('#password', password)
        await page.click('#kc-login')
        await page.wait_for_load_state("networkidle")
        
        print("2. Capture de la requête officielle...")
        async with page.expect_request(lambda req: "/api/plannings/me" in req.url) as req_info:
            await page.goto("https://my.esme.fr/#/mainContent/menuEntry/227/planning")

        original_request = await req_info.value
        headers = original_request.headers

        print("3. Téléchargement de l'année scolaire (2026-2027)...")
        full_year_url = "https://my.esme.fr/api/plannings/me?days=1&days=2&days=3&days=4&days=5&days=6&days=7&startDate=2026-09-01&endDate=2027-07-31"
        api_response = await context.request.get(full_year_url, headers=headers)

        if not api_response.ok:
            print(f"❌ Erreur API HTTP {api_response.status}")
            await browser.close()
            sys.exit(1)

        planning_data = await api_response.json()
        await browser.close()

        print("4. Conversion en ICS...")
        if not convert_json_to_ics(planning_data, "emploi_du_temps.ics"):
            print("❌ Aucun cours n'a pu être converti.")
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())