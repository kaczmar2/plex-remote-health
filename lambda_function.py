import os, time, urllib.request, urllib.parse, json
from urllib.error import URLError, HTTPError
import boto3

SSM = boto3.client("ssm")
PUSH_URL = "https://api.pushover.net/1/messages.json"

CHECK_URL     = os.environ["CHECK_URL"]            # e.g. https://plex.christiankaczmarek.com/identity
STATUS_PARAM  = os.environ.get("STATUS_PARAM", "/homelab/plex_remote_status")
PUSH_TOKEN    = os.environ["PUSHOVER_TOKEN"]
PUSH_USER     = os.environ["PUSHOVER_USER"]

def get_prev_status():
    try:
        resp = SSM.get_parameter(Name=STATUS_PARAM)
        return json.loads(resp["Parameter"]["Value"]).get("status", "unknown")
    except SSM.exceptions.ParameterNotFound:
        return "unknown"

def put_status(status):
    payload = {"status": status, "ts": int(time.time())}
    SSM.put_parameter(Name=STATUS_PARAM, Value=json.dumps(payload), Type="String", Overwrite=True)

def pushover(title, message, priority=0):
    data = urllib.parse.urlencode({
        "token": PUSH_TOKEN, "user": PUSH_USER,
        "title": title, "message": message,
        "priority": str(priority),
    }).encode()
    req = urllib.request.Request(PUSH_URL, data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

def probe():
    try:
        req = urllib.request.Request(CHECK_URL, headers={"User-Agent": "plex-remote-health"})
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.getcode()
            body = r.read(4096).decode("utf-8", "ignore")
            ok = (code == 200) and ("machineIdentifier=" in body)
            return ("up" if ok else "down"), code
    except (HTTPError, URLError, TimeoutError):
        return "down", 0

def handler(event, context):
    current, http_code = probe()
    prev = get_prev_status()
    if current != prev:
        if current == "down":
            pushover("Plex remote health: DOWN",
                     f"{CHECK_URL} failed (HTTP {http_code}) at {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}",
                     priority=1)
        elif prev == "down" and current == "up":
            pushover("Plex remote health: RECOVERED",
                     f"Service restored at {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
        put_status(current)
    return {"previous": prev, "current": current, "http": http_code}
