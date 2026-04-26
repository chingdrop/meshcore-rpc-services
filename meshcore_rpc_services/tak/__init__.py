"""TAK / ATAK bridge.

A small read-only consumer that turns the retained `mc/...` node-state
topics into Cursor-on-Target events streamed to a TAK Server over TCP.

Lives in the same package as the RPC service because both:
  * are Python application logic (no radio code, no hardware),
  * share the MQTT contract (`meshcore_rpc_services.mqtt.topics`),
  * deploy to the same Pi (or sibling box on the home LAN),
  * version and ship together.

Runs as a separate process via the `meshcore-tak-bridge` CLI entry point,
so its lifecycle is independent of the RPC service. They just live in
the same source tree.
"""
