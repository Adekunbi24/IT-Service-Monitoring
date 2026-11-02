import time
import os
import psycopg
from psycopg.rows import dict_row

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from fastapi.responses import FileResponse

services = [
    "https://example.com",
    "https://github.com",
    "https://www.python.org"
]

SLOW_RESPONSE_THRESHOLD_MS = 500

results = []

previous_statuses = {}

previous_performance = {}

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

        if previous_status == "UP" and current_status == "DOWN":
            print(f"ALERT: {url} is DOWN!")

            try:
                await asyncio.to_thread(save_alert_to_database, url, "DOWN")
            except psycopg.Error as error:
                print("Failed to save alert:", error)

        elif previous_status == "DOWN" and current_status == "UP":
            print(f"RECOVERY: {url} is back UP!")

            try:
                await asyncio.to_thread(save_alert_to_database, url, "RECOVERY")
            except psycopg.Error as error:
                print("Failed to save alert:", error)

        previous_statuses[url] = current_status

async def monitor_services():
    while True:
        current_results = []

        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in services:
                try:
                    start_time = time.perf_counter()

                    response = await client.get(url)

                    end_time = time.perf_counter()

                    response_time_ms = round((end_time - start_time) * 1000, 2)

                    status = "UP" if response.status_code == 200 else "DOWN"

                    if status == "UP":
                        current_performance = (
                            "SLOW"
                            if response_time_ms > SLOW_RESPONSE_THRESHOLD_MS
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


        results.clear()
        results.extend(current_results)

        await detect_status_changes(current_results)

        print("Automatic monitoring completed:", results)
        try:
            await asyncio.to_thread(save_results_to_database, current_results)
            print("Monitoring results saved to PostgreSQL!")
        except psycopg.Error as error:
            print("Database error:", error)

        await asyncio.sleep(60)


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