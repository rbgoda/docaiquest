// Type declarations for @docaiquest/sdk.

export interface DocaiquestClientOptions {
  /** Owner-scoped API key, e.g. `dq_live_...`. */
  apiKey: string;
  /** API base URL. Defaults to https://docaiq.jicama.tech */
  baseUrl?: string;
}

export interface Citation {
  docId: string;
  name: string;
  page: number;
  quote: string;
}

export interface AskResult {
  answer: string;
  grounded: boolean;
  confidence?: number;
  citations: Citation[];
}

export interface DocumentSummary {
  id: string;
  name: string;
  type: string;
  createdAt: string;
}

export interface ExtractResult {
  status: string;
  docType: string;
  fields: Record<string, unknown>;
  citations: Citation[];
  confidence?: number;
}

export declare class DocaiquestClient {
  constructor(options: DocaiquestClientOptions);

  /** Ask a grounded question across the owner's documents. */
  ask(
    question: string,
    opts?: { topK?: number }
  ): Promise<{ answer: string; grounded: boolean; citations: any[] } & Partial<AskResult>>;

  /** List the owner's documents, returning the `documents` array. */
  documents(opts?: { limit?: number }): Promise<DocumentSummary[]>;

  /** Extract structured fields from a single document file. */
  extract(file: Blob | File, filename?: string): Promise<ExtractResult>;
}

export default DocaiquestClient;
