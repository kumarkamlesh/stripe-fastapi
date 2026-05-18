# Stripe Payment Gateway — FastAPI

Test Stripe payments locally with a FastAPI backend and browser UI.

---

## Requirements

- Python 3.12+
- Stripe account ([sign up free](https://dashboard.stripe.com/register))
- Stripe CLI (for webhook testing)

---

## Setup

### 1. Clone & create virtualenv

```bash
git clone <repo-url>
cd stripe-fastapi
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Copy `.env` and fill in your Stripe test keys:

```bash
# .env
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...   # fill after step 6
FRONTEND_URL=http://localhost:8000
```

Get keys from [Stripe Dashboard → Developers → API Keys](https://dashboard.stripe.com/test/apikeys).  
**Both keys must be from the same Stripe account.**

### 4. Run server

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` in browser.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/config` | Returns publishable key for frontend |
| `POST` | `/payments/create-intent` | Create payment intent |
| `GET` | `/payments/{id}` | Get payment intent status |
| `POST` | `/payments/{id}/retry` | Retry a failed payment |
| `POST` | `/payments/refund` | Refund a succeeded payment |
| `POST` | `/checkout/create-session` | Create Stripe hosted checkout session |
| `POST` | `/customers` | Create Stripe customer |
| `POST` | `/webhooks/stripe` | Stripe webhook receiver |

### Request bodies

**Create Payment Intent**
```json
{ "amount": 2999, "currency": "usd", "description": "Order #1" }
```

**Refund**
```json
{ "payment_intent_id": "pi_xxx", "amount": 1000, "reason": "requested_by_customer" }
```
Omit `amount` for full refund. Valid reasons: `requested_by_customer`, `duplicate`, `fraudulent`.

**Checkout Session**
```json
{ "product_name": "Premium Plan", "amount": 4999, "currency": "usd", "quantity": 1 }
```

**Create Customer**
```json
{ "email": "user@example.com", "name": "Jane Doe" }
```

---

## Testing Payments

### Test cards

| Card Number | Scenario |
|-------------|----------|
| `4242 4242 4242 4242` | Success |
| `4000 0025 0000 3155` | 3D Secure required |
| `4000 0000 0000 9995` | Fails — insufficient funds |
| `4000 0000 0000 0002` | Fails — card declined |

Use any future expiry date, any 3-digit CVC, any ZIP.

### Test refund flow

```bash
# 1. Pay with 4242... → note pi_xxx from success message

# 2. Full refund
curl -X POST http://localhost:8000/payments/refund \
  -H "Content-Type: application/json" \
  -d '{"payment_intent_id": "pi_xxx", "reason": "requested_by_customer"}'

# 3. Partial refund (amount in cents)
curl -X POST http://localhost:8000/payments/refund \
  -H "Content-Type: application/json" \
  -d '{"payment_intent_id": "pi_xxx", "amount": 1000, "reason": "requested_by_customer"}'
```

### Test retry flow

```bash
# 1. Pay with 4000 0000 0000 9995 → note pi_xxx from error message

# 2. Get new client_secret for retry
curl -X POST http://localhost:8000/payments/pi_xxx/retry

# 3. Use returned client_secret in frontend to re-collect card
```

---

## Webhook Testing

Webhooks require Stripe CLI to forward events to your local server.

### 1. Install Stripe CLI

```bash
# macOS
brew install stripe/stripe-cli/stripe

# Linux
# Download from https://github.com/stripe/stripe-cli/releases
```

### 2. Login & listen

```bash
stripe login
stripe listen --forward-to localhost:8000/webhooks/stripe
```

Copy the `whsec_...` secret printed in terminal.

### 3. Add webhook secret to `.env`

```
STRIPE_WEBHOOK_SECRET=whsec_...
```

Restart server (`Ctrl+C` then `uvicorn app.main:app --reload --port 8000`).

### 4. Trigger test events

```bash
stripe trigger payment_intent.succeeded
stripe trigger payment_intent.payment_failed
stripe trigger checkout.session.completed
```

Server logs will show the event details.

---

## Project Structure

```
stripe-fastapi/
├── app/
│   └── main.py          # FastAPI app, all routes
├── static/
│   └── index.html       # Browser test UI
├── .env                 # API keys (never commit)
├── requirements.txt
└── README.md
```

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `No such payment_intent` | Secret & publishable keys from different accounts | Use matching key pair from same Stripe account |
| `Address already in use` | Port 8000 occupied | `lsof -ti:8000 \| xargs kill` |
| `Invalid signature` | Wrong webhook secret | Copy `whsec_...` from `stripe listen` output into `.env` |
| `Cannot refund payment with status 'requires_payment_method'` | Trying to refund a failed payment | Only succeeded payments can be refunded |
