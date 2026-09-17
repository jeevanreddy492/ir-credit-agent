"""Cloud Logging + Cloud Trace (OpenTelemetry). Falls back to stdout / no exporter locally."""
import logging

from app.config import settings


def setup_logging() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if settings.app_env != "local":
        try:
            import google.cloud.logging
            google.cloud.logging.Client().setup_logging(log_level=settings.log_level)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning("Cloud Logging unavailable; using stdout")


def setup_tracing(app) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        if settings.app_env != "local":
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
            provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=settings.google_cloud_project)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("tracing not configured")
