import ipaddress
import socket
import time
import os
import psycopg
from psycopg.rows import dict_row

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl

from fastapi.responses import FileResponse

services = [
    "https://example.com",
    "https://github.com",
    "https://www.python.org"
]

SLOW_RESPONSE_THRESHOLD_MS = 500

from pydantic import Field

class MonitoredServiceCreate(BaseModel):
    service_url: HttpUrl
    check_interval_seconds: int = Field(default=60, ge=10)
    slow_threshold_ms: float = Field(default=2000, gt=0)

def is_public_ip(ip_address):
    address = ipaddress.ip_address(ip_address)
    return address.is_global

def has_only_public_ips(hostname):
    try:
        addresses = socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM
        )

        return bool(addresses) and all(
            is_public_ip(address[4][0])
            for address in addresses
        )

    except (socket.gaierror, ValueError):
        return False

results = []

previous_statuses = {}

previous_performance = {}

last_check_times = {}

def save_alert_to_database(url, alert_type, response_time_ms=None):
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.monitoring_alerts
                (service_url, alert_type, response_time_ms)
                VALUES (%s, %s, %s);
                """,
                (url, alert_type, response_time_ms)
            )

        connection.commit()

async def detect_status_changes(current_results):
    for result in current_results:
        url = result["service"]
        current_status = result["status"]

        previous_status = previous_statuses.get(url)

        if previous_status == "UP" and current_status in ("DOWN", "ERROR"):
            print(f"ALERT: {url} is DOWN!")

            try:
                await asyncio.to_thread(save_alert_to_database, url, "DOWN")
            except psycopg.Error as error:
                print("Failed to save alert:", error)

        elif previous_status in ("DOWN", "ERROR") and current_status == "UP":
            print(f"RECOVERY: {url} is back UP!")

            try:
                await asyncio.to_thread(save_alert_to_database, url, "RECOVERY")
            except psycopg.Error as error:
                print("Failed to save alert:", error)

        previous_statuses[url] = current_status

def get_monitored_services():
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor() as cursor:
            cursor.execute("""
    SELECT service_url, check_interval_seconds, slow_threshold_ms
    FROM public.monitored_services
    WHERE is_active = TRUE;
""")
            rows = cursor.fetchall()

    return [
        {
            "service_url": row[0],
            "check_interval_seconds": row[1],
            "slow_threshold_ms": row[2]
        }
        for row in rows
    ]

def save_monitored_service(
    service_url,
    check_interval_seconds,
    slow_threshold_ms
):
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.monitored_services (
                    service_url,
                    check_interval_seconds,
                    slow_threshold_ms
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (service_url)
                DO UPDATE SET
                    check_interval_seconds = EXCLUDED.check_interval_seconds,
                    slow_threshold_ms = EXCLUDED.slow_threshold_ms;
            """, (
                service_url,
                check_interval_seconds,
                slow_threshold_ms
            ))
