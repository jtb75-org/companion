import { useState } from 'react'
import { API_BASE, getAuthHeader } from '../api/client'

export interface ImageAnalysis {
  status: 'good' | 'poor' | 'error'
  feedback: string
  has_text: boolean
  brightness: number
}

export function useImageAnalysis() {
  const [analyzing, setAnalyzing] = useState(false)

  const analyzeImage = async (uri: string): Promise<ImageAnalysis | null> => {
    setAnalyzing(true)
    try {
      const authHeader = await getAuthHeader()
      if (!authHeader.Authorization) return null

      const formData = new FormData()
      formData.append('file', {
        uri,
        type: 'image/jpeg',
        name: 'frame.jpg',
      } as any)

      const response = await fetch(`${API_BASE}/api/v1/documents/scan/analyze`, {
        method: 'POST',
        // No Content-Type — let fetch set the multipart/form-data boundary.
        headers: authHeader,
        body: formData,
      })

      if (!response.ok) return null
      
      const result = await response.json()
      return result as ImageAnalysis
    } catch (err) {
      console.log('[useImageAnalysis] error:', err)
      return null
    } finally {
      setAnalyzing(false)
    }
  }

  return { analyzeImage, analyzing }
}
