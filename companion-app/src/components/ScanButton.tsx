import React, { useState } from 'react'
import {
  TouchableOpacity,
  StyleSheet,
  ActionSheetIOS,
  Platform,
  Alert,
  Text,
  View,
  ActivityIndicator,
  NativeModules,
} from 'react-native'
import { launchImageLibrary, Asset } from 'react-native-image-picker'
import auth from '@react-native-firebase/auth'
import { API_BASE } from '../api/client'
import { colors } from '../theme/colors'

const { DocumentScannerModule } = NativeModules

type UploadStatus = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

export function ScanButton() {
  const [status, setStatus] = useState<UploadStatus>('idle')

  const handlePress = () => {
    if (Platform.OS === 'ios') {
      ActionSheetIOS.showActionSheetWithOptions(
        {
          options: ['Cancel', 'Scan Document', 'Choose from Library'],
          cancelButtonIndex: 0,
        },
        (buttonIndex) => {
          if (buttonIndex === 1) scanWithVisionKit()
          else if (buttonIndex === 2) pickFromLibrary()
        },
      )
    } else {
      Alert.alert('Scan Document', 'Choose an option', [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Scan Document', onPress: scanWithVisionKit },
        { text: 'Choose from Library', onPress: pickFromLibrary },
      ])
    }
  }

  const scanWithVisionKit = async () => {
    try {
      const uris: string[] = await DocumentScannerModule.scanDocument()
      if (!uris || uris.length === 0) return

      const assets: Asset[] = uris.map((uri, i) => ({
        uri,
        type: 'image/jpeg',
        fileName: `scan_page_${i}.jpg`,
      }))
      uploadImages(assets)
    } catch (err: any) {
      if (err?.code !== 'CANCELLED') {
        Alert.alert('Error', 'Failed to scan document')
      }
    }
  }

  const pickFromLibrary = async () => {
    try {
      const result = await launchImageLibrary({
        mediaType: 'photo',
        quality: 0.8,
        maxWidth: 2048,
        maxHeight: 2048,
        selectionLimit: 0,
      })
      if (result.didCancel) return
      if (result.errorCode) {
        if (result.errorCode === 'permission') {
          Alert.alert(
            'Permission Required',
            'Please allow photo library access in Settings.',
          )
        } else {
          Alert.alert(
            'Error',
            result.errorMessage || 'Could not open photo library',
          )
        }
        return
      }
      const assets = result.assets
      if (assets && assets.length > 0) uploadImages(assets)
    } catch (err: any) {
      console.log('[ScanButton] library error:', err)
      Alert.alert('Error', err.message || 'Failed to open photo library')
    }
  }

  const uploadImages = async (assets: Asset[]) => {
    if (assets.length === 0) return

    setStatus('uploading')

    try {
      const user = auth().currentUser
      if (!user) {
        Alert.alert('Error', 'You must be signed in to scan documents')
        setStatus('idle')
        return
      }

      const token = await user.getIdToken()

      const formData = new FormData()
      for (const asset of assets) {
        if (!asset.uri) continue
        formData.append('files', {
          uri: asset.uri,
          type: asset.type || 'image/jpeg',
          name: asset.fileName || 'scan.jpg',
        } as any)
      }

      const response = await fetch(`${API_BASE}/api/v1/documents/scan`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      })

      if (!response.ok) {
        throw new Error(`Upload failed: ${response.status}`)
      }

      const body = await response.json().catch(() => ({} as any))

      // Exact duplicate: the member re-sent a file they already have (e.g. they
      // thought the first try didn't work). Reassure them — nothing went wrong,
      // and we didn't add a second copy.
      if (body?.duplicate === true) {
        setStatus('done')
        Alert.alert(
          'You already have this',
          "No need to add it again — it's already saved.",
        )
        setTimeout(() => setStatus('idle'), 2000)
        return
      }

      setStatus('processing')

      const pageLabel = assets.length > 1 ? `${assets.length}-page document` : 'Document'
      setTimeout(() => {
        setStatus('done')
        Alert.alert('Success', `${pageLabel} uploaded! We'll notify you when it's processed.`)
        setTimeout(() => setStatus('idle'), 2000)
      }, 1000)
    } catch (err) {
      setStatus('error')
      Alert.alert('Upload Failed', 'Could not upload the document. Please try again.')
      setTimeout(() => setStatus('idle'), 2000)
    }
  }

  const statusLabel: Record<UploadStatus, string> = {
    idle: '',
    uploading: 'Uploading...',
    processing: 'Processing...',
    done: 'Done!',
    error: 'Failed',
  }

  return (
    <View style={styles.wrapper} pointerEvents="box-none">
      {status !== 'idle' && (
        <View style={styles.statusBadge}>
          {(status === 'uploading' || status === 'processing') && (
            <ActivityIndicator size="small" color={colors.white} style={{ marginRight: 6 }} />
          )}
          <Text style={styles.statusText}>{statusLabel[status]}</Text>
        </View>
      )}
      <TouchableOpacity
        style={[styles.fab, status !== 'idle' && status !== 'done' && styles.fabDisabled]}
        onPress={handlePress}
        disabled={status === 'uploading' || status === 'processing'}
        activeOpacity={0.7}
      >
        <Text style={styles.fabIcon}>{"\ud83d\udcf7"}</Text>
      </TouchableOpacity>
    </View>
  )
}

const styles = StyleSheet.create({
  wrapper: {
    position: 'absolute',
    bottom: 24,
    right: 20,
    alignItems: 'flex-end',
  },
  fab: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.blue,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.2,
    shadowRadius: 5,
    elevation: 6,
  },
  fabDisabled: {
    opacity: 0.6,
  },
  fabIcon: {
    fontSize: 24,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.gray800,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginBottom: 8,
  },
  statusText: {
    color: colors.white,
    fontSize: 13,
    fontWeight: '600',
  },
})
