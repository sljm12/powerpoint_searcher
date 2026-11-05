# EX1 open acceptance issues

These issues prevent an unconditional EX1 completion claim. They do not authorize EX2.

## EX1-SEC-PARSER. Source package parsing runs in the service process

The upload boundary enforces byte and archive limits, rejects external relationships, active content, unsafe members, and XML DTDs, and validates the OOXML package type. However, `python-pptx` source validation and inventory creation run in the service process. Only LibreOffice conversion runs in the Linux OS sandbox. These checks reduce input exposure but do not protect the service from a parser vulnerability or every adversarial parser resource pattern.

Do not describe the whole upload pipeline as sandboxed. Closure requires moving untrusted source parsing into the confined worker and checking cancellation, resource limits, and deletion behavior there.

## EX1-SEC-WIN. Native Windows confined UNO does not start

Zero-capability AppContainer probes establish private-file denial, blocked loopback and external networking, private-folder writes, UNO import, and LibreOffice startup. Real conversion cannot connect to the private UNO pipe. LibreOffice prefixes Windows pipe names with `OSL_PIPE_`; AppContainer permits its local pipe namespace. An attempted in-process Desktop context crashed with access violation `0xC0000005`.

Keep the production native Windows renderer unavailable. Do not add network capabilities, loopback exemptions, or an unconfined fallback. The Linux service can run in an explicitly prepared WSL distribution on a Windows host. That deployment remains a Linux renderer and does not establish native Windows confinement.

Closure requires an actual restricted Windows conversion, no-egress and private-storage probes, descendant cancellation and cleanup, and retained PNGs from the same fixtures.

## EX1-FIDELITY-SMARTART. PowerPoint reference is unavailable

The genuine SmartArt fixture preserves its source identity, chevrons, text, and isolated export on both operating systems. Its white labels sit against the left chevron edges. An original PowerPoint rendering is unavailable, so close visual fidelity is provisional. Machine mapping and retained text cannot establish the reference appearance.

See the [fixture provenance](fixtures/README.md) and [renderer evidence](evidence/README.md). Closure requires a trusted reference image and visual acceptance or a renderer correction with repeated coverage checks.

## EX1-SOURCE-FIELDS. Effective inherited dynamic fields are unsupported

Enabled inherited footer, date, and slide-number placeholders appear in the source inventory. Extraction fails the required slide explicitly while effective field resolution is unsupported. It never reports those fields as successfully exported or silently omits them. Static master content is covered by the representative fixture.

Closure requires field-resolution fixtures and actual exports with matching source values on both renderer versions.

## EX1-RESOURCE-DISK. Polling caps are not filesystem quotas

Input, image, output-file, and manifest limits bound accepted service results. Renderer work-directory polling and timeout termination are operational controls. They do not provide an exact filesystem quota or eliminate the possibility of overshoot between checks. Use a dedicated filesystem with a quota when a hard disk ceiling is required.

Linux additionally requires bubblewrap user/network namespaces and a systemd user manager with delegated cgroup v2 memory and process controllers. The launcher verifies its effective limits and fails closed when they are unavailable.
