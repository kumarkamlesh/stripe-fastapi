import json
import os
from pathlib import Path
import stripe
from fastapi import Depends, FastAPI, HTTPException, Request, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import (
    create_tables, get_db,
    Payment, Refund, Customer, CheckoutSession, WebhookEvent, User, RevokedToken,
)
from auth import (
    hash_password, verify_password, create_access_token, get_current_user,
    bearer_scheme,
)

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")

tags_metadata = [
    {"name": "Authentication", "description": "Register, login, logout, and current user."},
    {"name": "Payments", "description": "Create, retrieve, refund, and retry Stripe PaymentIntents."},
    {"name": "Checkout", "description": "Stripe-hosted Checkout Sessions."},
    {"name": "Customers", "description": "Stripe Customer records."},
    {"name": "Webhooks", "description": "Stripe webhook event receiver."},
    {"name": "Utility", "description": "Health check and configuration."},
]

app = FastAPI(title="Stripe Payment Gateway", openapi_tags=tags_metadata)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent.parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.on_event("startup")
def on_startup():
    create_tables()


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the browser-based payment test UI from static/index.html."""
    with open(BASE_DIR / "static/index.html") as f:
        return f.read()


class PaymentIntentRequest(BaseModel):
    amount: int
    currency: str = "usd"
    description: Optional[str] = None

class CheckoutRequest(BaseModel):
    product_name: str
    amount: int
    currency: str = "usd"
    quantity: int = 1

class RefundRequest(BaseModel):
    payment_intent_id: str
    amount: Optional[int] = None
    reason: str = "requested_by_customer"

class CustomerRequest(BaseModel):
    email: str
    name: Optional[str] = None

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/register", status_code=201, tags=["Authentication"])
async def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user. Email must be unique."""
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "name": user.name}


@app.post("/auth/login", tags=["Authentication"])
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Login with email and password. Returns a Bearer token."""
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")
    token, _ = create_access_token(user.id, user.email)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/auth/logout", tags=["Authentication"])
async def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Logout — revokes the current token so it cannot be reused."""
    from auth import SECRET_KEY, ALGORITHM
    import jwt as _jwt
    payload = _jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    jti = payload["jti"]
    if not db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        db.add(RevokedToken(jti=jti))
        db.commit()
    return {"message": "Logged out successfully"}


@app.get("/auth/me", tags=["Authentication"])
async def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return {"id": current_user.id, "email": current_user.email, "name": current_user.name}


@app.get("/health", tags=["Utility"])
async def health():
    return {"status": "ok"}


@app.get("/config", tags=["Utility"])
async def get_config():
    return {"publishable_key": PUBLISHABLE_KEY}


@app.post("/payments/create-intent", tags=["Payments"])
async def create_payment_intent(
    body: PaymentIntentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a Stripe PaymentIntent.

    Returns a client_secret which the frontend uses with Stripe.js
    to collect card details and confirm the payment.

    Amount must be in smallest currency unit (cents for USD).
    Example: amount=2999 charges $29.99.
    """
    try:
        intent = stripe.PaymentIntent.create(
            amount=body.amount,
            currency=body.currency,
            description=body.description,
            automatic_payment_methods={"enabled": True},
        )
        db.add(Payment(
            payment_intent_id=intent.id,
            amount=intent.amount,
            currency=intent.currency,
            description=body.description,
            status=intent.status,
        ))
        db.commit()
        return {
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "status": intent.status,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.get("/payments", tags=["Payments"])
async def list_payments(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all payments stored in the local database, newest first."""
    rows = (
        db.query(Payment)
        .order_by(Payment.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "payment_intent_id": r.payment_intent_id,
            "amount": r.amount,
            "currency": r.currency,
            "description": r.description,
            "status": r.status,
            "customer_id": r.customer_id,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@app.get("/payments/{payment_intent_id}", tags=["Payments"])
async def get_payment(
    payment_intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve status/details of a PaymentIntent.

    Returns local DB record when present (fast path), falls back to live Stripe data.
    """
    row = db.query(Payment).filter(Payment.payment_intent_id == payment_intent_id).first()
    if row:
        return {
            "id": row.payment_intent_id,
            "amount": row.amount,
            "currency": row.currency,
            "status": row.status,
            "source": "db",
        }
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        return {
            "id": intent.id,
            "amount": intent.amount,
            "currency": intent.currency,
            "status": intent.status,
            "source": "stripe",
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/checkout/create-session", tags=["Checkout"])
async def create_checkout_session(
    body: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a Stripe-hosted Checkout Session.

    Returns a session URL that redirects the customer to Stripe's
    pre-built payment page. Redirects to /success on completion or /cancel on abort.
    """
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": body.currency,
                    "product_data": {"name": body.product_name},
                    "unit_amount": body.amount,
                },
                "quantity": body.quantity,
            }],
            mode="payment",
            success_url="http://localhost:8000/success",
            cancel_url="http://localhost:8000/cancel",
        )
        db.add(CheckoutSession(
            session_id=session.id,
            product_name=body.product_name,
            amount=body.amount,
            currency=body.currency,
            quantity=body.quantity,
            status=session.status or "open",
        ))
        db.commit()
        return {"session_id": session.id, "url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/payments/refund", tags=["Payments"])