def set_service_active(service_url: str, is_active: bool):
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE public.monitored_services
                SET is_active = %s
                WHERE service_url = %s
                RETURNING service_url;
                """,
                (is_active, service_url)
            )

            return cursor.fetchone() is not None

async def monitor_services():
    while True:
        current_results = []

        try:
            active_services = await asyncio.to_thread(get_monitored_services)
        except psycopg.Error as error:
            print("Failed to load monitored services:", error)
            await asyncio.sleep(5)
            continue

        active_urls = {
            service["service_url"]
            for service in active_services
        }

        results[:] = [
            result
            for result in results
            if result["service"] in active_urls
        ]

        for url in list(last_check_times):
            if url not in active_urls:
                last_check_times.pop(url, None)
                previous_statuses.pop(url, None)
                previous_performance.pop(url, None)

        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=False,
            trust_env=False
        ) as client:
            for service in active_services:
                url = service["service_url"]
                check_interval_seconds = service["check_interval_seconds"]
                slow_threshold_ms = service["slow_threshold_ms"]
                now = time.monotonic()
                last_checked = last_check_times.get(url)

                if last_checked is not None:
                    elapsed_time = now - last_checked

                    if elapsed_time < check_interval_seconds:
                        continue

                last_check_times[url] = now
                try:
                    start_time = time.perf_counter()

                    response = await client.get(url)

                    end_time = time.perf_counter()

                    response_time_ms = round((end_time - start_time) * 1000, 2)

                    status = "UP" if response.status_code == 200 else "DOWN"

                    if status == "UP":
                        current_performance = (
                            "SLOW"
                            if response_time_ms > slow_threshold_ms
                            else "NORMAL"
                        )

                        previous = previous_performance.get(url)

                        if previous == "NORMAL" and current_performance == "SLOW":
                            print(f"SLOW RESPONSE: {url} took {response_time_ms} ms")

                            try:
                                await asyncio.to_thread(
                                    save_alert_to_database, url, "SLOW", response_time_ms
                                )
                                print("Slow-response alert saved to PostgreSQL!")
                            except psycopg.Error as error:
                                print("Failed to save slow-response alert:", error)

                        elif previous == "SLOW" and current_performance == "NORMAL":
                            print(f"PERFORMANCE RECOVERY: {url} returned to normal")

                            try:
                                await asyncio.to_thread(
                                    save_alert_to_database, url, "NORMAL", response_time_ms
                                )
                                print("Performance recovery alert saved to PostgreSQL!")
                            except psycopg.Error as error:
                                print("Failed to save performance recovery alert:", error)

                        previous_performance[url] = current_performance
                    else:
                        previous_performance.pop(url, None)

                    current_results.append({
                        "service": url,
                        "status": status,
                        "status_code": response.status_code,
                        "response_time_ms": response_time_ms
                    })

                
                except httpx.RequestError as error:
                    print(
                        f"MONITORING ERROR: {url} - "
                        f"{type(error).__name__}: {error}",
                        flush=True
                    )

                    previous_performance.pop(url, None)

                    current_results.append({
                        "service": url,
                        "status": "ERROR",
                        "status_code": None,
                        "response_time_ms": None
                    })

            for new_result in current_results:
                existing_result = next(
                    (
                        result
                        for result in results
                        if result["service"] == new_result["service"]
                    ),
                    None
                )

                if existing_result is not None:
                    results.remove(existing_result)

                results.append(new_result)

        if current_results:
            await detect_status_changes(current_results)

            print("Automatic monitoring completed:", current_results)

            try:
                await asyncio.to_thread(
                    save_results_to_database,
                    current_results
                )
                print("Monitoring results saved to PostgreSQL!")
            except psycopg.Error as error:
                print("Database error:", error)

        await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(monitor_services())

    try:
        yield
    finally:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.post("/services")
def add_service(service: MonitoredServiceCreate):
    if service.service_url.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="Only HTTPS websites are allowed"
        )

    if service.service_url.port not in (None, 443):
        raise HTTPException(
            status_code=400,
            detail="Only the standard HTTPS port (443) is allowed"
        )

    normalized_url = str(service.service_url).rstrip("/")
    hostname = service.service_url.host

    if not has_only_public_ips(hostname):
        raise HTTPException(
            status_code=400,
            detail="Website must resolve only to public IP addresses"
        )

    try:
        save_monitored_service(
             normalized_url,
            service.check_interval_seconds,
            service.slow_threshold_ms
        )
    except psycopg.Error:
        raise HTTPException(
            status_code=500,
            detail="Failed to save monitoring configuration"
        )

    return {
        "message": "Monitoring configuration saved successfully",
        "service_url": normalized_url,
        "check_interval_seconds": service.check_interval_seconds,
        "slow_threshold_ms": service.slow_threshold_ms
    }


class ServiceStatusUpdate(BaseModel):
    service_url: HttpUrl
    is_active: bool


@app.patch("/services/status")
def update_service_status(service: ServiceStatusUpdate):
    normalized_url = str(service.service_url).rstrip("/")

    try:
        updated = set_service_active(
            normalized_url,
            service.is_active
        )
    except psycopg.Error:
        raise HTTPException(
            status_code=500,
            detail="Failed to update monitoring status"
        )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Monitored service not found"
        )

    return {
        "message": "Monitoring status updated successfully",
        "service_url": normalized_url,
        "is_active": service.is_active
    }

@app.get("/")
def home():
    return {"message": "IT Service Monitoring Application is running"}


@app.get("/check")
def check_services():
    return results

@app.get("/history")
def get_history():
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT id, service_url, status, status_code, checked_at
                FROM public.monitoring_history
                ORDER BY checked_at DESC
                LIMIT 100;
            """)

            records = cursor.fetchall()

    return records

