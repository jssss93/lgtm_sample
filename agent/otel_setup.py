import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from config import OTEL_ENDPOINT, SERVICE_NAME, AGENT_TYPE

# ──────────────────────────── Resource ──────────────────────────
resource = Resource(attributes={
    "service.name": SERVICE_NAME,
    "agent.type": AGENT_TYPE,
})

# ──────────────────────────── Traces ────────────────────────────
trace_provider = TracerProvider(resource=resource)
trace_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer(__name__)

# ──────────────────────────── Metrics ───────────────────────────
metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True),
    export_interval_millis=5000,
)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(__name__)

# Counters & Histograms
agent_run_counter = meter.create_counter("agent.run.count", description="Number of agent runs")
agent_error_counter = meter.create_counter("agent.error.count", description="Number of agent errors")
llm_call_duration = meter.create_histogram("llm.call.duration", description="LLM call duration", unit="s")
token_usage_counter = meter.create_counter("llm.token.usage", description="Token usage")
cost_counter = meter.create_counter("llm.cost.usd", description="LLM cost in USD")
request_token_histogram = meter.create_histogram("llm.tokens.per_request", description="Tokens per request")
rate_limit_counter = meter.create_counter("llm.rate_limit.count", description="429 rate limit hits")
retry_counter = meter.create_counter("llm.retry.count", description="LLM call retries")
cache_hit_counter = meter.create_counter("cache.hit.count", description="Cache hits")
cache_miss_counter = meter.create_counter("cache.miss.count", description="Cache misses")
quota_reject_counter = meter.create_counter("quota.reject.count", description="Quota rejections")

# ──────────────────────────── Logs ──────────────────────────────
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTEL_ENDPOINT, insecure=True))
)
handler = LoggingHandler(logger_provider=logger_provider)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("agent")

# ──────────────────────────── Instrumentors ─────────────────────
HTTPXClientInstrumentor().instrument()


def shutdown_providers():
    """Graceful shutdown for all OTel providers."""
    trace_provider.shutdown()
    meter_provider.shutdown()
    logger_provider.shutdown()
