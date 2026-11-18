# User Configuration

Webhooks and services are configured in a YAML or JSON file. The worker reads it from the path in
the `JELLYHOOK_CONFIG_PATH` environment variable (default `/config/jellyhook.json`).

The file lets you:

- Configure multiple webhooks with independent settings
- Enable or disable specific services for each webhook
- Set service execution priorities
- Provide custom configuration for each service
- Define metadata updates based on file paths or content patterns

Starting points: [examples/jellyhook.example.yaml](../examples/jellyhook.example.yaml) and
[examples/jellyhook.example.json](../examples/jellyhook.example.json).
