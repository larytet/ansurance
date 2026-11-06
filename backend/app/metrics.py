"""Prometheus metrics. Served at /metrics on the API's internal port only: the proxy and the
load balancer forward just /api/*, so this is never reachable from outside the network.

Request metrics are recorded as requests happen. Everything else (queue depth, intake
outcomes, audit and backup freshness) is read from the database at scrape time, so it is
always current and survives restarts.
"""

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.audit.readbuffer import ReadAuditBuffer


def _age(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return (now - datetime.fromisoformat(value.split("|")[0])).total_seconds()
    except ValueError:
        return None


class DatabaseCollector(Collector):
    def __init__(self, maker: "sessionmaker[Session]", buffer: ReadAuditBuffer | None) -> None:
        self._maker, self._buffer = maker, buffer

    def collect(self) -> Iterable[GaugeMetricFamily]:
        up = GaugeMetricFamily("ansurance_database_up", "1 when the database answers")
        try:
            with self._maker() as db:
                yield from self._from_db(db)
            up.add_metric([], 1)
        except Exception:  # noqa: BLE001  (a failing scrape must not break the endpoint)
            up.add_metric([], 0)
        yield up
        if self._buffer is not None:
            pending = GaugeMetricFamily(
                "ansurance_read_audit_pending", "Read-audit entries not yet written"
            )
            pending.add_metric([], self._buffer.pending())
            dropped = GaugeMetricFamily(
                "ansurance_read_audit_dropped_total", "Read-audit entries dropped (queue full)"
            )
            dropped.add_metric([], self._buffer.dropped)
            yield pending
            yield dropped

    def _from_db(self, db: Session) -> Iterable[GaugeMetricFamily]:
        now = datetime.now(UTC)
        jobs = GaugeMetricFamily("ansurance_jobs", "Background jobs by status", labels=["status"])
        for status, n in db.execute(text("SELECT status, count(*) FROM job GROUP BY status")):
            jobs.add_metric([status], n)
        yield jobs
        oldest = db.execute(
            text(
                "SELECT extract(epoch FROM now() - min(run_at)) FROM job "
                "WHERE status = 'queued' AND run_at <= now()"
            )
        ).scalar()
        g = GaugeMetricFamily(
            "ansurance_jobs_oldest_queued_seconds", "Age of the oldest waiting job"
        )
        g.add_metric([], float(oldest or 0))
        yield g

        intake = GaugeMetricFamily(
            "ansurance_intake_items", "Intake items by state (last 30 days)", labels=["state"]
        )
        for state, n in db.execute(
            text(
                "SELECT state, count(*) FROM intake_item "
                "WHERE received_at > now() - interval '30 days' GROUP BY state"
            )
        ):
            intake.add_metric([state], n)
        yield intake
        misfiled = db.execute(
            text(
                "SELECT count(*) FROM intake_feedback WHERE kind = 'wrong_client' "
                "AND at > now() - interval '30 days'"
            )
        ).scalar()
        m = GaugeMetricFamily("ansurance_intake_misfiled_30d", "Documents moved to another client")
        m.add_metric([], misfiled or 0)
        yield m

        inbox = GaugeMetricFamily(
            "ansurance_inbox_open", "Open Inbox items by role", labels=["role"]
        )
        for role, n in db.execute(
            text("SELECT role, count(*) FROM inbox_item WHERE status = 'open' GROUP BY role")
        ):
            inbox.add_metric([role], n)
        yield inbox

        info: dict[str, str] = dict(
            db.execute(
                text(
                    "SELECT key, value FROM platform_info WHERE key LIKE 'audit.%' OR key LIKE 'backup.%'"
                )
            ).all()
        )
        audit_verify = info.get("audit.last_verify")
        ok = GaugeMetricFamily(
            "ansurance_audit_verify_ok", "1 if the last audit verification passed"
        )
        if audit_verify:
            ok.add_metric([], 1 if audit_verify.endswith("|ok") else 0)
        yield ok
        for name, doc, key in (
            (
                "ansurance_audit_verify_age_seconds",
                "Seconds since the last audit verification",
                "audit.last_verify",
            ),
            (
                "ansurance_backup_age_seconds",
                "Seconds since the last successful backup",
                "backup.last_success",
            ),
            (
                "ansurance_backup_files_sync_age_seconds",
                "Seconds since the originals were last copied",
                "backup.last_files_sync",
            ),
            (
                "ansurance_restore_test_age_seconds",
                "Seconds since the last restore self-test",
                "backup.last_restore_test",
            ),
        ):
            gauge = GaugeMetricFamily(name, doc)
            age = _age(info.get(key), now)
            if age is not None:
                gauge.add_metric([], age)
            yield gauge
        rt = info.get("backup.last_restore_test")
        r = GaugeMetricFamily("ansurance_restore_test_ok", "1 if the last restore self-test passed")
        if rt:
            r.add_metric([], 1 if rt.endswith("|ok") else 0)
        yield r


def install(app: FastAPI, maker: "sessionmaker[Session]", buffer: ReadAuditBuffer | None) -> None:
    registry = CollectorRegistry()
    requests = Counter(
        "ansurance_http_requests_total",
        "HTTP requests",
        ["method", "route", "status"],
        registry=registry,
    )
    latency = Histogram(
        "ansurance_http_request_duration_seconds",
        "HTTP request latency",
        ["route"],
        registry=registry,
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    )
    registry.register(DatabaseCollector(maker, buffer))

    @app.middleware("http")
    async def record(request: Request, call_next: Callable[[Request], Any]) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"  # a template, never raw ids
        requests.labels(request.method, template, str(response.status_code)).inc()
        latency.labels(template).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(registry), media_type="text/plain; version=0.0.4")
