# Plex Remote Health Monitor

## Automated health monitoring for your Plex Media Server using AWS Lambda

This project provides an "outside-in" health check for a Plex server that's
accessible remotely through an HTTPS reverse proxy (e.g., `https://plex.yourdomain.com`).
A lightweight AWS Lambda function runs every 5 minutes to verify your Plex
server is reachable from the internet, and sends you **Pushover notifications**
when the server goes down or recovers.

## Why This Exists

If you run Plex behind a reverse proxy (like Nginx) for remote access, you want
to know if it becomes unreachable from the outside world. This solution:

- ✅ **Tests the real path** — Verifies the complete chain: DNS → TLS/HTTPS → Reverse Proxy → Plex
- ✅ **Smart notifications** — Alerts only on state changes (DOWN ↔ RECOVERED), not every check
- ✅ **Essentially free** — Runs in AWS Free Tier (~8,640 Lambda invocations/month = $0)
- ✅ **No Plex config needed** — Uses the public `/identity` endpoint (no auth required)
- ✅ **Simple deployment** — Pure Python, no dependencies, deployed via AWS CLI

---

## How It Works

```text
┌─────────────┐      Every 5 min        ┌──────────────────┐
│ EventBridge │ ───────────────────────▶│ Lambda Function  │
│   (Cron)    │                         │                  │
└─────────────┘                         │ 1. HTTP GET      │
                                        │    /identity     │
                                        │ 2. Check for     │
                                        │    200 + XML     │
                                        │ 3. Compare to    │
                                        │    last state    │
                                        └────────┬─────────┘
                                                 │
                        ┌────────────────────────┼────────────────────┐
                        │                        │                    │
                        ▼                        ▼                    ▼
                 ┌─────────────┐         ┌─────────────┐      ┌──────────┐
                 │ SSM Param   │         │  Pushover   │      │CloudWatch│
                 │   Store     │         │    Alert    │      │   Logs   │
                 │             │         │ (if changed)│      └──────────┘
                 │ Stores last │         └─────────────┘
                 │   status    │
                 └─────────────┘
```

**The Lambda function:**

1. Makes an HTTP request to `https://plex.yourdomain.com/identity`
2. Checks if the response is `200 OK` and contains `machineIdentifier=` in the XML
3. Compares current status to the previous status stored in SSM Parameter Store
4. If the status changed (up→down or down→up), sends a Pushover notification
5. Updates the stored status for the next check

---

## What Endpoint Is Being Checked?

**Endpoint:** `https://plex.yourdomain.com/identity`

This is a lightweight, **unauthenticated** Plex endpoint that returns a small
XML response with your server's `machineIdentifier` when Plex is running and
reachable.

**Example response:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="0" machineIdentifier="abc123..." version="1.32.8.7639-fb6452ebf" />
```

The check considers Plex **UP** if:

- HTTP status is `200`
- Response body contains `machineIdentifier=`

**Notes:**

- The `secure=0` vs `secure=1` flag in Plex settings doesn't matter here — we only care that the HTTPS reverse proxy path works end-to-end
- If you have an **IPv6 AAAA record** for your Plex domain, make sure your firewall/router forwards WAN IPv6:443 to your reverse proxy, or remove the AAAA record

---

## Prerequisites

Before you begin, you need:

1. **AWS Account** with AWS CLI configured
   - Run `aws sts get-caller-identity` — it should return your account info
   - Pick a **region** (e.g., `us-east-1`) and use it consistently throughout

2. **Plex Server** accessible via HTTPS reverse proxy
   - Example: `https://plex.example.com`
   - Must be reachable from the public internet

