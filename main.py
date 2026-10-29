from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Canteen Counter Allocation API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict this to your frontend domain before deployment.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TOTAL_COUNTERS = 4
SCANS_PER_COUNTER = 5
lock = Lock()

counters = []
customers: List[dict] = []
total_scans = 0
next_customer_id = 1
alerts: List[dict] = []


def reset_state():
    global counters, customers, total_scans, next_customer_id, alerts
    counters = [
        {
            "id": i,
            "active": (i == 1),
            "staff_notified": False,
        }
        for i in range(1, TOTAL_COUNTERS + 1)
    ]
    customers = []
    total_scans = 0
    next_customer_id = 1
    alerts = []


reset_state()


class CustomerRequest(BaseModel):
    name: str = "Customer"


def counter_load(counter_id: int) -> int:
    return sum(
        1 for c in customers
        if c["counter_id"] == counter_id and c["status"] == "waiting"
    )


def snapshot() -> dict:
    result_counters = []
    for counter in counters:
        result_counters.append({
            **counter,
            "waiting": counter_load(counter["id"]),
            "assigned_total": sum(1 for c in customers if c["counter_id"] == counter["id"]),
            "completed": sum(
                1 for c in customers
                if c["counter_id"] == counter["id"] and c["status"] == "completed"
            ),
        })
    return {
        "total_scans": total_scans,
        "counters": result_counters,
        "customers": list(reversed(customers[-50:])),
        "alerts": list(reversed(alerts[-20:])),
        "threshold": SCANS_PER_COUNTER,
        "total_counters": TOTAL_COUNTERS,
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/state")
def get_state():
    with lock:
        return snapshot()


@app.post("/api/scan")
def scan_customer(request: CustomerRequest):
    global total_scans, next_customer_id

    with lock:
        active = [c for c in counters if c["active"]]
        # Choose the active counter with the fewest customers currently waiting.
        # Counter ID breaks ties, making the result deterministic.
        selected = min(active, key=lambda c: (counter_load(c["id"]), c["id"]))

        customer = {
            "id": next_customer_id,
            "name": request.name.strip() or "Customer",
            "counter_id": selected["id"],
            "status": "waiting",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        next_customer_id += 1
        customers.append(customer)
        total_scans += 1

        notification = None
        # After each batch of five successful scans, activate the next counter
        # for subsequent customers. Existing assignments never change.
        if total_scans % SCANS_PER_COUNTER == 0:
            inactive = next((c for c in counters if not c["active"]), None)
            if inactive:
                inactive["active"] = True
                inactive["staff_notified"] = True
                notification = {
                    "id": len(alerts) + 1,
                    "message": f"Please open Counter {inactive['id']}. The QR-scan threshold was reached.",
                    "counter_id": inactive["id"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "read": False,
                }
                alerts.append(notification)

        return {
            "customer": customer,
            "notification": notification,
            "state": snapshot(),
        }


@app.post("/api/customers/{customer_id}/complete")
def complete_customer(customer_id: int):
    with lock:
        customer = next((c for c in customers if c["id"] == customer_id), None)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if customer["status"] == "completed":
            return {"customer": customer, "state": snapshot()}
        customer["status"] = "completed"
        customer["completed_at"] = datetime.now(timezone.utc).isoformat()
        return {"customer": customer, "state": snapshot()}


@app.post("/api/alerts/{alert_id}/read")
def read_alert(alert_id: int):
    with lock:
        alert = next((a for a in alerts if a["id"] == alert_id), None)
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert["read"] = True
        return {"alert": alert, "state": snapshot()}


@app.post("/api/reset")
def reset_demo():
    with lock:
        reset_state()
        return snapshot()
