# Windows Device Helper

Purpose: expose the Windows hostname to POSNext / POS Awesome at:

```text
http://127.0.0.1:8765/device
```

Example response:

```json
{
  "computer_name": "SAL-POS-01",
  "service": "erpnext-payment-hub-device-helper",
  "version": "0.1.4"
}
```

## Manual test

```powershell
python .\payment_hub_device_helper.py
```

Then open:

```text
http://127.0.0.1:8765/device
```

## Install at Windows startup

Run PowerShell as Administrator:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows_service.ps1
```

The helper binds only to `127.0.0.1`, so it is not exposed to other computers on the LAN.
