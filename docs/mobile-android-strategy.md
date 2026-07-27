# Mobile and Android Strategy

Android should come after the hosted backend, not before it. The current app is a strong local/web dogfooding product, but mobile requires authenticated, user-scoped, server-authoritative APIs.

## Why Android Comes Later

Android needs:

- a stable hosted API;
- auth/session handling;
- per-user data ownership;
- network-safe error semantics;
- background notification strategy;
- privacy-safe logs and crash reports;
- Play Store policy readiness.

Building Android against the current single-user/global backend would create rework in auth, ownership, sync and monetization.

## Android API Needs

Core APIs that map well to Android once authenticated:

- task list and search;
- task create/edit/complete/delete/restore;
- task detail metadata and notes;
- Hoy and Ahora planning;
- Inbox processing;
- reminders list/create/cancel;
- confirmations review/confirm/cancel;
- safe system status subset;
- LLM suggestion status and suggestion apply with quotas.

APIs that should be admin/web-only:

- full Settings editor;
- Trello board discovery/mapping;
- backups and restore-plan;
- full diagnostics/support bundles;
- dev simulator endpoints;
- raw Telegram processing endpoints.

Recommended API shape:

- introduce `/api/v1` before Android.
- return stable machine-readable error codes.
- use pagination or updated-since cursors.
- avoid UI-specific strings as the only contract.
- keep writes server-authoritative.

## Auth and Session Strategy Options

### Option 1 - Cookie Session for Web, Token Pair for Mobile

Pros:

- Good web security posture with HttpOnly cookies.
- Mobile can use access/refresh tokens.

Cons:

- Two auth surfaces to test.
- Requires careful CSRF design for web.

### Option 2 - OAuth/OIDC Provider

Pros:

- Offloads login, password reset and MFA.
- Friendly to mobile.

Cons:

- Adds provider dependency and cost/complexity.

### Option 3 - First-Party JWT Only

Pros:

- Simple mental model for mobile.

Cons:

- Easy to get wrong around refresh, revocation and storage.
- Web CSRF/XSS considerations still matter.

Recommendation:

- Phase 2 can start with web session auth for one admin.
- M19D recommends a hybrid long-term model: cookie session + CSRF for web, bearer access token + rotating refresh token for Android/mobile.
- Android refresh tokens should live in Android Keystore and be revocable per device/session.
- Phase 3/4 can still revisit OIDC before public multi-user if password reset/MFA requirements grow.
- Android should not ship until refresh/revocation/device sessions are tested.

Detailed design: [Auth, user scope and request context design](auth-user-scope-design.md) and [Auth API boundary](auth-api-boundary.md).

## Push Notification Considerations

Possible notification channels:

- Telegram remains an integration/notification channel.
- Android push via FCM for native reminders and planning prompts.
- Email can be considered later for account/security events.

Design constraints:

- Push payloads should avoid sensitive task content by default.
- Notification preferences must be per user.
- Weekend/vacation modes need server-side enforcement.
- Failed push should not drop reminders silently.
- Device tokens are secrets and must be user-scoped.

## Offline and Cache Considerations

Useful cacheable data:

- active task lists;
- task detail;
- Hoy/Ahora read models;
- settings summary;
- pending reminders/confirmations summary.

Server-authoritative data:

- completion/deletion/restoration state;
- Trello writes;
- confirmations;
- reminders delivery state;
- billing/entitlements;
- integration credentials.

Offline v1 recommendation:

- Start read-through cache only.
- Allow offline draft creation later, with explicit sync/conflict UI.
- Do not allow offline Trello writes or destructive actions.

## Play Store Readiness Checklist

Before Play Store:

- Privacy policy.
- Terms/account deletion path.
- Clear data export/delete behavior.
- Crash reporting with redaction.
- No secrets in app package.
- Secure token storage.
- Notification permission UX.
- Background work policy compliance.
- Ads SDK privacy review if ads are used.
- Internal test track with real RC checklist.

## Free, Premium and Ads Considerations

Possible premium features:

- higher AI suggestion quota;
- more Trello boards/integrations;
- advanced planning views;
- mobile push;
- longer backup/history;
- shared/team spaces later.

Free tier constraints:

- task count or active task count;
- AI suggestion quota;
- one integration/board;
- limited history;
- ads if chosen.

Ads caution:

- Do not design ads before auth and user model.
- Do not send task content to ad networks.
- Keep ads away from sensitive task detail screens if possible.
- Confirm Play Store data safety disclosures before adding an ads SDK.

## What to Avoid for Now

- No Android implementation before hosted auth/user ownership.
- No mobile-specific backend fork.
- No local-only API assumptions in Android.
- No Trello/Telegram tokens embedded in mobile.
- No mobile push until notification preferences are user-scoped.
- No monetization or ads in the local dogfooding runtime.
