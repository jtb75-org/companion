# D.D. Companion -- iOS App

The D.D. Companion mobile app is the primary interface for adults with developmental disabilities to interact with D.D., their AI independence assistant. It provides a daily briefing, conversational chat, document scanning, task management, and push notifications.

## Prerequisites

- Node.js >= 22.11.0
- Xcode 16+ with iOS Simulator
- CocoaPods (`gem install cocoapods`)
- A `GoogleService-Info.plist` from the Firebase console (not checked into version control)

## Setup

```bash
cd companion-app

# Install JS dependencies
npm install

# Install iOS native dependencies
cd ios && pod install && cd ..
```

Place `GoogleService-Info.plist` in `companion-app/ios/CompanionApp/` for Firebase Auth and FCM to work. This file is project-specific and comes from the Firebase console.

## Environment Configuration

The API base URL is set in `src/api/client.ts`. Both dev and production currently point to the staging Cloud Run backend:

```
https://companion-staging-backend-381910341082.us-central1.run.app
```

To point at a local backend, change `API_BASE` in that file.

## Running

### Simulator

```bash
npm run ios
```

This starts Metro and builds the app in the iOS Simulator. Metro must remain running in the terminal.

### Physical device

Open `ios/CompanionApp.xcworkspace` in Xcode, select your device as the run target, and build (Cmd+R). You will need a valid Apple Developer signing profile.

## Architecture

### Entry point

`App.tsx` wraps the app in `SafeAreaProvider` > `AuthProvider` > `AppNavigator`.

### Auth

`src/auth/AuthProvider.tsx` manages authentication:
- Email/password

The auth state is exposed via a React context (`useAuth` hook). On sign-out, the FCM device token is deactivated on the backend.

### Navigation

`src/navigation/AppNavigator.tsx` uses React Navigation bottom tabs with four screens:

| Tab | Screen | Purpose |
|-----|--------|---------|
| Today | `TodayScreen` | Morning briefing, daily items |
| Chat | `ChatScreen` | Conversational interface with D.D. |
| My Stuff | `MyStuffScreen` | Bills, medications, documents, todos |
| Profile | `ProfileScreen` | User settings, preferences |

Before showing tabs, the navigator checks profile completion via `/api/v1/me`. Incomplete profiles route to `OnboardingScreen`. Unauthenticated users see `LoginScreen`.

### API Client

`src/api/client.ts` is a thin `fetch` wrapper that automatically attaches the Firebase ID token as a Bearer token on every request.

### Push Notifications

- `src/hooks/usePushNotifications.ts` -- Requests permission, registers the FCM token with the backend (`POST /api/v1/me/devices`), handles foreground messages, and listens for token refresh.
- `src/notifications/backgroundHandler.ts` -- Registers the FCM background message handler.
- `Info.plist` declares `remote-notification` as a `UIBackgroundMode`.

### Other Components

- `src/components/ScanButton.tsx` -- Camera/photo-library document scanning
- `src/components/TodoCheckbox.tsx` -- Task completion toggle
- `src/components/InviteCaregiverForm.tsx` -- Caregiver invitation flow
- `src/hooks/useImageAnalysis.ts` -- Image processing for scanned documents

## Tests

```bash
npm test
```

Uses Jest with React Test Renderer. Tests live in `__tests__/`. Currently a smoke test that renders the root App component.

## Building for TestFlight

1. Open `ios/CompanionApp.xcworkspace` in Xcode.
2. Select the **CompanionApp** target.
3. Set the version and build number under General > Identity.
4. Select **Any iOS Device (arm64)** as the destination.
5. Product > Archive.
6. In the Organizer, select the archive and click **Distribute App** > **App Store Connect**.
7. The build will appear in App Store Connect for TestFlight distribution after processing.

Ensure signing certificates and provisioning profiles are configured in Xcode under Signing & Capabilities.

## Bundle IDs

| Bundle ID | Usage |
|-----------|-------|
| `com.mydailydignity.companion` | Production bundle ID, set in the Xcode project (Debug and Release) |
| `com.companionapp` | Default React Native template ID; no longer used. If you see this in build errors, verify the Xcode project settings match `com.mydailydignity.companion`. |

The bundle ID is configured in `ios/CompanionApp.xcodeproj/project.pbxproj` under `PRODUCT_BUNDLE_IDENTIFIER`. Info.plist references it via `$(PRODUCT_BUNDLE_IDENTIFIER)`.

## Permissions

The app requests these permissions (declared in `Info.plist`):

| Permission | Reason |
|------------|--------|
| Camera | Document and mail scanning |
| Photo Library | Selecting documents from photos |
| Location (When In Use) | Helping caregivers find nearby services |
| Remote Notifications | Push notifications for medication reminders, document processing |
