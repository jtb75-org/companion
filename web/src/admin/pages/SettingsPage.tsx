import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../shared/api/client'
import { Card } from '../../shared/components/Card'

interface DeletionSettings {
  grace_period_days: number
}

const defaults: DeletionSettings = {
  grace_period_days: 30,
}

// OCR engine feature flag. Decides whether document PHI is processed locally
// (paddleocr) or sent off-cluster to Google (documentai). Gated to 'admin' role
// server-side. Default mirrors the env fallback (documentai) when unset.
const OCR_PROVIDERS = ['documentai', 'paddleocr'] as const
type OcrProvider = (typeof OCR_PROVIDERS)[number]
const OCR_DEFAULT: OcrProvider = 'documentai'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [settings, setSettings] = useState<DeletionSettings>(defaults)
  const [configId, setConfigId] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)

  const [ocrProvider, setOcrProvider] = useState<OcrProvider>(OCR_DEFAULT)
  const [ocrConfigId, setOcrConfigId] = useState<string | null>(null)
  const [ocrStatus, setOcrStatus] = useState<string | null>(null)
  const [ocrDirty, setOcrDirty] = useState(false)

  useQuery({
    queryKey: ['config-deletion-settings'],
    queryFn: async () => {
      try {
        const data = await api<{ entries: { id: string; key: string; value: unknown; category: string }[] }>(
          '/admin/config'
        )
        const ocrMatch = data.entries.find(
          (e) => e.category.toLowerCase() === 'feature_flag' && e.key === 'ocr_primary_provider'
        )
        if (ocrMatch) {
          setOcrConfigId(ocrMatch.id)
          const p = (ocrMatch.value as { provider?: string }).provider
          if (p === 'documentai' || p === 'paddleocr') setOcrProvider(p)
        }

        const match = data.entries.find(
          (e) => e.category.toLowerCase() === 'deletion_settings' && e.key === 'grace_period_days'
        )
        if (match) {
          setConfigId(match.id)
          const val = match.value as { days: number }
          setSettings({ grace_period_days: val.days ?? 30 })
          return val
        }
        return defaults
      } catch {
        return defaults
      }
    },
  })

  const mutation = useMutation({
    mutationFn: async (vals: DeletionSettings) => {
      const payload = { days: vals.grace_period_days }
      if (configId) {
        await api(`/admin/config/${configId}`, {
          method: 'PATCH',
          body: JSON.stringify({ value: payload, reason: 'Updated via admin settings' }),
        })
      } else {
        await api('/admin/config', {
          method: 'POST',
          body: JSON.stringify({
            category: 'deletion_settings',
            key: 'grace_period_days',
            value: payload,
            description: 'Account deletion grace period in days (0 = immediate)',
          }),
        })
      }
    },
    onSuccess: () => {
      setSaveStatus('Saved successfully')
      setDirty(false)
      queryClient.invalidateQueries({ queryKey: ['config-deletion-settings'] })
      setTimeout(() => setSaveStatus(null), 3000)
    },
    onError: () => {
      setSaveStatus('Failed to save')
      setTimeout(() => setSaveStatus(null), 3000)
    },
  })

  const ocrMutation = useMutation({
    mutationFn: async (provider: OcrProvider) => {
      const payload = { provider }
      if (ocrConfigId) {
        await api(`/admin/config/${ocrConfigId}`, {
          method: 'PATCH',
          body: JSON.stringify({ value: payload, reason: 'OCR engine changed via admin settings' }),
        })
      } else {
        await api('/admin/config', {
          method: 'POST',
          body: JSON.stringify({
            category: 'feature_flag',
            key: 'ocr_primary_provider',
            value: payload,
            description: 'Primary OCR engine for document ingestion',
          }),
        })
      }
    },
    onSuccess: () => {
      setOcrStatus('Saved successfully')
      setOcrDirty(false)
      queryClient.invalidateQueries({ queryKey: ['config-deletion-settings'] })
      setTimeout(() => setOcrStatus(null), 3000)
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error && err.message ? err.message : 'Failed to save'
      setOcrStatus(msg.includes('403') ? "Failed: requires 'admin' role" : 'Failed to save')
      setTimeout(() => setOcrStatus(null), 4000)
    },
  })

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-900">Settings</h1>

      <Card title="OCR Engine" subtitle="Primary engine used to extract text from uploaded documents">
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <label className="text-sm text-gray-700 w-48">Primary provider</label>
            <select
              value={ocrProvider}
              onChange={(e) => {
                setOcrProvider(e.target.value as OcrProvider)
                setOcrDirty(true)
              }}
              className="w-56 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-companion-blue-light focus:outline-none focus:ring-1 focus:ring-companion-blue-light"
            >
              {OCR_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p === 'paddleocr' ? 'PaddleOCR (self-hosted, local)' : 'Google Document AI (cloud)'}
                </option>
              ))}
            </select>
          </div>
          {ocrProvider === 'documentai' && (
            <p className="text-xs text-amber-600 font-medium">
              Document AI sends document contents to Google for processing. Use PaddleOCR to keep all document data on-cluster.
            </p>
          )}
          <p className="text-xs text-gray-500">
            Changing the OCR engine requires the <strong>admin</strong> role. Takes effect for newly ingested documents.
          </p>
          <div className="flex items-center gap-4">
            <button
              onClick={() => ocrMutation.mutate(ocrProvider)}
              disabled={ocrMutation.isPending || !ocrDirty}
              className="px-4 py-2 bg-companion-blue text-white rounded-lg text-sm font-medium hover:bg-companion-blue-mid disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {ocrMutation.isPending ? 'Saving...' : 'Save OCR Engine'}
            </button>
            {ocrStatus && (
              <p className={`text-sm ${ocrStatus.includes('Failed') ? 'text-red-600' : 'text-green-600'}`}>
                {ocrStatus}
              </p>
            )}
          </div>
        </div>
      </Card>

      <Card title="Account Deletion" subtitle="Configure the grace period before permanent deletion">
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <label className="text-sm text-gray-700 w-48">Grace period (days)</label>
            <input
              type="number"
              min="0"
              max="365"
              value={settings.grace_period_days}
              onChange={(e) => {
                setSettings({ grace_period_days: parseInt(e.target.value) || 0 })
                setDirty(true)
              }}
              className="w-24 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-companion-blue-light focus:outline-none focus:ring-1 focus:ring-companion-blue-light"
            />
          </div>
          <p className="text-xs text-gray-500">
            Set to <strong>0</strong> for immediate deletion (no grace period).
            Default is <strong>30 days</strong>.
          </p>
          {settings.grace_period_days === 0 && (
            <p className="text-xs text-amber-600 font-medium">
              Warning: With 0 days, account deletion requests will be executed immediately. All data will be permanently removed with no recovery window.
            </p>
          )}
        </div>
      </Card>

      <div className="flex items-center justify-between">
        <div>
          {saveStatus && (
            <p className={`text-sm ${saveStatus.includes('Failed') ? 'text-red-600' : 'text-green-600'}`}>
              {saveStatus}
            </p>
          )}
        </div>
        <button
          onClick={() => mutation.mutate(settings)}
          disabled={mutation.isPending || !dirty}
          className="px-4 py-2 bg-companion-blue text-white rounded-lg text-sm font-medium hover:bg-companion-blue-mid disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {mutation.isPending ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}
