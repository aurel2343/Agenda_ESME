import os
import sys
import json
from datetime import datetime
from playwright.async_api import async_playwright
import nest_asyncio

nest_asyncio.apply()

def convert_json_to_ics(json_data, output_ics_path="emploi_du_temps.ics"):
    interventions = json_data.get("interventions", [])
    if not interventions:
        print("❌ Aucune intervention trouvée dans le JSON.")
        return False

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ESME Planning Exporter//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]

    count = 0
    for intervention in interventions:
        event_id = intervention.get('id', 'unknown')
        uid = f"esme-{event_id}@esme.fr"
        
        start_str = intervention.get("startDateTime")
        end_str = intervention.get("endDateTime")
        
        if not start_str or not end_str:
            continue
            
        # Conversion du format ISO (ex: 2026-09-14T06:30:00Z) vers le format ICS (YYYYMMDDTHHMMSSZ)
        dtstart = start_str.replace("-", "").replace(":", "")
        dtend = end_str.replace("-", "").replace(":", "")
        
        # Extraction du domaine / matière
        field_name = ""
        if "field" in intervention and isinstance(intervention["field"], dict):
            field_caption = intervention["field"].get("caption")
            if isinstance(field_caption, dict):
                field_name = field_caption.get("fr", "")
            
        # Extraction du type d'activité (CM, TD, etc.)
        activity_name = ""
        if "activityType" in intervention and isinstance(intervention["activityType"], dict):
            act_caption = intervention["activityType"].get("caption")
            if isinstance(act_caption, dict):
                activity_name = act_caption.get("fr", "")
            
        # Extraction du nom du cours (unité pédagogique)
        course_name = ""
        ped_units = intervention.get("interventionPedagogicalUnits", [])
        if ped_units and isinstance(ped_units[0], dict) and "pedagogicalUnit" in ped_units[0]:
            pu_caption = ped_units[0]["pedagogicalUnit"].get("caption", {})
            if isinstance(pu_caption, dict):
                course_name = pu_caption.get("fr", "")
            
        # Construction du résumé (titre de l'événement)
        summary = course_name if course_name else (field_name if field_name else "Cours ESME")
        if activity_name:
            summary = f"{summary} ({activity_name})"
            
        # Extraction des intervenants
        instructors = []
        for inst in intervention.get("interventionInstructors", []):
            if isinstance(inst, dict):
                person = inst.get("person", {})
                fname = person.get("currentFirstName", "")
                lname = person.get("currentLastName", "")
                if fname or lname:
                    instructors.append(f"{fname} {lname}".strip())
        instructor_str = ", ".join(instructors)
        
        # Extraction des salles de cours
        rooms = []
        for res in intervention.get("interventionResources", []):
            if isinstance(res, dict):
                r = res.get("resource", {})
                if r.get("isRoom"):
                    cap_dict = r.get("caption", {})
                    cap = cap_dict.get("fr") if isinstance(cap_dict, dict) else None
                    if not cap:
                        cap = r.get("code", "")
                    if cap:
                        rooms.append(cap)
        location = ", ".join(rooms)
        
        # Construction de la description
        desc_parts = []
        if field_name:
            desc_parts.append(f"Matière : {field_name}")
        if instructor_str:
            desc_parts.append(f"Intervenant(e) : {instructor_str}")
        if activity_name:
            desc_parts.append(f"Type : {activity_name}")
        description = "\\n".join(desc_parts)
        
        # Ajout des lignes de l'événement au calendrier
        ics_lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{summary}",
            f"LOCATION:{location}" if location else "",
            f"DESCRIPTION:{description}" if description else "",
            "END:VEVENT"
        ])
        count += 1

    ics_lines.append("END:VCALENDAR")

    # Écriture du fichier ICS final
    ics_content = "\r\n".join([line for line in ics_lines if line])
    with open(output_ics_path, "wb") as f:
        f.write(ics_content.encode("utf-8"))
        
    print(f"✅ Export réussi : {count} événements exportés vers '{output_ics_path}'")
    return count > 0

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
        try:
            await page.goto("https://my.esme.fr/", wait_until="networkidle")
            await page.fill('#username', email)
            await page.fill('#password', password)
            await page.click('#kc-login')
            await page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"❌ Échec de la connexion : {e}")
            await browser.close()
            sys.exit(1)
        
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
