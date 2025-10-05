# Plex Remote Health (AWS Lambda)

Outside-in health check for a Plex server fronted by an HTTPS reverse proxy (e.g., `https://plex.yourdomain.com`).  
The Lambda probes `GET /identity`, stores the last known status in **SSM Parameter Store**, and sends **Pushover** alerts on **state changes only** (DOWN → RECOVERED).

- ✅ Verifies the *real remote path* (DNS → TLS/SNI → Nginx → Plex)  
- ✅ Alerts only on transitions (no spam while stable)  
- ✅ Runs every 5 minutes via EventBridge (effectively $0/month in the free tier)  
- ✅ No Plex “Remote Access” required/enabled in Plex UI  

---

## What it checks (and why)

- **Endpoint:** `https://plex.<your-domain>/identity`  
  This is lightweight, unauthenticated, and returns `200` with a small XML body containing `machineIdentifier=…` when Plex is reachable through your reverse proxy.

- **“secure=0 vs 1”**: Irrelevant for this check. We only care that the public HTTPS path to Plex works end-to-end.

- **IPv6 note:** If you publish an **AAAA** record for your hostname, be sure WAN-IPv6:443 forwards to your proxy. Otherwise remove the AAAA (or run a second IPv4-only check).

---

## Prerequisites

- AWS account with **`aws` CLI** configured (`aws sts get-caller-identity` should work).  
- A Plex reverse proxy hostname (e.g., `https://plex.example.com`) reachable from the internet.  
- **Pushover** app token & user key.  
- This repo contains `lambda_function.py` (the Lambda code).

---

## Setup (Steps 1–4)

> Use the **same region** throughout (CLI & Console).

### 1) Create the Lambda IAM role (once)

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)

cat > trust-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [{ "Effect": "Allow", "Principal": { "Service": "lambda.amazonaws.com" }, "Action": "sts:AssumeRole" }]
}
JSON

aws iam create-role \
  --role-name plex-remote-health-role \
  --assume-role-policy-document file://trust-policy.json

cat > inline-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": [ "ssm:GetParameter", "ssm:PutParameter" ], "Resource": "*" },
    { "Effect": "Allow", "Action": [ "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents" ], "Resource": "*" }
  ]
}
JSON

aws iam put-role-policy \
  --role-name plex-remote-health-role \
  --policy-name plex-remote-health-inline \
  --policy-document file://inline-policy.json

ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/plex-remote-health-role"
```