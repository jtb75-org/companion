/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base URL for the public knowledge endpoint. Empty/unset → same-origin
   * relative fetch (`/public/knowledge/ask`). Set to an absolute origin to point
   * the widget at a cross-origin API host.
   */
  readonly VITE_KNOWLEDGE_API_BASE?: string;
  /**
   * Force the offline MockKnowledgeApi anywhere (dev/offline/tests) when set to
   * the string "true". Redundant explicit force-mock — the mock is already the
   * default everywhere (see knowledgeApi.ts).
   */
  readonly VITE_KNOWLEDGE_USE_MOCK?: string;
  /**
   * THE launch switch: set to the string "true" to opt IN to the real
   * same-origin HttpKnowledgeApi (`/public/knowledge/ask`). Unset/anything else →
   * the fail-safe MockKnowledgeApi, INCLUDING in production. Flip this only once
   * the public endpoint (backend #151) + Cloudflare edge protection are live.
   */
  readonly VITE_KNOWLEDGE_ENABLE_HTTP?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