3. **Pushover Account** for notifications ([pushover.net](https://pushover.net))
   - Create an application to get your **API Token**
   - Note your **User Key**
   - Cost: One-time $5 for mobile apps (optional 30-day trial)

4. **Basic familiarity with:**
   - Running commands in a terminal
   - AWS services (Lambda, IAM, CloudWatch)
   - Your Plex setup

---

## Setup Instructions

> **Important:** Use the **same AWS region** throughout this guide (both CLI and Console).

### Step 1: Create the IAM Role for Lambda

The Lambda function needs permission to:

- Read/write to **SSM Parameter Store** (to track status)
- Write to **CloudWatch Logs** (for debugging)

Run these commands in your terminal:

```bash
# Get your AWS account ID and region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)

# Create trust policy (allows Lambda to assume this role)
cat > trust-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [{ "Effect": "Allow", "Principal": { "Service": "lambda.amazonaws.com" }, "Action": "sts:AssumeRole" }]
}
JSON

# Create the IAM role
aws iam create-role \
  --role-name plex-remote-health-role \
  --assume-role-policy-document file://trust-policy.json

# Create permission policy (SSM + CloudWatch Logs)
cat > inline-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": [ "ssm:GetParameter", "ssm:PutParameter" ], "Resource": "*" },
    { "Effect": "Allow", "Action": [ "logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents" ], "Resource": "*" }
  ]
}
JSON

# Attach the policy to the role
aws iam put-role-policy \
  --role-name plex-remote-health-role \
  --policy-name plex-remote-health-inline \
  --policy-document file://inline-policy.json

# Give IAM a few seconds to propagate
sleep 10
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/plex-remote-health-role"
echo "$ROLE_ARN"
```

---

### Step 2: Package & Create the Lambda Function

First, package the Python code:

```bash
# Create deployment package
zip -r build.zip lambda_function.py
```

Now create the Lambda function (**replace the environment variables** with your values):

```bash
aws lambda create-function \
  --function-name plex-remote-health \
  --runtime python3.12 \
  --role "$ROLE_ARN" \
  --handler lambda_function.handler \
  --zip-file fileb://build.zip \
  --timeout 10 \
  --memory-size 128 \
  --environment "Variables={CHECK_URL=https://plex.yourdomain.com/identity,PUSHOVER_TOKEN=your_pushover_app_token_here,PUSHOVER_USER=your_pushover_user_key_here,STATUS_PARAM=/homelab/plex_remote_status}"
```

**Environment Variables Explained:**

- `CHECK_URL` — Your Plex `/identity` endpoint (replace `yourdomain.com`)
- `PUSHOVER_TOKEN` — Your Pushover application token
- `PUSHOVER_USER` — Your Pushover user key
- `STATUS_PARAM` — SSM parameter name to store status (can be any path you want)

**Test the function manually:**

```bash
aws lambda invoke \
  --function-name plex-remote-health \
  --payload '{}' \
  response.json

cat response.json
```

You should see output like:

```json
{"previous": "unknown", "current": "up", "http": 200}
```

And you should receive a Pushover notification (since it's the first run,
status changed from `unknown` → `up`).

---

### Step 3: Schedule It Every 5 Minutes (EventBridge)

Configure the Lambda to run automatically every 5 minutes:

```bash
# Create or update the EventBridge rule
aws events put-rule \
  --name plex-remote-health-5min \
  --schedule-expression 'rate(5 minutes)'

# Allow EventBridge to invoke your Lambda
RULE_ARN="arn:aws:events:$REGION:$ACCOUNT_ID:rule/plex-remote-health-5min"
aws lambda add-permission \
  --function-name plex-remote-health \
  --statement-id plex-remote-health-events \
  --action 'lambda:InvokeFunction' \
  --principal events.amazonaws.com \
  --source-arn "$RULE_ARN" || true

# Attach Lambda as the target
FUNC_ARN=$(aws lambda get-function --function-name plex-remote-health --query Configuration.FunctionArn --output text)
aws events put-targets \
  --rule plex-remote-health-5min \
  --targets "Id"="lambda","Arn"="$FUNC_ARN"
```

**Done!** Your Lambda will now run every 5 minutes.

---

### Step 4: Verify It's Working

1. **Check CloudWatch Logs** (wait a few minutes after setup):

   ```bash
   aws logs tail /aws/lambda/plex-remote-health --follow
   ```

2. **Check SSM Parameter Store**:

   ```bash
   aws ssm get-parameter --name /homelab/plex_remote_status
   ```

   You should see:

   ```json
   {
     "Parameter": {
       "Name": "/homelab/plex_remote_status",
       "Value": "{\"status\": \"up\", \"ts\": 1234567890}"
     }
   }
   ```

3. **Test the notification** by temporarily breaking your Plex access (e.g.,
stop Nginx or Plex). Within 5 minutes, you should receive a "DOWN" alert,
then a "RECOVERED" alert when you fix it.

---

## Updating the Lambda Code

If you modify `lambda_function.py`, redeploy with:

```bash
zip -r build.zip lambda_function.py
aws lambda update-function-code --function-name plex-remote-health --zip-file fileb://build.zip
```

---

## Cost Breakdown

Assuming a 5-minute schedule (**~8,640 invocations/month**):

| Service | Usage | Cost |
|---------|-------|------|
| **Lambda** | 8,640 invokes × ~500ms × 128MB | Free Tier (1M requests/month) |
| **EventBridge** | 8,640 events/month | Free Tier |
| **SSM Parameter Store** | 8,640 reads + 8,640 writes | Free Tier (Standard params) |
| **CloudWatch Logs** | ~5MB/month | Free Tier (5GB/month) |
| **Pushover** | Notifications on state change only | One-time $5 (app purchase) |

**Total monthly AWS cost:** ~**$0.00** (stays within Free Tier limits in most accounts)

---

## Troubleshooting

**"Parameter not found" error on first run:**

- Normal — the SSM parameter is created automatically on first successful check

**No Pushover notifications:**

- Check your `PUSHOVER_TOKEN` and `PUSHOVER_USER` are correct
- Test manually with:

  ```bash
  curl -s --form-string "token=YOUR_TOKEN" \
    --form-string "user=YOUR_USER" \
    --form-string "message=Test message" \
    https://api.pushover.net/1/messages.json
  ```

**Lambda times out:**

- Check your Plex server is actually reachable from the internet
- Verify your `CHECK_URL` is correct
- Check CloudWatch Logs for error details

**Want to change the check frequency:**

- Modify the EventBridge rule:

  ```bash
  aws events put-rule \
    --name plex-remote-health-5min \
    --schedule-expression "rate(10 minutes)"  # or "rate(1 minute)", etc.
  ```

---

## Cleanup / Uninstall

To remove everything:

```bash
# Delete EventBridge rule targets
aws events remove-targets \
  --rule plex-remote-health-5min \
  --ids lambda

# Delete EventBridge rule
aws events delete-rule \
  --name plex-remote-health-5min

# Remove Lambda permission for EventBridge
aws lambda remove-permission \
  --function-name plex-remote-health \
  --statement-id plex-remote-health-events

# Delete Lambda function
aws lambda delete-function \
  --function-name plex-remote-health

# Delete IAM role policy
aws iam delete-role-policy \
  --role-name plex-remote-health-role \
  --policy-name plex-remote-health-inline

# Delete IAM role
aws iam delete-role \
  --role-name plex-remote-health-role

# Delete SSM parameter (optional)
aws ssm delete-parameter \
  --name /homelab/plex_remote_status
```

---

## License

MIT License - See [LICENSE](LICENSE) file
