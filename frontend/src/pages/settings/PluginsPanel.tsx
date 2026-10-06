/**
 * The plugins tab: what is in `plugin/`, what is on, and how each is
 * configured.
 *
 * The switch is the plugin system's whole point: it flips a settings row, the
 * backend's gate starts answering 409 on the plugin's API, and the shell's
 * registry hears the change and drops the nav item and the popup the same
 * moment. Config drafts per card - a plugin with several settings should not
 * have to save one at a time - and the hotkey field records whatever combo is
 * pressed while it is focused, using the same parser the popup listens with.
 *
 * 「重新扫描」 picks up folders that appeared after startup. It is here
 * rather than automatic because importing new Python is a restart-shaped act:
 * the scan is additive, and code changes to a loaded plugin still take a
 * backend restart, which the card says rather than pretending.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../api/client'
import {
  listPlugins,
  reloadPlugins,
  setPluginConfig,
  setPluginEnabled,
  type PluginInfo,
  type PluginSettingField,
} from '../../api/plugins'
import { RefreshIcon } from '../../components/icons'
import { useToast } from '../../hooks/useToast'
import { describeEvent, formatHotkey, parseHotkey } from '../../plugins/hotkey'
import { notifyPluginsChanged } from '../../plugins/registry'

export function PluginsPanel() {
  const toast = useToast()
  const [plugins, setPlugins] = useState<PluginInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)

  const reload = useCallback(() => {
    return listPlugins()
      .then((installed) => {
        setPlugins(installed)
        setLoadError(null)
      })
      .catch((cause: unknown) => setLoadError(describe(cause)))
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function toggle(plugin: PluginInfo, enabled: boolean) {
    setError(null)
    try {
      const next = await setPluginEnabled(plugin.id, enabled)
      setPlugins((current) => current?.map((p) => (p.id === next.id ? next : p)) ?? current)
      notifyPluginsChanged()
    } catch (cause) {
      setError(describe(cause))
    }
  }

  async function rescan() {
    setScanning(true)
    setError(null)
    try {
      const result = await reloadPlugins()
      setPlugins(result.plugins)
      setLoadError(null)
      notifyPluginsChanged()
      toast.show(
        result.added.length > 0
          ? `新载入 ${result.added.length} 个插件`
          : '没有发现新插件',
      )
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setScanning(false)
    }
  }

  return (
    <section>
      <div className="field">
        <label>插件</label>
        <span className="hint">
          项目根目录 plugin/ 下的每个文件夹是一个插件：后端提供 API，前端提供页面。
          开关即时生效；改了已载入插件的代码需要重启后端。
        </span>
        <div className="plugin-actions">
          <button
            type="button"
            className="button button-ghost"
            disabled={scanning}
            onClick={() => void rescan()}
          >
            <RefreshIcon size={15} />
            {scanning ? '扫描中…' : '重新扫描'}
          </button>
        </div>
      </div>

      {loadError !== null && <div className="form-error">{loadError}</div>}
      {error !== null && <div className="form-error">{error}</div>}
      {plugins === null ? null : plugins.length === 0 ? (
        <p className="setup-empty">plugin/ 目录里还没有插件。</p>
      ) : (
        <ul className="plugin-list">
          {plugins.map((plugin) => (
            <PluginCard key={plugin.id} plugin={plugin} onToggle={toggle} onSaved={reload} />
          ))}
        </ul>
      )}
    </section>
  )
}

function PluginCard({
  plugin,
  onToggle,
  onSaved,
}: {
  plugin: PluginInfo
  onToggle(plugin: PluginInfo, enabled: boolean): Promise<void>
  onSaved(): Promise<void> | void
}) {
  const toast = useToast()
  const [draft, setDraft] = useState(plugin.config)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setDraft(plugin.config)
  }, [plugin.config])

  const dirty =
    plugin.settings_schema.length > 0 &&
    plugin.settings_schema.some((field) => draft[field.key] !== plugin.config[field.key])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const next = await setPluginConfig(plugin.id, draft)
      setDraft(next.config)
      await onSaved()
      notifyPluginsChanged()
      toast.show('配置已保存')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <li className={`plugin-card${plugin.broken ? ' is-broken' : ''}`}>
      <div className="plugin-head">
        <span className="plugin-name">{plugin.name}</span>
        {plugin.version !== null && <span className="plugin-version">v{plugin.version}</span>}
        {!plugin.loaded && <span className="plugin-stale">待重新扫描载入</span>}
        <span className="spacer" />
        {plugin.broken ? null : (
          <button
            type="button"
            role="switch"
            aria-checked={plugin.enabled}
            aria-label={plugin.enabled ? `停用 ${plugin.name}` : `启用 ${plugin.name}`}
            className={`skill-switch${plugin.enabled ? ' is-on' : ''}`}
            title={plugin.enabled ? '已启用' : '已停用'}
            onClick={() => void onToggle(plugin, !plugin.enabled)}
          >
            <span className="skill-knob" />
          </button>
        )}
      </div>

      {plugin.broken ? (
        <span className="skill-error">{plugin.error ?? '插件加载失败。'}</span>
      ) : (
        <>
          {plugin.description !== null && (
            <p className="plugin-description">{plugin.description}</p>
          )}
          {plugin.settings_schema.map((field) => (
            <PluginField
              key={field.key}
              field={field}
              value={draft[field.key]}
              onChange={(value) => setDraft((current) => ({ ...current, [field.key]: value }))}
            />
          ))}
          {error !== null && <div className="form-error">{error}</div>}
          {dirty && (
            <div className="plugin-save">
              <button
                type="button"
                className="button button-primary button-small"
                disabled={saving}
                onClick={() => void save()}
              >
                {saving ? '保存中…' : '保存配置'}
              </button>
            </div>
          )}
        </>
      )}
    </li>
  )
}

function PluginField({
  field,
  value,
  onChange,
}: {
  field: PluginSettingField
  value: string | number | boolean | undefined
  onChange(value: string | number | boolean): void
}) {
  if (field.type === 'bool') {
    const on = value === true
    return (
      <div className="field plugin-field">
        <label>{field.label}</label>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label={on ? `关闭 ${field.label}` : `开启 ${field.label}`}
          className={`skill-switch${on ? ' is-on' : ''}`}
          onClick={() => onChange(!on)}
        >
          <span className="skill-knob" />
        </button>
      </div>
    )
  }
  if (field.type === 'hotkey') {
    const current = typeof value === 'string' ? value : String(field.default)
    const combo = parseHotkey(current)
    return (
      <div className="field plugin-field">
        <label htmlFor={`plugin-${field.key}`}>{field.label}</label>
        <input
          id={`plugin-${field.key}`}
          className="plugin-hotkey"
          type="text"
          readOnly
          value={combo === null ? current : formatHotkey(combo)}
          placeholder="点击后按下组合键"
          onKeyDown={(event) => {
            // Recording swallows the key: the browser's own Ctrl+N must not
            // fire just because the field was focused when it was pressed.
            event.preventDefault()
            const recorded = describeEvent(event.nativeEvent)
            if (recorded !== null) {
              onChange(recorded)
            }
          }}
        />
        <span className="hint">点击框后按下组合键，Esc 取消；字母数字要带修饰键，F1–F12 可单独使用。</span>
      </div>
    )
  }
  return (
    <div className="field plugin-field">
      <label htmlFor={`plugin-${field.key}`}>{field.label}</label>
      <input
        id={`plugin-${field.key}`}
        type={field.type === 'int' ? 'number' : 'text'}
        value={value === undefined ? String(field.default) : String(value)}
        onChange={(event) => {
          if (field.type === 'int') {
            const parsed = Number.parseInt(event.target.value, 10)
            onChange(Number.isNaN(parsed) ? '' : parsed)
          } else {
            onChange(event.target.value)
          }
        }}
      />
    </div>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}