async def create_refund(
    body: RefundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Refund a succeeded payment, fully or partially.

    Validates that the payment has succeeded before issuing a refund.
    Guards against over-refunding by comparing against already-refunded amount.
    Omit amount for a full refund. Amount must be in cents.

    Valid reasons: requested_by_customer, duplicate, fraudulent.
    """
    try:
        intent = stripe.PaymentIntent.retrieve(body.payment_intent_id)

        if intent.status != "succeeded":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot refund payment with status '{intent.status}'. Only 'succeeded' payments can be refunded."
            )

        already_refunded = sum(r.amount for r in intent.charges.data[0].refunds.data) if intent.charges.data else 0
        refundable = intent.amount - already_refunded
        if refundable <= 0:
            raise HTTPException(status_code=400, detail="Payment already fully refunded.")

        if body.amount and body.amount > refundable:
            raise HTTPException(
                status_code=400,
                detail=f"Requested refund {body.amount} exceeds refundable amount {refundable}."
            )

        params = {"payment_intent": body.payment_intent_id, "reason": body.reason}
        if body.amount:
            params["amount"] = body.amount
        refund = stripe.Refund.create(**params)

        db.add(Refund(
            refund_id=refund.id,
            payment_intent_id=body.payment_intent_id,
            amount=refund.amount,
            reason=body.reason,
            status=refund.status,
        ))
        db.commit()

        return {
            "refund_id": refund.id,
            "status": refund.status,
            "amount": refund.amount,
            "refunded_so_far": already_refunded + refund.amount,
            "original_amount": intent.amount,
        }
    except HTTPException:
        raise
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/payments/{payment_intent_id}/retry", tags=["Payments"])
async def retry_payment(
    payment_intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retry a failed payment — returns fresh client_secret for the same PaymentIntent.

    Only works when status is requires_payment_method or requires_confirmation.
    """
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        if intent.status not in ("requires_payment_method", "requires_confirmation"):
            raise HTTPException(
                status_code=400,
                detail=f"Payment status '{intent.status}' cannot be retried. Only failed payments can be retried."
            )
        row = db.query(Payment).filter(Payment.payment_intent_id == payment_intent_id).first()
        if row:
            row.status = intent.status
            db.commit()
        return {
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "status": intent.status,
            "last_error": intent.get("last_payment_error", {}).get("message"),
        }
    except HTTPException:
        raise
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/customers", tags=["Customers"])
async def create_customer(
    body: CustomerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a Stripe Customer record with email and optional name.

    Storing customers allows attaching payment methods and tracking history.
    """
    try:
        customer = stripe.Customer.create(email=body.email, name=body.name)
        db.add(Customer(
            customer_id=customer.id,
            email=customer.email,
            name=customer.name,
        ))
        db.commit()
        return {"customer_id": customer.id, "email": customer.email}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.get("/customers", tags=["Customers"])
async def list_customers(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all customers stored in the local database, newest first."""
    rows = (
        db.query(Customer)
        .order_by(Customer.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "customer_id": r.customer_id,
            "email": r.email,
            "name": r.name,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@app.post("/webhooks/stripe", tags=["Webhooks"])
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Receive and process Stripe webhook events.

    Verifies the request signature using STRIPE_WEBHOOK_SECRET.
    Handles: payment_intent.succeeded, payment_intent.payment_failed,
    checkout.session.completed, invoice.payment_succeeded.

    Use 'stripe listen --forward-to localhost:8000/webhooks/stripe'
    during local development.
    """
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event["type"]
    data  = event["data"]["object"]

    # Idempotency: skip already-processed events
    if not db.query(WebhookEvent).filter(WebhookEvent.event_id == event["id"]).first():
        db.add(WebhookEvent(
            event_id=event["id"],
            event_type=etype,
            object_id=data.get("id"),
            payload=json.dumps(event["data"]),
        ))

    if etype == "payment_intent.succeeded":
        print(f"Payment succeeded: {data['id']} — {data['amount']} {data['currency'].upper()}")
        row = db.query(Payment).filter(Payment.payment_intent_id == data["id"]).first()
        if row:
            row.status = "succeeded"

    elif etype == "payment_intent.payment_failed":
        err    = data.get("last_payment_error", {})
        reason = err.get("message", "unknown")
        code   = err.get("decline_code") or err.get("code", "")
        print(f"Payment failed: {data['id']} | reason={reason} | code={code}")
        row = db.query(Payment).filter(Payment.payment_intent_id == data["id"]).first()
        if row:
            row.status = "failed"

    elif etype == "checkout.session.completed":
        print(f"Checkout complete: {data['id']}")
        row = db.query(CheckoutSession).filter(CheckoutSession.session_id == data["id"]).first()
        if row:
            row.status = "complete"

    elif etype == "invoice.payment_succeeded":
        print(f"Invoice paid: {data['id']}")

    db.commit()
    return {"received": True, "event": etype}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
