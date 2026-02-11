<script lang="ts" module>
  import { pushState } from '$app/navigation';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { freeze } from '$lib/stores';
  import { escapeHTML, sniffer } from '$lib/utils';
  import type { IconifyIcon } from 'iconify-icon';
  import { tick } from 'svelte';
  import { SvelteMap } from 'svelte/reactivity';
  import { fade } from 'svelte/transition';
  import { v4 as uuidv4 } from 'uuid';

  export type Message = {
    /** Whether the dialog can be closed by navigating back. */
    shallow?: boolean;
    /** Whether to show a prompt dialog. */
    prompt: boolean;
    /** The advice to display in the prompt dialog. */
    advice?: string;
    /** The icon to display before the title. */
    icon?: string | IconifyIcon;
    /** The message title to display. */
    title: string;
    /** The message content to display. */
    message?: string;
    /** The callback function when the dialog is canceled. */
    oncancel?: () => void;
    /** The callback function when the dialog is confirmed. */
    onconfirm?: (input?: string) => void;
    /** Whether the dialog is closed explicitly. */
    explicit?: boolean;
  };

  export function prompt(msg: Omit<Message, 'prompt' | 'explicit'>) {
    show({ ...msg, prompt: true });
  }

  export function confirm(msg: Partial<Omit<Message, 'prompt' | 'advice' | 'explicit'>>) {
    show({ ...msg, prompt: false, title: msg.title ?? '' });
  }

  /**
   * Reactive map of message instances.
   */
  export const messages = new SvelteMap<string, Message>();

  /**
   * Show a message dialog.
   *
   * @param msg - The message dialog instance.
   */
  async function show(msg: Message) {
    // https://svelte.dev/docs/kit/shallow-routing
    if (msg.shallow === undefined) {
      msg.shallow = !sniffer.isAndroidEdge();
    }
    if (msg.shallow) {
      pushState('', {});
    }
    const id = `msg-${uuidv4()}`;
    msg.explicit = false;
    messages.set(id, msg);
    sniffer.isMobileSafari() && freeze.set(true);
    // wait for the dialog to be added to the DOM
    await tick();
    (document.getElementById(id) as HTMLDialogElement | null)?.showModal();
  }

  /**
   * Close a message dialog.
   *
   * @param id - The id of the message dialog.
   * @param shallow - Whether to close the dialog by navigating back.
   * @param callback - The callback function to be called before closing the dialog.
   * @param event - The mouse event that triggered the close action.
   */
  function close(id: string, shallow?: boolean, callback?: () => void, event: MouseEvent | null = null) {
    if (event) {
      event.preventDefault();
    }
    let msg = messages.get(id);
    if (msg) {
      callback?.();
      msg.explicit = true;
      if (shallow) {
        history.back();
      } else {
        messages.delete(id);
        freeze.set(false);
      }
    }
  }

  /**
   * Convert part of the title to italic format.
   *
   * @param title - The title string.
   * @return The HTML string with italicized parts.
   */
  function italic(title: string): string {
    title = escapeHTML(title);
    return title.replace(
      /\[([^\]]+)\]/g,
      '<span class="text-sm font-normal opacity-60">[<span class="italic px-1">$1</span>]</span>'
    );
  }
</script>

<!-- close the topmost message dialog when the user navigates back -->
<svelte:window
  onpopstate={(event) => {
    if (messages.size > 0) {
      const [id, msg] = Array.from(messages.entries()).pop() ?? [];
      if (id && msg && msg.shallow) {
        event.stopPropagation();
        if (!msg.explicit) {
          msg.oncancel?.();
        }
        messages.delete(id);
        freeze.set(false);
      }
    }
  }}
/>

{#each messages.entries() as [id, msg] (id)}
  {@const icon = msg.icon || (msg.prompt ? icons.info : icons.warning)}
  <dialog {id} class="modal transition-none" transition:fade={{ duration: 200 }}>
    <form method="dialog" class="modal-backdrop">
      <button onclick={(event) => close(id, msg.shallow, msg.oncancel, event)} aria-label="Close"></button>
    </form>
    <div class="modal-box max-w-xl">
      <form method="dialog" class="modal-corner">
        <button onclick={(event) => close(id, msg.shallow, msg.oncancel, event)}>✕</button>
      </form>
      <!-- message title -->
      <h3 class="modal-title">
        <iconify-icon {icon} width="1.5rem" class="size-6 opacity-90"></iconify-icon>
        <!-- eslint-disable-next-line svelte/no-at-html-tags -->
        {@html italic(msg.title || $_('message.default.title'))}
      </h3>
      <!-- message content -->
      <p class="opacity-90">
        {msg.message || $_('message.default.content')}
      </p>
      {#if msg.prompt}
        <input type="text" class="input w-full" value={msg.advice} />
      {/if}
      <!-- action buttons -->
      <div class="modal-action">
        <button class="btn" onclick={() => close(id, msg.shallow, msg.oncancel)}>
          {$_('message.cancel')}
        </button>
        <button
          class="btn btn-submit"
          onclick={() => {
            close(id, msg.shallow, () => {
              if (msg.onconfirm) {
                if (msg.prompt) {
                  msg.onconfirm((document.querySelector(`#${id} input`) as HTMLInputElement | null)?.value);
                } else {
                  msg.onconfirm();
                }
              }
            });
          }}
        >
          {$_('message.confirm')}
        </button>
      </div>
    </div>
  </dialog>
{/each}
