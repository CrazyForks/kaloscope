export const SERVICE_WORKER_NOTIFICATIONS_MESSAGE = 'KALOSCOPE_NOTIFICATIONS';

export type NotificationRecord = {
  id: number;
  title: string;
  content: string;
  created_at: string;
  seen: boolean;
};

export type ServiceWorkerNotificationPayload = {
  id: number;
  title: string;
  body: string;
  tag: string;
  createdAt: string;
};

export type ServiceWorkerNotificationMessage = {
  type: typeof SERVICE_WORKER_NOTIFICATIONS_MESSAGE;
  notifications: ServiceWorkerNotificationPayload[];
};

export type NotificationFormatter = (notification: NotificationRecord) => {
  title: string;
  body: string;
};

export type NotificationDispatchState = {
  initialized: boolean;
  knownIds: Set<number>;
};

export function createNotificationDispatchState(): NotificationDispatchState {
  return {
    initialized: false,
    knownIds: new Set()
  };
}

export function collectServiceWorkerNotifications(
  notifications: NotificationRecord[],
  state: NotificationDispatchState,
  format: NotificationFormatter
): ServiceWorkerNotificationPayload[] {
  const unread = notifications.filter((notification) => !notification.seen);
  const pending = state.initialized ? unread.filter((notification) => !state.knownIds.has(notification.id)) : [];

  for (const notification of unread) {
    state.knownIds.add(notification.id);
  }
  state.initialized = true;

  return pending.map((notification) => {
    const { title, body } = format(notification);
    return {
      id: notification.id,
      title,
      body,
      tag: `kaloscope-notification-${notification.id}`,
      createdAt: notification.created_at
    };
  });
}
