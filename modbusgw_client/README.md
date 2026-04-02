# ModBus Client Library (`modbusgw-client`)

A companion library for interacting with ModBus devices - either directly or through the [Modbus Gateway](https://github.com/tspspi/modbusgw) - using a consistent, class-based API. The package mirrors the `PDUs` used on the gateway side, exposes pluggable transports (serial RTU, Modbus/TCP over TCP or Unix sockets, optional (m)TLS), and wraps everything behind a convenience dependency-light client.

## Features

* __Unified transport abstraction__: `BaseClient` defines the lifecycle contract, while `SerialClient` and `TcpClient` implement retryable RTU / ModBus-TCP access with context-manager support and sync/async entry points.
* __High-level helper API__: `ModbusClient` offers typed helpers for reading coils, holding/input registers, and writing coils/registers with struct-based encoding/decoding helpers for binary payloads.
* __Raw PDU access__: `ModbusRequest` / `ModbusResponse` classes mirror the gateway side.
* __TLS & Unix socket support__: ModBus/TCP connections can be wrapped in TLS (including mTLS) or redirected over [Unix domain sockets](https://www.tspi.at/2026/01/04/UDS.html) for local-only communication.
* __Error model__: Client-specific exceptions distinguish transport, protocol, and server-side failures, making it easy to hook into retry/backoff logic in higher-level applications.

## Installation

Install from PyPI:

```
pip install modbusgw-client
```

or from this repository while hacking on both the gateway and client in tandem:

```
pip install -e ./modbusgw_client/
```

## Usage Examples

Serial RTU session:

```python
from modbusgw_client import api

with api.ModbusClient.serial("/dev/ttyUSB0", baudrate=9600, unit_id=1) as client:
    coils = client.read_coils(address=0, quantity=8)
    client.write_register(address=10, value=0x1234)
```

Modbus/TCP (with optional TLS) session:

```python
from modbusgw_client import api
from modbusgw_client.tcp_client import TLSConfig

with api.ModbusClient.tcp("192.0.2.15", port=1502, tls=TLSConfig(ca_file="/etc/ssl/certs/root.pem")) as client:
    temps = client.read_input_registers(address=100, quantity=2, fmt=">f")
    client.write_coils(address=20, values=[True, False, True])
```

Both open the transport during the context manager block and close it automatically. In addition to context management one can call `client.connect()` / `client.close()` manually.

## Advanced Usage

* __Transaction batching__: `BaseClient.bulk_execute()` accepts an iterable of pre-built PDUs and returns decoded responses synchronously.
* __Structured payloads__: The helper can pack/unpack register payloads using Python'"'"'s `struct` syntax (`fmt="<f"`, etc.). Validation ensures the byte lengths match full registers before transmission.
* __Unix socket mode__: `ModbusClient.unix()` is a thin wrapper around `TcpClient(unix_socket=...)`, ideal when the gateway exposes its Modbus/TCP frontend [via a local socket with stricter filesystem ACLs](https://www.tspi.at/2026/01/04/UDS.html) than network ACLs.

## Error Handling

All errors derive from `ModbusClientError`:

* `TransportError`: socket/serial open failures, I/O timeouts, CRC/MBAP framing issues, TLS negotiation problems.
* `ConnectionClosed`: attempts to `execute()` without calling `connect()` first.
* `ProtocolError`: decoded response does not match the expected type or violates struct/payload expectations.
* `ModbusServerError`: server-side exception responses (function code ORed with `0x80`); exposes both the function and Modbus exception code for logging or policy decisions.

The high-level helpers raise these exceptions directly, so callers can implement retries or alerting around the specific failure domains.

## Relationship to the Gateway

While the library can talk to any ModBus device, it is primarily intended as the canonical client for the [Modbus Gateway](https://github.com/tspspi/modbusgw) itself. Using the shared PDU classes keeps the gateway routing tests deterministic, makes MQTT mirroring semantics easier to validate, and ensures configuration changes in the daemon can be rehearsed via this standalone package before deploying to hardware.
