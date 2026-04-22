<script lang="ts" module>
  import type { Notification } from '$lib/types';

  export type NotificationsProps = {
    /** The class names for the wrapper element. */
    class?: string;
    /** The class names for the trigger button. */
    triggerClass?: string;
  };

  const MOCK_NOTIFICATIONS: Notification[] = [
    {
      id: 1,
      title: 'Indexer task completed',
      content: 'The Bangumi calendar index finished successfully and 24 items were updated.',
      seen: false,
      created_at: '2026-04-22T09:30:00Z',
      updated_at: '2026-04-22T09:30:00Z'
    },
    {
      id: 2,
      title: 'Download queue paused',
      content: 'The downloader paused because the remote server responded with a temporary rate limit.',
      seen: false,
      created_at: '2026-04-22T08:10:00Z',
      updated_at: '2026-04-22T08:10:00Z'
    },
    {
      id: 3,
      title: 'New workflow available',
      content: 'A new media cleanup workflow was added to the shared library and can be imported now.',
      seen: true,
      created_at: '2026-04-21T18:40:00Z',
      updated_at: '2026-04-21T18:40:00Z'
    }
  ];

  let notifications: Notification[] = $state([...MOCK_NOTIFICATIONS]);
  let count = $derived(notifications.length);
</script>

<script lang="ts">
  import { Modal } from '$lib/components';
  import { _, dateTime, number } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { class: _class, triggerClass }: NotificationsProps = $props();

  // the modal dialog for the notifications center
  let modal: Modal;

  export const showModal = () => modal.show();
  export const getCount = () => {
    return count;
  };

  function removeNotification(id: number) {
    notifications = notifications.filter((notification) => notification.id !== id);
  }

  function clearNotifications() {
    notifications = [];
  }
</script>

<div class="indicator {_class}">
  {#if count > 0}
    <span class="indicator-item mt-1 badge badge-xs badge-primary">
      {count > 99 ? '99+' : count}
    </span>
  {/if}
  <button
    class="btn h-10 min-h-10 btn-subtle px-3 {triggerClass}"
    onclick={showModal}
    aria-label={$_('app.notifications')}
  >
    <iconify-icon icon={icons.alertUrgent} width="1.625rem" class="size-6.5 opacity-90"></iconify-icon>
  </button>
</div>

<Modal icon={icons.alertUrgent} title={$_('app.notifications')} maxWidth="42rem" bind:this={modal}>
  <div class="flex items-center justify-between gap-2">
    <span class="mx-1 text-sm font-semibold opacity-50">
      {$_('data.paginator.total', $number(count))}
    </span>
    <button class="btn btn-subtle btn-sm" onclick={clearNotifications} disabled={count === 0}>
      <iconify-icon icon={icons.delete} width="1rem"></iconify-icon>
      {$_('action.clear', $_('entity.notifications'))}
    </button>
  </div>
  {#if notifications.length === 0}
    <div class="rounded-box border border-dashed py-10 text-center text-sm opacity-50">
      {$_('data.nodata')}
    </div>
  {:else}
    <ul class="flex max-h-[50vh] flex-col gap-3 overflow-y-auto">
      {#each notifications as notification (notification.id)}
        <li class="flex items-start justify-between gap-1 rounded-box border p-2 shadow-sm">
          <div class="min-w-0 flex-1 space-y-2 pt-1.5 pl-2">
            <div class="flex items-center gap-2">
              {#if !notification.seen}
                <span class="status animate-bounce status-info"></span>
              {/if}
              <h4 class="truncate text-sm font-semibold text-surface">
                {notification.title}
              </h4>
            </div>
            <p class="text-sm leading-6 whitespace-pre-wrap opacity-80">
              {notification.content}
            </p>
            <time class="text-xs opacity-50">
              {$dateTime(notification.created_at)}
            </time>
          </div>
          <button
            class="btn btn-square btn-subtle btn-sm"
            aria-label={$_('action.delete', $_('entity.notification'))}
            onclick={() => removeNotification(notification.id)}
          >
            <iconify-icon icon={icons.deleteDismiss} width="1rem"></iconify-icon>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</Modal>
