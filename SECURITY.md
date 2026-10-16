# Security

Report suspected vulnerabilities privately to contact@axiocairn.com. Include the affected version, a minimal reproduction using synthetic data, and the observed impact. Do not send credentials, real personal data, or database contents.

This release processes trusted local SQLite stores and binds its optional HTTP service to loopback. It provides no authentication or TLS and must not be exposed to untrusted callers. A matching receipt does not authenticate its issuer or verify a business outcome. Stop using affected operations while evidence remains ambiguous.

Direct HTTP clients allow only the literal `http://127.0.0.1:PORT` development host, disable proxies and reject redirects. This is a local-service exception; other private, link-local, metadata and DNS destinations are rejected before connecting. JSON and receipt metadata are limited to 65,536 UTF-8 bytes, receipt queries to 1,000 rows and 1 MiB total encoded data, and file objects to 16 MiB. Exceeding a bound returns an explicit error or a stopped MANUAL_REVIEW result, never retry clearance. These controls do not authenticate a trusted local caller or provide tenant isolation.
