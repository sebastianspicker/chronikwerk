/** Shared types for the admin UI modules. */

export type ConfigControl = HTMLInputElement | HTMLSelectElement;
export type ConfigForm = HTMLFormElement & {
  elements: HTMLFormControlsCollection & {security_acknowledged: HTMLInputElement};
};
export type ConfigValue = string | number | boolean;
export type ConfigValues = Record<string, ConfigValue>;

export interface StatusResponse {
  admission: {
    running: string | number;
    pending: string | number;
    max_running?: number;
    max_pending?: number;
  };
}
export interface StorageCheckResponse { storage?: {writable?: boolean}; }
export interface ConfigError { path: string; message: string; }
export interface ConfigDiff { path: string; before: unknown; after: unknown; }
export interface ConfigValidationResponse {
  errors?: ConfigError[];
  message?: string;
  overlay?: unknown;
  diff?: ConfigDiff[];
}
export interface ConfigStageResponse { revision?: string; message?: string; }
export interface ConfigDraft { values: ConfigValues; securityAcknowledged: boolean; }
export interface OverviewElements {
  status: HTMLElement;
  running: HTMLElement;
  pending: HTMLElement;
  refreshed: HTMLTimeElement;
}
