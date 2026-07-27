import { useEffect, useRef } from 'react';

/**
 * The Automation Hub section.
 *
 * loadAutomations renders this between System and the user groups, and it is
 * pure informational content — pipelines, recipes, guides, reference and tips —
 * built by _buildAutomationHub in stats-automations.js.
 *
 * That builder is SHARED with the video automations page, so its content is
 * mounted here rather than restated in JSX. Re-authoring it would fork a body
 * of copy that has to stay identical on both sides, which is precisely the
 * drift the icon parity test exists to prevent.
 *
 * React owns an empty host node and hands the subtree to the builder. Nothing
 * inside is React-managed, so the builder's own collapse handler and
 * localStorage state keep working untouched.
 */
export function AutomationHub() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = host.current;
    if (!mount || typeof window._buildAutomationHub !== 'function') return;

    const section = window._buildAutomationHub();
    mount.appendChild(section);
    // Strict mode mounts effects twice; without this the hub appears twice.
    return () => {
      section.remove();
    };
  }, []);

  return <div ref={host} data-automation-hub="" />;
}
