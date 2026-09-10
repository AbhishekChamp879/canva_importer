# Security and privacy

The service is intended for a single local user and binds only to loopback through its supplied entry point. It has no API authentication or tenant isolation. CORS permits Figma HTTPS origins, loopback HTTP origins, and null/missing origins; CORS is not authentication.

Sensitive data includes Canva share URLs, OAuth credentials/tokens, page images, and local capture metadata. The application stores capture files without application-level encryption. Error messages are bounded and redact common credentials and Canva share tokens.

The public URL boundary accepts constrained HTTPS Canva hosts and routes. Short-link resolution validates each redirect. OAuth export downloads validate trusted hosts, redirects, MIME types, image content, and size limits. Public Open Graph fallback checks the final response URL and loaded body size; it is not a streaming download.

Canva content executes in an isolated browser context. The current launch configuration includes --no-sandbox, which weakens browser isolation. Public hosting would require a different security architecture.

The plugin uses DOM creation and textContent for untrusted text. Its development network policy allows only the local backend. Each image-import session validates metadata, limits images to 25 MiB, and rolls back created frames on cancellation or fatal errors.

OAuth uses PKCE and one-time state. User tokens remain in memory and are cleared on disconnect or restart. The client secret remains in backend configuration.

Captures have a default one-hour TTL, enforced while the service runs or accesses storage. Files are not deleted while the service is stopped. Stop the backend before manually removing any artifact directory, and verify the exact path first. Imported Figma frames persist independently of local capture expiration.

Before public hosting, add authentication, authorization, tenant-scoped storage, encryption, quotas, production deployment, monitoring, and browser isolation.
