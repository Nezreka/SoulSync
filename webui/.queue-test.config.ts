import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'./tests', testMatch:'queue-layout.spec.ts', use:{launchOptions:{channel:'msedge'}}, workers:1});
