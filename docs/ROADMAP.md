# Page importer roadmap

Priorities for the retained page-image workflow:

1. Expand owned live examples across aspect ratios, document lengths, viewer modes, and duplicated pages.
2. Verify every captured image against its intended Canva page and confirm final Figma dimensions.
3. Test cancellation, worker capacity, browser crashes, disk-full failures, and process restart.
4. Bound aggregate memory and disk usage across cached captures.
5. Make backend/plugin endpoint configuration consistent.
6. Validate OAuth exports with a real configured integration and improve design-list pagination.
7. Add reproducible dependency management and automated deterministic checks.

A hosted version would additionally require authentication, tenant isolation, a durable queue, managed storage, quotas, production browser isolation, and observability.

Future work should preserve the simple output contract: one image and one correctly sized Figma frame per selected page.
