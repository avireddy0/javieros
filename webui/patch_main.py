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

# --- Fix OAuth callback 401 bug ---
# The /oauth/clients/{client_id}/callback endpoint requires Bearer token auth
# via get_verified_user, but browser redirects from Google don't carry the token.
# Fix: store user_id in session during /authorize, recover it in /callback.

# 1. Store user.id in session before authorize redirect
authorize_return = "    return await oauth_client_manager.handle_authorize(request, client_id=client_id)"
if authorize_return in main_text:
    main_text = main_text.replace(
        authorize_return,
        '    request.session["oauth_user_id"] = user.id\n' + authorize_return,
    )

# 2. Replace callback to use session instead of Bearer token
old_callback = """@app.get("/oauth/clients/{client_id}/callback")
async def oauth_client_callback(
    client_id: str,
    request: Request,
    response: Response,
    user=Depends(get_verified_user),
):
    return await oauth_client_manager.handle_callback(
        request,
        client_id=client_id,
        user_id=user.id if user else None,
        response=response,
    )"""

new_callback = """@app.get("/oauth/clients/{client_id}/callback")
async def oauth_client_callback(
    client_id: str,
    request: Request,
    response: Response,
):
    user_id = request.session.get("oauth_user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OAuth session expired or not found. Please retry the authorization flow.",
        )
    response_out = await oauth_client_manager.handle_callback(
        request,
        client_id=client_id,
        user_id=user_id,
        response=response,
    )
    request.session.pop("oauth_user_id", None)
    return response_out"""

if old_callback in main_text:
    main_text = main_text.replace(old_callback, new_callback)
    print("OK: Patched OAuth callback to use session-based auth")
else:
    print("WARN: Could not find OAuth callback pattern to patch")

main_path.write_text(main_text)