@app.get("/dashboard")
def dashboard():
    return FileResponse("dashboard.html")

def save_results_to_database(current_results):
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor() as cursor:
            for result in current_results:
                cursor.execute(
                    """
                    INSERT INTO public.monitoring_history
                    (service_url, status, status_code, response_time_ms)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (
                        result["service"],
                        result["status"],
                        result["status_code"],
                        result["response_time_ms"]
                    )
                )

        connection.commit()

@app.get("/uptime")
def get_uptime():
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"), 
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT
                    service_url,
                    COUNT(*) AS total_checks,
                    COUNT(*) FILTER (WHERE status = 'UP') AS successful_checks,
                    ROUND(
                        COUNT(*) FILTER (WHERE status = 'UP') * 100.0 / COUNT(*),
                        2
                    ) AS uptime_percentage
                FROM public.monitoring_history
                GROUP BY service_url
                ORDER BY uptime_percentage DESC;
            """)

            records = cursor.fetchall()

    return records   


@app.get("/alerts")
def get_alerts():
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
    SELECT id, service_url, alert_type, response_time_ms, created_at
    FROM public.monitoring_alerts
    ORDER BY created_at DESC
    LIMIT 100;
""")

            records = cursor.fetchall()

    return records

@app.get("/uptime-history")
def get_uptime_history():
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT
                    service_url,
                    DATE(checked_at) AS check_date,
                    COUNT(*) AS total_checks,
                    COUNT(*) FILTER (
                        WHERE status = 'UP'
                    ) AS successful_checks,
                    ROUND(
                        COUNT(*) FILTER (WHERE status = 'UP')
                        * 100.0 / COUNT(*),
                        2
                    ) AS uptime_percentage
                FROM public.monitoring_history
                GROUP BY service_url, DATE(checked_at)
                ORDER BY check_date ASC, service_url ASC;
            """)

            records = cursor.fetchall()

    return records

@app.get("/response-times")
def get_response_times():
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT
                    service_url,
                    COUNT(response_time_ms) AS total_measurements,
                    ROUND(AVG(response_time_ms)::numeric, 2) AS avg_response_time_ms,
                    ROUND(MIN(response_time_ms)::numeric, 2) AS min_response_time_ms,
                    ROUND(MAX(response_time_ms)::numeric, 2) AS max_response_time_ms
                FROM public.monitoring_history
                WHERE response_time_ms IS NOT NULL
                GROUP BY service_url
                ORDER BY avg_response_time_ms ASC;
            """)

            records = cursor.fetchall()

    return records


@app.get("/response-time-history")
def get_response_time_history():
    with psycopg.connect(
       host=os.environ.get("DB_HOST", "localhost"), 
        port=5432,
        dbname="it_service_monitoring",
        user="postgres",
        password=os.environ["DB_PASSWORD"]
    ) as connection:

        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT
                    id,
                    service_url,
                    response_time_ms,
                    checked_at
                FROM public.monitoring_history
                WHERE response_time_ms IS NOT NULL
                ORDER BY checked_at DESC, id DESC
                LIMIT 300;
            """)

            records = cursor.fetchall()

    return records