# main.py
import os
import httpx
from datetime import date
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse

app = FastAPI()

# --- Config (set these as environment variables on Render) ---
CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]   # your personal refresh token
VERIFY_TOKEN  = os.environ["STRAVA_VERIFY_TOKEN"]    # any string you choose, e.g. "mycyclingbot"
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]


# --- Strava OAuth: exchange refresh token for a fresh access token ---
async def get_access_token() -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post("https://www.strava.com/oauth/token", data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
        })
        r.raise_for_status()
        return r.json()["access_token"]


# --- Claude API: get a sport fact for today ---
async def get_sport_fact(sport: str = "cycling") -> str:
    today = date.today().strftime("%B %-d")   # e.g. "February 21"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "messages":   [{"role": "user", "content":
                    f"Give me one interesting {sport} history fact for {today}. It must have happened on this date in a previous year. Please make sure the fact is verifiably correct. Make it fairly Europe-centric, and avoid first ever cycle race type facts which are a bit boring."
                    f"Maximum 20 words. Just the fact, no preamble."
                }],
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()


# --- Strava: update activity description ---
async def update_activity(activity_id: int, description: str):
    token = await get_access_token()
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"description": description},
            timeout=15,
        )
        r.raise_for_status()


# --- Webhook validation (GET) – Strava calls this when you register ---
@app.get("/webhook")
async def verify_webhook(
    hub_mode:         str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge:    str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return {"hub.challenge": hub_challenge}
    return JSONResponse(status_code=403, content={"error": "Forbidden"})


# --- Webhook event receiver (POST) – fires on every Strava activity ---
@app.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.json()

    # Only act on newly created activities
    if body.get("object_type") != "activity" or body.get("aspect_type") != "create":
        return {"status": "ignored"}

    activity_id = body["object_id"]

    try:
        token = await get_access_token()

        # Fetch full activity to check type
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://www.strava.com/api/v3/activities/{activity_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            activity = r.json()

        if activity.get("description"):
            print(f"Activity {activity_id} already has description")
            return {"status": "skipped"}
            
        activity_type = activity.get("sport_type", "")
        print(f"Activity type: {activity_type}")

        running_types = ("Run", "TrailRun", "VirtualRun")
        if activity_type in running_types:
            sport = "running"
            emoji = "🏃"
        else:
            sport = "cycling"
            emoji = "🚴"

        fact = await get_sport_fact(sport)
        today = date.today().strftime("%B %-d")
        description = f"{emoji} {sport.capitalize()} fact: {fact}"
        await update_activity(activity_id, description)
        print(f"Updated activity {activity_id}: {description}")
    except Exception as e:
        print(f"Error processing activity {activity_id}: {e}")

    # Always return 200 quickly — Strava will retry if you don't
    return {"status": "ok"}

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}
