import asyncio
from datetime import datetime
import json
import os
import re
import sys
import uuid
from playwright.async_api import async_playwright
import nest_asyncio

nest_asyncio.apply()


def convert_json_to_exact_ics(json_data, ics_filepath="emploi_du_temps.ics"):
  ics_lines = [
      "BEGIN:VCALENDAR",
      "PRODID:-//github.com/rianjs/ical.net//NONSGML ical.net 2.2//EN",
      "VERSION:2.0",
  ]

  interventions = json_data.get("interventions", [])

  for intervention in interventions:
    event_id = intervention.get("id", "unknown")
    uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"esme-{event_id}"))

    start_str = intervention.get("startDateTime")
    end_str = intervention.get("endDateTime")

    if not start_str or not end_str:
      continue

    dtstart = start_str.replace("-", "").replace(":", "")
    dtend = end_str.replace("-", "").replace(":", "")
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # Type d'activité
    activity_caption = ""
    if "activityType" in intervention and "caption" in intervention[
        "activityType"
    ]:
      activity_caption = intervention["activityType"]["caption"].get("fr", "")

    prefix = "Cours"
    act_lower = activity_caption.lower()
    if "td" in act_lower or "travaux dirigés" in act_lower:
      prefix = "TD"
    elif "tp" in act_lower or "travaux pratiques" in act_lower:
      prefix = "TP"
    elif "app" in act_lower:
      prefix = "APP"
    elif "cours" in act_lower or "cm" in act_lower or "magistral" in act_lower:
      prefix = "Cours"
    elif "tg" in act_lower or "grand" in act_lower:
      prefix = "TG"
    elif intervention.get("isExam"):
      prefix = "EXAM"

    # Nom du cours
    course_name = ""
    ped_units = intervention.get("interventionPedagogicalUnits", [])
    if ped_units and "pedagogicalUnit" in ped_units[0]:
      course_name = (
          ped_units[0]["pedagogicalUnit"].get("caption", {}).get("fr", "")
      )

    if not course_name:
      if "field" in intervention and "caption" in intervention["field"]:
        course_name = intervention["field"]["caption"].get("fr", "")
      else:
        course_name = "Cours"

    # Groupes / Populations (nettoyage des parenthèses type "(Paris S03 26-27)")
    clean_populations = []
    for pop in intervention.get("interventionPopulations", []):
      p_cap = pop.get("population", {}).get("caption", {}).get("fr", "")
      if p_cap:
        clean_p = re.sub(r"\s*\(.*?\)", "", p_cap).strip()
        clean_populations.append(clean_p)

    group_str = " - ".join(clean_populations) if clean_populations else "SUP"

    # SUMMARY sans les parenthèses
    summary = f"{prefix} - {group_str} - {course_name}"

    # Salles / Ressources
    rooms = []
    for res in intervention.get("interventionResources", []):
      r = res.get("resource", {})
      if r.get("isRoom"):
        cap = r.get("caption", {}).get("fr") or r.get("code", "")
        if cap:
          rooms.append(cap)

    if rooms:
      if len(rooms) == 1:
        location = rooms[0]
      else:
        location = "\\; \n".join(rooms)
    else:
      location = "A Distance"

    # Formateurs
    instructors = []
    for inst in intervention.get("interventionInstructors", []):
      person = inst.get("person", {})
      fname = person.get("currentFirstName", "")
      lname = person.get("currentLastName", "")
      if fname or lname:
        instructors.append(f"{fname} {lname}".strip())

    # DESCRIPTION structurée avec groupes nettoyés
    desc_lines = ["Ressources : "]
    if rooms:
      for r in rooms:
        desc_lines.append(f" - {r}")
    else:
      desc_lines.append(" - A Distance")

    desc_lines.append("")
    desc_lines.append("Formateurs : ")
    if instructors:
      for inst in instructors:
        desc_lines.append(f" - {inst}")
    else:
      desc_lines.append(" - Non spécifié")

    desc_lines.append("")
    desc_lines.append("Groupes : ")
    if clean_populations:
      for cp in clean_populations:
        desc_lines.append(f" - {cp}")
    else:
      desc_lines.append(" - SUP")

    description = "\\n".join(desc_lines)

    ics_lines.extend([
        "BEGIN:VEVENT",
        "CLASS:PUBLIC",
        f"DESCRIPTION:{description}",
        f"DTEND:{dtend}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"LOCATION:{location}",
        "SEQUENCE:1",
        "STATUS:Confirmed",
        f"SUMMARY:{summary}",
        "TRANSP:Opaque",
        f"UID:{uid}",
        "END:VEVENT",
    ])

  ics_lines.append("END:VCALENDAR")

  ics_content = "\r\n".join(ics_lines)
  with open(ics_filepath, "w", encoding="utf-8") as f:
    f.write(ics_content)

  print(
      f"Export nettoyé réussi : {len(interventions)} événements exportés vers"
      f" {ics_filepath}."
  )
  return len(interventions) > 0


async def main():
  email = os.environ.get("ESME_USER")
  password = os.environ.get("ESME_PASSWORD")
  if not email or not password:
    print(
        "❌ Erreur : Les variables d'environnement ESME_USER et ESME_PASSWORD"
        " sont requises."
    )
    sys.exit(1)

  print("🚀 Lancement du navigateur de scraping...")
  async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
    context = await browser.new_context(viewport={"width": 1920, "height": 1080})
    page = await context.new_page()

    print("1. Connexion au portail Keycloak...")
    try:
      await page.goto("https://my.esme.fr/", wait_until="networkidle")
      await page.fill("#username", email)
      await page.fill("#password", password)
      await page.click("#kc-login")
      await page.wait_for_load_state("networkidle")
    except Exception as e:
      print(f"❌ Échec de la connexion : {e}")
      await browser.close()
      sys.exit(1)

    print("2. Capture de la requête officielle...")
    async with page.expect_request(
        lambda req: "/api/plannings/me" in req.url
    ) as req_info:
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

    print("4. Conversion en ICS avec le nouveau format...")
    if not convert_json_to_exact_ics(planning_data, "emploi_du_temps.ics"):
      print("❌ Aucun cours n'a pu être converti.")
      sys.exit(1)


if __name__ == "__main__":
  asyncio.run(main())
