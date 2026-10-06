/**
 * The plugin host's wire shapes and the calls the settings screen and the
 * shell make: list, switch, configure, rescan.
 */

import { api } from './client'

/** One configurable field, mirrored from the plugin's SETTINGS_SCHEMA. */
export interface PluginSettingField {
  key: string
  label: string
  type: 'hotkey' | 'bool' | 'int' | 'text'
  default: string | number | boolean
}

export interface PluginInfo {
  id: string
  name: string
  description: string | null
  version: string | null
  settings_schema: PluginSettingField[]
  config: Record<string, string | number | boolean>
  enabled: boolean
  broken: boolean
  error: string | null
  /**
   * False for a folder that appeared after the app loaded its plugins: one
   * rescan brings it in, and until then the card says so instead of listing
   * the folder as if it were running.
   */
  loaded: boolean
}

export function listPlugins(): Promise<PluginInfo[]> {
  return api<PluginInfo[]>('/api/plugins')
}

export function setPluginEnabled(pluginId: string, enabled: boolean): Promise<PluginInfo> {
  return api<PluginInfo>(`/api/plugins/${pluginId}/enabled`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  })
}

export function setPluginConfig(
  pluginId: string,
  settings: Record<string, string | number | boolean>,
): Promise<PluginInfo> {
  return api<PluginInfo>(`/api/plugins/${pluginId}/config`, {
    method: 'PUT',
    body: JSON.stringify({ settings }),
  })
}

export interface ReloadResult {
  added: string[]
  plugins: PluginInfo[]
}

export function reloadPlugins(): Promise<ReloadResult> {
  return api<ReloadResult>('/api/plugins/reload', { method: 'POST' })
}
