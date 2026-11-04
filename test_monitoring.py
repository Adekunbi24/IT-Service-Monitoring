
import asyncio
from unittest.mock import patch
import httpx

from main import detect_status_changes, previous_statuses


def test_website_goes_down():
    # Pretend the website was previously UP
    previous_statuses.clear()
    previous_statuses["https://example.com"] = "UP"

    # Pretend the website is now DOWN
    current_results = [
        {"service": "https://example.com", "status": "DOWN"}
    ]

    # Replace the real database-saving function with a fake one
    with patch("main.save_alert_to_database") as mock_save:
        asyncio.run(detect_status_changes(current_results))

        # Check that the correct alert was generated
        mock_save.assert_called_once_with(
            "https://example.com", "DOWN"
        )

    # Check that the new status was remembered
    assert previous_statuses["https://example.com"] == "DOWN"

def test_website_recovers():
    # Pretend the website was previously DOWN
    previous_statuses.clear()
    previous_statuses["https://example.com"] = "DOWN"

    # Pretend the website is now UP
    current_results = [
        {"service": "https://example.com", "status": "UP"}
    ]

    with patch("main.save_alert_to_database") as mock_save:
        asyncio.run(detect_status_changes(current_results))

        # Check that a RECOVERY alert was generated
        mock_save.assert_called_once_with(
            "https://example.com", "RECOVERY"
        )

    # Check that the website's new status was remembered
    assert previous_statuses["https://example.com"] == "UP"



def test_website_stays_up():
    previous_statuses.clear()
    previous_statuses["https://example.com"] = "UP"

    current_results = [
        {"service": "https://example.com", "status": "UP"}
    ]

    with patch("main.save_alert_to_database") as mock_save:
        asyncio.run(detect_status_changes(current_results))

        # No alert should be saved
        mock_save.assert_not_called()

    assert previous_statuses["https://example.com"] == "UP"


def test_website_stays_down():
    previous_statuses.clear()
    previous_statuses["https://example.com"] = "DOWN"

    current_results = [
        {"service": "https://example.com", "status": "DOWN"}
    ]

    with patch("main.save_alert_to_database") as mock_save:
        asyncio.run(detect_status_changes(current_results))

        # No new alert should be generated
        mock_save.assert_not_called()

    assert previous_statuses["https://example.com"] == "DOWN"


from unittest.mock import AsyncMock, MagicMock
import main


def test_successful_website_monitoring():
    async def run_test():
        fake_response = MagicMock()
        fake_response.status_code = 200

        fake_client = AsyncMock()
        fake_client.get.return_value = fake_response

        fake_context = AsyncMock()
        fake_context.__aenter__.return_value = fake_client

        async def stop_after_one_cycle(seconds):
            raise asyncio.CancelledError()

        with (
            patch.object(main, "services", ["https://example.com"]),
            patch.object(main.httpx, "AsyncClient", return_value=fake_context),
            patch.object(main, "detect_status_changes", new_callable=AsyncMock),
            patch.object(main, "save_results_to_database"),
            patch.object(main.asyncio, "sleep", side_effect=stop_after_one_cycle),
        ):
            try:
                await main.monitor_services()
            except asyncio.CancelledError:
                pass

            assert main.results[0]["service"] == "https://example.com"
            assert main.results[0]["status"] == "UP"
            assert main.results[0]["status_code"] == 200

    asyncio.run(run_test())


def test_unreachable_website():
    async def run_test():
        fake_client = AsyncMock()
        fake_client.get.side_effect = httpx.ConnectError(
            "Connection failed"
        )

        fake_context = AsyncMock()
        fake_context.__aenter__.return_value = fake_client

        async def stop_after_one_cycle(seconds):
            raise asyncio.CancelledError()

        with (
            patch.object(main, "services", ["https://example.com"]),
            patch.object(main.httpx, "AsyncClient", return_value=fake_context),
            patch.object(main, "detect_status_changes", new_callable=AsyncMock),
            patch.object(main, "save_results_to_database"),
            patch.object(main.asyncio, "sleep", side_effect=stop_after_one_cycle),
        ):
            try:
                await main.monitor_services()
            except asyncio.CancelledError:
                pass

            assert main.results[0]["status"] == "ERROR"
            assert main.results[0]["status_code"] is None
            assert main.results[0]["response_time_ms"] is None

    asyncio.run(run_test())


def test_slow_website_response():
    async def run_test():
        url = "https://example.com"

        main.previous_performance.clear()
        main.previous_performance[url] = "NORMAL"

        fake_response = MagicMock()
        fake_response.status_code = 200

        fake_client = AsyncMock()
        fake_client.get.return_value = fake_response

        fake_context = AsyncMock()
        fake_context.__aenter__.return_value = fake_client

        async def stop_after_one_cycle(seconds):
            raise asyncio.CancelledError()

        with (
            patch.object(main, "services", [url]),
            patch.object(main.httpx, "AsyncClient", return_value=fake_context),
            patch.object(main.time, "perf_counter", side_effect=[10.0, 10.8]),
            patch.object(main, "save_alert_to_database") as mock_alert,
            patch.object(main, "save_results_to_database"),
            patch.object(main, "detect_status_changes", new_callable=AsyncMock),
            patch.object(main.asyncio, "sleep", side_effect=stop_after_one_cycle),
        ):
            try:
                await main.monitor_services()
            except asyncio.CancelledError:
                pass

            mock_alert.assert_called_once_with(url, "SLOW", 800.0)

            assert main.results[0]["status"] == "UP"
            assert main.results[0]["response_time_ms"] == 800.0
            assert main.previous_performance[url] == "SLOW"

    asyncio.run(run_test())


def test_performance_recovery():
    async def run_test():
        url = "https://example.com"

        main.previous_performance.clear()
        main.previous_performance[url] = "SLOW"

        fake_response = MagicMock()
        fake_response.status_code = 200

        fake_client = AsyncMock()
        fake_client.get.return_value = fake_response

        fake_context = AsyncMock()
        fake_context.__aenter__.return_value = fake_client

        async def stop_after_one_cycle(seconds):
            raise asyncio.CancelledError()

        with (
            patch.object(main, "services", [url]),
            patch.object(main.httpx, "AsyncClient", return_value=fake_context),
            patch.object(main.time, "perf_counter", side_effect=[10.0, 10.2]),
            patch.object(main, "save_alert_to_database") as mock_alert,
            patch.object(main, "save_results_to_database"),
            patch.object(main, "detect_status_changes", new_callable=AsyncMock),
            patch.object(main.asyncio, "sleep", side_effect=stop_after_one_cycle),
        ):
            try:
                await main.monitor_services()
            except asyncio.CancelledError:
                pass

            mock_alert.assert_called_once_with(url, "NORMAL", 200.0)

            assert main.results[0]["status"] == "UP"
            assert main.results[0]["response_time_ms"] == 200.0
            assert main.previous_performance[url] == "NORMAL"

    asyncio.run(run_test())






