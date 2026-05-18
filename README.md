# Stripe Payment Gateway — FastAPI

FastAPI backend with JWT authentication, Stripe payments, and SQLite persistence.

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

```bash
# .env
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...        # fill after step 6
FRONTEND_URL=http://localhost:8000
JWT_SECRET_KEY=replace-with-a-long-random-secret
```

Get Stripe keys from [Dashboard → Developers → API Keys](https://dashboard.stripe.com/test/apikeys).  
**Both Stripe keys must be from the same account.**  
Generate a strong `JWT_SECRET_KEY`:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Run server

```bash
# Option A — direct
cd app && python3 main.py

# Option B — uvicorn from project root
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` in browser.

---

## Authentication

All payment, refund, customer, and checkout endpoints require a JWT Bearer token.

### Register

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword", "name": "Your Name"}'
```

### Login

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}'
```

Response:
```json
{ "access_token": "eyJ...", "token_type": "bearer" }
```

### Use the token

Pass the token in every protected request:

```bash
curl http://localhost:8000/payments \
  -H "Authorization: Bearer <access_token>"
```

### Logout

```bash
curl -X POST http://localhost:8000/auth/logout \
  -H "Authorization: Bearer <access_token>"
```

Token is immediately revoked in the database — reuse returns `401`.

### Get current user

```bash
curl http://localhost:8000/auth/me \
  -H "Authorization: Bearer <access_token>"
```

---

## API Endpoints

### Auth

| Method | Endpoint | Auth required | Description |
|--------|----------|:---:|-------------|
| `POST` | `/auth/register` | No | Register new user (email + password) |
| `POST` | `/auth/login` | No | Login — returns Bearer token |
| `POST` | `/auth/logout` | Yes | Revoke current token |
| `GET` | `/auth/me` | Yes | Current user profile |

### Payments

| Method | Endpoint | Auth required | Description |
|--------|----------|:---:|-------------|
| `POST` | `/payments/create-intent` | Yes | Create payment intent |
| `GET` | `/payments` | Yes | List all payments from DB |
| `GET` | `/payments/{id}` | Yes | Get payment (DB first, falls back to Stripe) |
| `POST` | `/payments/{id}/retry` | Yes | Retry a failed payment |
| `POST` | `/payments/refund` | Yes | Refund a succeeded payment |

### Checkout & Customers

| Method | Endpoint | Auth required | Description |
|--------|----------|:---:|-------------|
| `POST` | `/checkout/create-session` | Yes | Create Stripe hosted checkout session |
| `POST` | `/customers` | Yes | Create Stripe customer |
| `GET` | `/customers` | Yes | List all customers from DB |

### Webhooks & Utility

| Method | Endpoint | Auth required | Description |
|--------|----------|:---:|-------------|
| `POST` | `/webhooks/stripe` | No | Stripe webhook receiver |
| `GET` | `/health` | No | Health check |
| `GET` | `/config` | No | Returns Stripe publishable key |

---

### Request bodies

**Register**
```json
{ "email": "you@example.com", "password": "yourpassword", "name": "Your Name" }
```

**Login**
```json
{ "email": "you@example.com", "password": "yourpassword" }
```

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
TOKEN="Bearer <access_token>"

# Full refund
curl -X POST http://localhost:8000/payments/refund \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"payment_intent_id": "pi_xxx", "reason": "requested_by_customer"}'

# Partial refund (amount in cents)
curl -X POST http://localhost:8000/payments/refund \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"payment_intent_id": "pi_xxx", "amount": 1000, "reason": "requested_by_customer"}'
```

### Test retry flow

```bash
curl -X POST http://localhost:8000/payments/pi_xxx/retry \
  -H "Authorization: Bearer <access_token>"
```

---

## Webhook Testing

### 1. Install Stripe CLI

```bash
# macOS
brew install stripe/stripe-cli/stripe

# Linux — download from https://github.com/stripe/stripe-cli/releases
```

### 2. Login & listen

```bash
stripe login
stripe listen --forward-to localhost:8000/webhooks/stripe
```

Copy the `whsec_...` secret printed in terminal into `.env`.

### 3. Trigger test events

```bash
stripe trigger payment_intent.succeeded
stripe trigger payment_intent.payment_failed
stripe trigger checkout.session.completed
```

---

## Database

SQLite database (`app/stripe_payments.db`) auto-created on first run.

| Table | Stores |
|-------|--------|
| `users` | Registered users (bcrypt hashed passwords) |
| `revoked_tokens` | Logged-out JWT tokens (for immediate revocation) |
| `payments` | Payment intents — status updated via webhooks |
| `refunds` | Refunds issued |
| `customers` | Stripe customers created |
| `checkout_sessions` | Checkout sessions — status updated on completion |
| `webhook_events` | All incoming webhook events (idempotent) |

### Inspect the database

```bash
sqlite3 app/stripe_payments.db

.tables
SELECT * FROM users;
SELECT * FROM payments;
SELECT * FROM customers;
SELECT * FROM refunds;
SELECT * FROM webhook_events;
.quit
```

Or via API (requires auth):
```bash
curl http://localhost:8000/payments -H "Authorization: Bearer <token>"
curl http://localhost:8000/customers -H "Authorization: Bearer <token>"
```

---

## Project Structure

```
stripe-fastapi/
├── app/
│   ├── main.py          # FastAPI app, all routes
│   ├── auth.py          # JWT auth — hashing, token creation, get_current_user
│   └── database.py      # SQLAlchemy models + SQLite setup
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
| `401 Invalid token` | Missing or expired Bearer token | Login again to get a fresh token |
| `401 Token has been revoked` | Token was logged out | Login again |
| `400 Email already registered` | Duplicate registration | Use different email or just login |
| `No such payment_intent` | Stripe keys from different accounts | Use matching key pair from same account |
| `Address already in use` | Port 8000 occupied | `lsof -ti:8000 \| xargs kill` |
| `Invalid signature` | Wrong webhook secret | Copy `whsec_...` from `stripe listen` into `.env` |
| `Cannot refund payment with status 'requires_payment_method'` | Refunding a failed payment | Only succeeded payments can be refunded |
