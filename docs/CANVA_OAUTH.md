# Optional Canva connection

Public-link capture works without integration credentials. To access private fixed-size designs through official exports, configure a Canva Connect integration.

1. Create an integration in the Canva developer portal.
2. Register the exact callback `http://127.0.0.1:3000/api/canva/oauth/callback` for the default local configuration.
3. Put CANVA_CLIENT_ID, CANVA_CLIENT_SECRET, and CANVA_REDIRECT_URI in the backend environment.
4. Restart the backend and choose **Connect Canva** in the plugin.
5. Complete authorization in the browser, return to Figma, select a design, and load its pages.

The implementation requests design:meta:read and design:content:read. Authorization uses PKCE/SHA-256, a random one-time state, and a ten-minute state lifetime. User tokens remain in backend memory and refresh is serialized to accommodate rotating refresh tokens.

Design metadata and official per-page PNG exports enter the ordinary preview, selection, and page-image import workflow. The local implementation limits OAuth designs to 500 pages.

Disconnect clears local tokens and attempts remote revocation. Restart requires reconnecting. The connection uses a local single-user integration; access depends on the configured credentials and the connected account's permissions.

The plugin receives an authorization URL, including its public state parameter, but does not receive the client secret, PKCE verifier, access token, or refresh token.
