"""Data integration connectors — ported from the desktop app's backend/connectors/.

Each connector implements test()/preview(n)/fetch_all(max_rows) and optionally
iter_rows(max_rows) for streaming. See app/services/integrations.py for the
generic CRUD/test/preview/sync flow that drives these.
"""
from __future__ import annotations

from typing import Any

VALID_TYPES = {
    "csv", "excel",
    "sqlserver", "oracle", "postgres", "mysql",
    "sap_odata", "erp_rest", "mes_rest",
    "opcua", "mqtt",
    "opcda", "pi_webapi",
}


def get_connector(conn_type: str, config: dict[str, Any]):
    """Return an initialized connector for the given type."""
    t = (conn_type or "").strip().lower()

    if t == "csv":
        from app.services.connectors.csv_connector import CsvConnector
        return CsvConnector(config)
    if t == "excel":
        from app.services.connectors.excel_connector import ExcelConnector
        return ExcelConnector(config)
    if t == "sqlserver":
        from app.services.connectors.sqlserver_connector import SqlServerConnector
        return SqlServerConnector(config)
    if t == "oracle":
        from app.services.connectors.oracle_connector import OracleConnector
        return OracleConnector(config)
    if t == "postgres":
        from app.services.connectors.postgres_connector import PostgresConnector
        return PostgresConnector(config)
    if t == "mysql":
        from app.services.connectors.mysql_connector import MysqlConnector
        return MysqlConnector(config)
    if t in ("sap_odata", "erp_rest", "mes_rest"):
        from app.services.connectors.rest_connector import RestConnector
        return RestConnector(config)
    if t == "opcua":
        from app.services.connectors.opcua_connector import OpcUaConnector
        return OpcUaConnector(config)
    if t == "mqtt":
        from app.services.connectors.mqtt_connector import MqttConnector
        return MqttConnector(config)
    if t == "opcda":
        from app.services.connectors.opcda_connector import OpcDaConnector
        return OpcDaConnector(config)
    if t == "pi_webapi":
        from app.services.connectors.pi_connector import PiConnector
        return PiConnector(config)

    raise ValueError(f"Unknown connector type: {conn_type!r}")
