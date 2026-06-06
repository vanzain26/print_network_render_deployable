# Local client API mapping

This hosted app exposes the registration portal and routing hub under one base URL.

```text
BASE_URL=https://your-render-app.onrender.com
```

## Connect an author node

```http
POST /api/routing/connect
Content-Type: application/json

{
  "registration_code": "REG-....",
  "node_type": "author_node",
  "display_name": "Author Node Laptop",
  "capabilities": {"client":"author_node_tkinter"}
}
```

## Connect a device node

```http
POST /api/routing/connect
Content-Type: application/json

{
  "registration_code": "REG-....",
  "node_type": "device_node",
  "display_name": "Garage Printer",
  "capabilities": {
    "installed_nozzle_mm": "0.4",
    "loaded_material": "PLA",
    "usb_port": "COM3",
    "receive_jobs": true
  }
}
```

## Register a bundle from the author node

```http
POST /api/jobs/register-bundle
Content-Type: application/json

{
  "author_connection_id": "CONN-....",
  "device_connection_id": "CONN-....",
  "title": "Bracket test print",
  "source_gcode_filename": "bracket.gcode",
  "authorized_count": 3,
  "price_per_print_cents": 800,
  "instances": [
    {
      "author_job_code": "JOB-AUTH-001",
      "gcode_filename": "bracket_instance_001.gcode",
      "gcode_text": "; demo gcode",
      "metadata": {"nozzle_mm":"0.4","material":"PLA"}
    }
  ]
}
```

If fewer instance objects are supplied than `authorized_count`, the routing hub will generate missing job IDs/codes.

## Device job lifecycle

```text
GET  /api/jobs/available?device_connection_id=CONN-...
POST /api/jobs/claim
POST /api/jobs/start
POST /api/jobs/complete
```

Completion payload:

```json
{
  "device_connection_id": "CONN-....",
  "job_id": "PRINT-....",
  "completion_status": "completed"
}
```

Abort payload:

```json
{
  "device_connection_id": "CONN-....",
  "job_id": "PRINT-....",
  "completion_status": "aborted"
}
```

Aborted jobs transfer no simulated funds.

## Author acknowledgement

```http
POST /api/jobs/acknowledge
Content-Type: application/json

{
  "author_connection_id": "CONN-....",
  "job_id": "PRINT-...."
}
```

A job pays only once, after one-time start authorization, device completion, and author acknowledgement.
