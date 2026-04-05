"""Pydantic configuration models."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, HttpUrl, PositiveFloat, PositiveInt


class ServiceConfig(BaseModel):
    log_level: str = 'INFO'
    pid_file: Path = Path('/var/run/modbusgw.pid')
    state_dir: Path = Path('/var/lib/modbusgw')
    log_file: Path | None = None
    reload_grace_seconds: PositiveInt = 5


class BusConfig(BaseModel):
    request_queue_size: PositiveInt = 1024
    response_timeout_ms: PositiveInt = 1500


class TracingConfig(BaseModel):
    enabled: bool = False
    targets: list[str] = Field(default_factory=list)
    file: Path | None = None


class RateLimitConfig(BaseModel):
    tokens_per_second: PositiveFloat
    burst_size: PositiveFloat | None = None


class SecurityConfig(BaseModel):
    ip_allow: list[str] = Field(default_factory=list)
    rate_limit: RateLimitConfig | None = None


class RetryConfig(BaseModel):
    backoff_min: PositiveFloat = 0.5
    backoff_max: PositiveFloat = 30.0
    max_attempts: PositiveInt = 3


class FrontendBaseConfig(BaseModel):
    id: str
    type: str
    retry: RetryConfig | None = None
    rate_limit: RateLimitConfig | None = None

    model_config = dict(extra='allow')


class SerialRtuSocketConfig(FrontendBaseConfig):
    type: Literal['serial_rtu_socket']
    socket_path: Path
    pty_mode: Literal['rw'] = 'rw'
    idle_close_seconds: PositiveInt = 600
    frame_timeout_ms: PositiveFloat = 5.0


class UnixModbusTcpConfig(FrontendBaseConfig):
    type: Literal['unix_modbus_tcp']
    socket_path: Path
    max_clients: PositiveInt = 64


class TlsConfig(BaseModel):
    ca_file: Path | None = None
    cert_file: Path | None = None
    key_file: Path | None = None
    require_client_cert: bool = False
    client_dn_allow: list[str] = Field(default_factory=list)
    verify_server_cert: bool = True


class TcpModbusFrontendConfig(FrontendBaseConfig):
    type: Literal['tcp_modbus_tcp']
    host: str = '0.0.0.0'
    port: PositiveInt = 502
    tls: TlsConfig | None = None
    cidr_allow: list[str] = Field(default_factory=list)


FrontendConfig = Annotated[
    Union[SerialRtuSocketConfig, UnixModbusTcpConfig, TcpModbusFrontendConfig],
    Field(discriminator='type')
]


class SerialBackendConfig(BaseModel):
    id: str
    type: Literal['pyserial'] = 'pyserial'
    device: Path
    baudrate: PositiveInt
    parity: Literal['N', 'E', 'O'] = 'N'
    stop_bits: PositiveFloat = 1
    request_timeout_ms: PositiveInt = 1200
    retry: RetryConfig = RetryConfig()


class TcpBackendConfig(BaseModel):
    id: str
    type: Literal['tcp_modbus'] = 'tcp_modbus'
    host: str
    port: PositiveInt
    use_tls: bool = False
    tls: TlsConfig | None = None
    connect_timeout: PositiveFloat = 2.0
    pool_size: PositiveInt = 4


BackendConfig = Annotated[
    Union[SerialBackendConfig, TcpBackendConfig],
    Field(discriminator='type')
]


class RangeConfig(BaseModel):
    start: PositiveInt
    end: PositiveInt

    model_config = dict(extra='forbid')


class RouteMatchConfig(BaseModel):
    unit_ids: list[Union[int, Literal['*']]]
    function_codes: list[Union[int, Literal['*']]]
    register_range: RangeConfig | None = None
    operations: list[Literal['read', 'write']] | None = None


class RouteConstraints(BaseModel):
    max_quantity: PositiveInt | None = None
    min_interval_ms: PositiveInt | None = None


class RouteConfig(BaseModel):
    frontend: str
    backend: str
    match: RouteMatchConfig
    unit_override: int | None = None
    mirror_to_mqtt: list[str] = Field(default_factory=list)
    allow_write: bool = True
    fallback_backend: str | None = None
    op_constraints: RouteConstraints | None = None


class MQTTMappingConfig(BaseModel):
    id: str
    topic: str
    direction: Literal['publish', 'subscribe', 'both']
    backend: str
    function: str | None = None
    unit_id: int
    register_address: PositiveInt
    quantity: PositiveInt | None = None
    scale: float | None = None
    offset: float | None = None
    payload_type: Literal['raw', 'json', 'float'] | None = None
    qos: int = 0
    retain: bool = False
    min_value: float | None = None
    max_value: float | None = None


class MQTTReconnectBackoff(BaseModel):
    min: PositiveFloat = 1.0
    max: PositiveFloat = 60.0


class MQTTSettings(BaseModel):
    host: str
    port: PositiveInt = 8883
    use_tls: bool = True
    ca_file: Path | None = None
    client_cert: Path | None = None
    client_key: Path | None = None
    username: str | None = None
    password_file: Path | None = None
    reconnect_backoff: MQTTReconnectBackoff = MQTTReconnectBackoff()


class MQTTConfig(BaseModel):
    settings: MQTTSettings
    mappings: list[MQTTMappingConfig] = Field(default_factory=list)


class BrokerConfig(BaseModel):
    type: Literal['mqtt', 'pastry']
    enabled: bool = True
    options: dict[str, object] = Field(default_factory=dict)


class GatewayConfig(BaseModel):
    service: ServiceConfig = ServiceConfig()
    bus: BusConfig = BusConfig()
    tracing: TracingConfig | None = None
    security: SecurityConfig | None = None
    frontends: list[FrontendConfig] = Field(default_factory=list)
    backends: list[BackendConfig] = Field(default_factory=list)
    routes: list[RouteConfig] = Field(default_factory=list)
    mqtt: MQTTConfig | None = None
    brokers: list[BrokerConfig] = Field(default_factory=list)
