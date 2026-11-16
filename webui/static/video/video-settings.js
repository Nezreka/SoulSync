/*
 * SoulSync — Video settings additions.
 *
 * Every video section of the Settings page is React now (SETTINGS_PORT.md,
 * the video pass):
 *   - Quality: video-quality.tsx (Video Quality, Custom Formats, YouTube Quality)
 *   - Library: library-video-folders.tsx, library-video-organization.tsx,
 *     library-video-prefs.tsx (with video-downloads.ts, video-organization.ts,
 *     video-prefs.ts)
 *   - Sources: video-source-blocks.tsx (seeding, usenet, retry)
 *   - Connections: connections-video-api.tsx, connections-video-server.tsx and
 *     Notifications in video-row-lists.tsx (video-keys.ts, video-server.ts)
 *   - Discovery: Import Lists in video-row-lists.tsx
 * each loading on soulsync:video-page-shown and saving from the video side's
 * Save Settings click (video-shown.ts). The retired video source picker and
 * slskd form went in video pass V3c: nothing read them.
 *
 * This file goes with settings.js when the page itself is React (step V4).
 */
