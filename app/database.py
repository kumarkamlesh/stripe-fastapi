import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

DATABASE_URL = "sqlite:///./stripe_payments.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    payment_intent_id = Column(String, unique=True, index=True, nullable=False)
    amount = Column(Integer, nullable=False)
    currency = Column(String, nullable=False)
    description = Column(String, nullable=True)
    status = Column(String, nullable=False)
    customer_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(Integer, primary_key=True)
    refund_id = Column(String, unique=True, index=True, nullable=False)
    payment_intent_id = Column(String, index=True, nullable=False)
    amount = Column(Integer, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    customer_id = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, index=True, nullable=False)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class CheckoutSession(Base):
    __tablename__ = "checkout_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String, unique=True, index=True, nullable=False)
    product_name = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    currency = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, unique=True, index=True, nullable=False)
    event_type = Column(String, index=True, nullable=False)
    object_id = Column(String, nullable=True)
    payload = Column(Text, nullable=False)
    processed_at = Column(DateTime, default=datetime.datetime.utcnow)


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
