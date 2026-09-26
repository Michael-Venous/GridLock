"""Lambda function URL for the Changes tab: POST /subscribe, POST /sample, GET /status.

The URL is public (the page runs in planners' browsers), so every input is validated, each email can sign up
a few times a day at most, and samples go only to confirmed subscribers, one every ten minutes.
CORS headers come from the function URL's own configuration.
"""
import base64
import json
import time

import alerts
from store import Store

SUBSCRIBES_PER_DAY = 5
SAMPLE_EVERY_S = 600
_changes = {"at": 0, "log": None}


def respond(code, payload):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def change_log(store):
    """data/changes.json from S3, reread at most every five minutes per warm container."""
    if not _changes["log"] or time.time() - _changes["at"] > 300:
        _changes["log"], _changes["at"] = store.get_json("state/changes.json", {"events": []}), time.time()
    return _changes["log"]


def handler(event, context, store=None):
    store = store or Store.from_env()
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/").rstrip("/") or "/"
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return respond(400, {"error": "the request body must be JSON"})
    try:
        if method == "POST" and path == "/subscribe":
            return subscribe(store, body)
        if method == "POST" and path == "/sample":
            return sample(store, body)
        if method == "GET" and path == "/status":
            sub = store.get_subscriber((event.get("queryStringParameters") or {}).get("id", ""))
            return respond(200, {"confirmed": bool(sub) and store.confirmed(sub.get("snsArn"))}) if sub else respond(404, {"error": "no such subscription"})
    except alerts.Invalid as e:
        return respond(400, {"error": str(e)})
    return respond(404, {"error": "not found"})


def subscribe(store, body):
    sub = alerts.clean_subscription(body)
    sid = store.subscriber_id(sub["email"])
    if not store.bump(f"subscribe#{sid}#{time.strftime('%Y-%m-%d')}", SUBSCRIBES_PER_DAY, 2 * 86400):
        return respond(429, {"error": "too many sign-ups for this address today; try again tomorrow"})
    old = store.get_subscriber(sid) or {}
    arn = old.get("snsArn")
    confirmed = store.confirmed(arn)
    if not confirmed:
        arn = store.subscribe(sub["email"], sid)   # sends (or resends) the confirmation email
    store.put_subscriber({**old, **sub, "id": sid, "snsArn": arn, "updated": int(time.time())})
    return respond(200, {"id": sid, "status": "confirmed" if confirmed else "pending"})


def sample(store, body):
    sid = str(body.get("id") or "")
    sub = store.get_subscriber(sid) if sid else None
    if not sub:
        return respond(404, {"error": "no such subscription"})
    if not store.confirmed(sub.get("snsArn")):
        return respond(409, {"error": "confirm the subscription from the AWS Notifications email first"})
    if time.time() - sub.get("lastSample", 0) < SAMPLE_EVERY_S:
        return respond(429, {"error": "one sample every ten minutes"})
    events = change_log(store)["events"]
    if not events:
        return respond(503, {"error": "the change log isn't loaded yet"})
    event, items = events[0], []
    for ev in events:                     # newest filing that touches the area, else the newest filing
        found = alerts.items_for(ev, sub)
        if found:
            event, items = ev, found
            break
    subject, text = alerts.message(event, items, sub, sample=True, app_url=store.app_url)
    store.publish(sid, subject, text)
    store.put_subscriber({**sub, "lastSample": int(time.time())})
    return respond(200, {"items": len(items), "filing": event["title"]})
