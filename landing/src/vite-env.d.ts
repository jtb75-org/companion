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
   * the string "true". Unset → the client is chosen by base/prod (see
   * knowledgeApi.ts).
   */
  readonly VITE_KNOWLEDGE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
