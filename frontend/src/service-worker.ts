/// <reference types="vite/client" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />
import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching';
import {
  SERVICE_WORKER_NOTIFICATIONS_MESSAGE,
  type ServiceWorkerNotificationMessage,
  type ServiceWorkerNotificationPayload
} from './lib/notifications';

declare let self: ServiceWorkerGlobalScope;

function isNotificationPayload(value: unknown): value is ServiceWorkerNotificationPayload {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const payload = value as Partial<ServiceWorkerNotificationPayload>;
  return (
    typeof payload.id === 'number' &&
    typeof payload.title === 'string' &&
    typeof payload.body === 'string' &&
    typeof payload.tag === 'string' &&
    typeof payload.createdAt === 'string'
  );
}

function isServiceWorkerNotificationMessage(data: unknown): data is ServiceWorkerNotificationMessage {
  if (!data || typeof data !== 'object') {
    return false;
  }
  const message = data as Partial<ServiceWorkerNotificationMessage>;
  return (
    message.type === SERVICE_WORKER_NOTIFICATIONS_MESSAGE &&
    Array.isArray(message.notifications) &&
    message.notifications.every(isNotificationPayload)
  );
}

async function showNotifications({ notifications }: ServiceWorkerNotificationMessage) {
  await Promise.all(
    notifications.map((notification) =>
      self.registration.showNotification(notification.title, {
        body: notification.body,
        tag: notification.tag,
        timestamp: Date.parse(notification.createdAt) || Date.now(),
        data: { id: notification.id }
      })
    )
  );
}

// activate the waiting service worker after the user accepts the update prompt
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  } else if (isServiceWorkerNotificationMessage(event.data)) {
    event.waitUntil(showNotifications(event.data));
  }
});

// keep mutable app shell files on the network so deployment cache headers can take effect
const mutableFrontendPaths = new Set(['/', '/index.html', '/404.html', '/manifest.webmanifest', '/_app/version.json']);
// filter the injected Workbox manifest before precaching
const precacheManifest = self.__WB_MANIFEST.filter((entry) => {
  const url = typeof entry === 'string' ? entry : entry.url;
  const { pathname } = new URL(url, self.location.origin);
  return !mutableFrontendPaths.has(pathname) && !pathname.endsWith('.html');
});

// precache immutable build assets only
precacheAndRoute(precacheManifest);

// clean up incompatible Workbox caches
cleanupOutdatedCaches();
