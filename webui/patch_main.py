"""Patch Open WebUI main.py to register custom routers."""
from pathlib import Path

index_path = Path("/app/build/index.html")
index_text = index_path.read_text()
if "/static/custom.js" not in index_text:
    index_text = index_text.replace(
        "</body>",
        '  <script defer src="/static/custom.js"></script>\n</body>',
    )
    index_path.write_text(index_text)

main_path = Path("/app/backend/open_webui/main.py")
main_text = main_path.read_text()
if "whatsapp_qr" not in main_text:
    main_text = main_text.replace(
        "    scim,\n)",
        "    scim,\n    whatsapp_qr,\n)",
    )

if "ide_hook" not in main_text:
    if "whatsapp_qr" in main_text:
        main_text = main_text.replace(
            "    whatsapp_qr,\n)",
            "    whatsapp_qr,\n    ide_hook,\n)",
        )
    else:
        main_text = main_text.replace(
            "    scim,\n)",
            "    scim,\n    ide_hook,\n)",
        )

if "cron_proxy" not in main_text:
    main_text = main_text.replace(
        "    ide_hook,\n)",
        "    ide_hook,\n    cron_proxy,\n)",
    )

marker = 'app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])'
if marker in main_text and "whatsapp_qr.router" not in main_text:
    main_text = main_text.replace(
        marker,
        marker
        + '\napp.include_router(whatsapp_qr.router, prefix="/api/v1/whatsapp", tags=["whatsapp"])',
    )

if "ide_hook.router" not in main_text:
    main_text = main_text.replace(
        marker,
        marker + '\napp.include_router(ide_hook.router, tags=["ide"])',
    )

if "cron_proxy.router" not in main_text:
    main_text = main_text.replace(
        marker,
        marker + '\napp.include_router(cron_proxy.router, prefix="/api/cron", tags=["cron"])',
    )

main_path.write_text(main_text)
