# Modbus Gateway

A flexible and extensible ModBus gateway written in Python, supporting pluggable frontends and backends, advanced routing, and security filtering.

The gateway allows to bridge, transform, and secure ModBus communication across heterogeneous systems - from serial devices to TCP and Unix domain sockets - while maintaining full control over addressing and access boundaries. It exposes a variety of frontends (emulating serial ports, ModBus TCP over UDS or IP with and without (m)TLS support) as well as backends (serial ports, ModBus IP targets, etc.). It allows multiple applications to access the same backends, providing arbitration and synchronization.

This project has been developed to allow multiple services to access various services attached to the same hardware ModBus network on a machine exploiting multiple RS485 interfaces (identified via [unique device names](https://www.tspi.at/2023/06/26/cp2102nuniquedevd.html)).

## Features

* __Pluggable architecture__: Modular frontends and backends, allowing further extensions in future versions.
* __Multiple frontends__: Virtual serial ports (`pty`), ModBus TCP over TCP and Unix domain sockets
* __Secure TCP support__: TLS and mTLS (with client certificate authentication) for ModBus TCP over TCP/IP
* __Multiple backends__: Hardware serial ports and ModBus TCP IP backends
* __Flexible Routing Engine__: Map device IDs and registers between frontends and backends; split or aggregate devices across multiple backends and passthrough mode for transparent forwarding
* __Security Filtering__: Per-frontend filtering rules enforcing access boundaries on register/device level. Ideal for isolating subsystems or exposing limited views.
* __Future extensions__: Planned MQTT interface for IoT integration

### Work in Progress

The following features are still work in progress:

* Appropriate daemonization (currently the gateway executes in foreground)
* MQTT support
* REST API support

## Installation

The gateway can be installed via PyPi via

```
pip install modbus-gateway
```

or from the repository root via

```
pip install -e .
```

The associated client library is available via

```
pip install modbusgw-client
```

Again it can also be installed from the repositories `modbusgw_client`
subdirectory via

```
pip install -e ./modbusgw_client/
```

## Configuration

The default configuration file is located at `~/.config/modbusgateway.cfg`. It
is composed of a single large JSON dictionary consisting of the following keys:

* `service` provides configuration of the main daemon
* `bus` configures the internal message bus
* `frontends` contains a list of frontend configurations over which clients
  are capable of accessing the daemon
* `backends` is the counterparts and defines the interfaces that are accessed
  on behalf of the clients via the gateway.
* `routes` provides a match-list based configuration on how to route messages 
  between frontends and backends.

The `service` section configures PID file to prevent multiple running
instances, the state directory that will be used for log- and tracefiles
as well as the loglevel:

```
"service" : {
   "log_level" : "INFO",
   "pid_file" : "/var/run/modbusgw.pid",
   "state_dir" : "/var/modbusgw/",
   "reload_grace_seconds" : 5
}
```

The `bus` configuration configures the internal buffer for incoming requests
that are routed to various backends:

```
"bus" : {
   "request_queue_size" : 64,
   "response_timeout_ms" : 1500
}
```

Note that this timeout should be shorter than the applications and frontends
timeouts.

### Frontend Configurations

#### Virtual Serial Ports (pty)

Virtual serial ports are directly accessible via `pyserial`  and similar interfaces.
This allows existing legacy software to access the gateway via unmodified code by
pointing it at the virtual serial port file handles:

```
{
   "id" : "virtual_serial_rtu",
   "type" : "serial_rtu_socket",
   "socket_path" : "/var/modbusgw/ttyBus0",
   "pty_mode" : "rw",
   "idle_close_seconds" : 600,
   "frame_timeout_ms" : 5.0
}
```

The shown configuration instantiates a virtual serial port at the specified `socket_path`,
allowing read-write transactions. The frame timeout handles incomplete messages on the
application side. The name `virtual_serial_rtu` is an arbitrary chosen name that is
used in the routing configuration.

#### ModBus IP TCP Socket

A ModBus IP socket speaks the ModBus IP protocol over an TCP socket (optionally
supporting TLS or mTLS for authenticated sessions). The following configuration exposes
unencrypted ModBus IP applying only IP subnet based filters:

```
{
   "id" : "frontend_tcp",
   "type" : "tcp_modbus_tcp",
   "host" : "192.0.2.1",
   "port" : 1234,
   "cidr_allow" : [
      "127.0.0.0/8",
      "192.0.2.0/24"
   ]
}
```

If TLS is desired the following configuration can be added to the frontend configuration
object:

```
   "tls" : {
      "cert_file" : "/path/to/server.crt",
      "key_file" : "/path/to/server.key",
      "ca_file" : "/path/to/rootca.crt",
      "require_client_cert" : true,
      "client_dn_allow" : [
         "CN=ModbusGW Test Client"
      ]
   }
```

The `cert_file` and `key_file` establish the server identity. The `ca_file` is only
used when `require_client_cert` is set to `true` to allow client authentication. The
additional (optional) `client_dn_allow` filter allows to filter the DNs from
valid certificates (after certificate validation) that are allowed to access the frontend.

### Backend Configurations

#### Hardware Serial Ports

The `pyserial` backend uses the [pyserial](https://pypi.org/project/pyserial/) library
to access an USB to RS485 based interface. This is the most simple hardware interface 
for DIY setups. The specified serial configuration is applied when accessing the backend.
Again the arbitrary `id` is used in the routing configuration.

```
{
   "id" : "hardware_serial",
   "type" : "pyserial",
   "device" : "/dev/ttyU0",
   "baudrate" : 9600,
   "parity" : "N",
   "stop_bits" : 1,
   "request_timeout_ms" : 1200
}
```

#### ModBus IP via TCP

A TCP backend can be configured via the `tcp_modbus` backend:

```
{
   "id" : "tcp_backend",
   "type" : "tcp_modbus",
   "host" : "127.0.0.1",
   "port" : 1234,
   "connect_timeout" : 2.0,
   "pool_size" : 2,
   "use_tls" : true,
   "tls" : {
      "ca_file" : "/path/to/root.crt",
      "cert_file" : "/path/to/client.crt",
      "key_file" : "/path/to/client.key"
   }
}
```

The `use_tls` and `tls` blocks are optional and are only used when (m)TLS is
desired. The `root.crt` is used for validation, the client keys for authentication
via mTLS.

### Routing Configuration

The routing configuration is provided as a list of routing commands that are matched
against incoming requests from the frontends. The first match determines to which backend 
a message is routed. The `backend` key and the `mirror_to_mqtt` key is not used
for matching, all other fields apply:

```
{
   "frontend" : "virtual_serial_rtu",
   "backend" : "hardware_serial",
   "match" : {
      "unit_ids" : [ "*" ],
      "function_codes" : [ "*" ]
   },
   "mirror_to_mqtt" : [ ]
}
```

The routing `match` block allows to filter given device IDs and function codes
as well as operations. For example to allow only function code 1 (read coils)
for the virtual device `5`, redirecting the operation to the backend device id `1`,
one would use

```
{
   "frontend" : "virtual_serial_rtu",
   "backend" : "hardware_serial",
   "match" : {
      "unit_ids" : [ 5 ],
      "function_codes" : [ 1 ],
      "operations" : [ "read" ]
   },
   "unit_override" : 1,
   "mirror_to_mqtt" : [ ]
}
```

Here the `match` block specifies conditions that _have_ to be fulfilled (all
have to be fulfilled). The optional `unit_override` replaces the device ID
on the virtual frontend bus to the given unit number before handing off the
the backend device. All fields can be used in arbitrary combinations.

### Example configuration file

The following configuration exposes a single serial to RS485 interface
via a local virtual serial port as well as a ModBus IP socket available
via unencrypted TCP:

```
{
   "service" : {
      "log_level" : "INFO",
      "pid_file" : "/var/run/modbusgw.pid",
      "state_dir" : "/var/modbusgw/",
      "reload_grace_seconds" : 5
   },
   "bus" : {
      "request_queue_size" : 64,
      "response_timeout_ms" : 1500
   },
   "frontends" : [
      {
         "id" : "virtual_serial_rtu",
         "type" : "serial_rtu_socket",
         "socket_path" : "/var/modbusgw/ttyBus0",
         "pty_mode" : "rw",
         "idle_close_seconds" : 600,
         "frame_timeout_ms" : 5.0
      },
      {
         "id" : "frontend_tcp",
         "type" : "tcp_modbus_tcp",
         "host" : "192.0.2.1",
         "port" : 1234,
         "cidr_allow" : [
            "127.0.0.0/8",
            "192.0.2.0/24"
         ]
      }
   ],
   "backends" : [
      {
         "id" : "hardware_serial",
         "type" : "pyserial",
         "device" : "/dev/ttyU0",
         "baudrate" : 9600,
         "parity" : "N",
         "stop_bits" : 1,
         "request_timeout_ms" : 1200
      }
   ],
   "routes" : [
      {
         "frontend" : "virtual_serial_rtu",
         "backend" : "hardware_serial",
         "match" : {
            "unit_ids" : [ "*" ],
            "function_codes" : [ "*" ]
         },
         "mirror_to_mqtt" : [ ]
      },
      {
         "frontend" : "frontend_tcp",
         "backend" : "hardware_serial",
         "match" : {
            "unit_ids" : [ "*" ],
            "function_codes" : [ "*" ]
         },
         "mirror_to_mqtt" : [ ]
      }
   ]
}
```

## Client Library

This repository also contains an independent client library for interacting with ModBus systems via serial ports or ModBus TCP. The documentation is found in the `modbusgw-client` directory.

