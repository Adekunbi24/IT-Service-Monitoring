from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def test_python_syntax():
    ast.parse((ROOT / "main.py").read_text())

def test_dashboard_admin_controls():
    html = (ROOT / "dashboard.html").read_text()
    assert 'id="addServiceForm"' in html
    assert 'id="adminLoginForm"' in html
    assert 'id="managedServices"' in html

def test_database_schema():
    sql = (ROOT / "init.sql").read_text()
    for table in ("monitoring_history", "monitoring_alerts", "monitored_services"):
        assert f"public.{table}" in sql
