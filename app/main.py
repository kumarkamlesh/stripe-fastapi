import os
import stripe
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")

app = FastAPI(title="Stripe Payment Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the browser-based payment test UI from static/index.html."""
    with open("static/index.html") as f:
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


@app.get("/health")
async def health():
    """Health check endpoint. Returns 200 if server is running."""
    return {"status": "ok"}


@app.get("/config")
async def get_config():
    """Return Stripe publishable key for use in frontend Stripe.js initialization."""
    return {"publishable_key": PUBLISHABLE_KEY}


@app.post("/payments/create-intent")
async def create_payment_intent(body: PaymentIntentRequest):
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
        return {
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "status": intent.status,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.get("/payments/{payment_intent_id}")
async def get_payment(payment_intent_id: str):
    """
    Retrieve the current status and details of a PaymentIntent by its ID.

    Useful for checking whether a payment succeeded, failed, or is still pending.
    """
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        return {
            "id": intent.id,
            "amount": intent.amount,
            "currency": intent.currency,
            "status": intent.status,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/checkout/create-session")
async def create_checkout_session(body: CheckoutRequest):
    """
    Create a Stripe-hosted Checkout Session.

    Returns a session URL that redirects the customer to Stripe's
    pre-built payment page. No card handling on your server needed.
    Redirects to /success on completion or /cancel on abort.
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
        return {"session_id": session.id, "url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/payments/refund")
async def create_refund(body: RefundRequest):
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


@app.post("/payments/{payment_intent_id}/retry")
async def retry_payment(payment_intent_id: str):
    """
    Retry a failed payment by returning a fresh client_secret for the same PaymentIntent.

    Only works when payment status is requires_payment_method (card declined/failed)
    or requires_confirmation. The frontend uses the returned client_secret
    to re-collect card details without creating a new PaymentIntent.
    """
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        if intent.status not in ("requires_payment_method", "requires_confirmation"):
            raise HTTPException(
                status_code=400,
                detail=f"Payment status '{intent.status}' cannot be retried. Only failed payments can be retried."
            )
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


@app.post("/customers")
async def create_customer(body: CustomerRequest):
    """
    Create a Stripe Customer record with email and optional name.

    Storing customers in Stripe allows attaching payment methods,
    tracking purchase history, and enabling saved cards for future payments.
    """
    try:
        customer = stripe.Customer.create(email=body.email, name=body.name)
        return {"customer_id": customer.id, "email": customer.email}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message))


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Receive and process Stripe webhook events.

    Verifies the request signature using STRIPE_WEBHOOK_SECRET to ensure
    the event came from Stripe. Handles: payment_intent.succeeded,
    payment_intent.payment_failed, checkout.session.completed,
    invoice.payment_succeeded.

    Use 'stripe listen --forward-to localhost:8000/webhooks/stripe'
    during local development to forward events from Stripe CLI.
    """
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event["type"]
    data  = event["data"]["object"]

    if etype == "payment_intent.succeeded":
        print(f"Payment succeeded: {data['id']} — {data['amount']} {data['currency'].upper()}")

    elif etype == "payment_intent.payment_failed":
        err    = data.get("last_payment_error", {})
        reason = err.get("message", "unknown")
        code   = err.get("decline_code") or err.get("code", "")
        print(f"Payment failed: {data['id']} | reason={reason} | code={code}")

    elif etype == "checkout.session.completed":
        print(f"Checkout complete: {data['id']}")

    elif etype == "invoice.payment_succeeded":
        print(f"Invoice paid: {data['id']}")

    return {"received": True, "event": etype}
