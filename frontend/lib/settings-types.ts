/** 可写回 .env 的配置项（对应后端 SettingsUpdate）。 */
export interface SettingsPayload {
  llm_provider: string;
  llm_model: string;
  llm_base_url: string;
  llm_has_key: boolean;
  search_provider: string;
  searxng_base_url: string;
  search_has_key: boolean;
  default_detail_level: string;
  env_path: string;
}

export interface SettingsUpdate {
  llm_provider?: string;
  llm_model?: string;
  llm_api_key?: string;
  llm_base_url?: string;
  search_provider?: string;
  search_api_key?: string;
  searxng_base_url?: string;
  default_detail_level?: string;
}

export interface SettingsSaveResult {
  saved: boolean;
  changed: string[];
  message: string;
}
