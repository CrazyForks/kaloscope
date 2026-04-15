<script lang="ts" module>
  import type { Resp, URLRule } from '$lib/types';

  type URLRuleEditorProps = Partial<{
    id: number;
    pattern: string;
    onsave: (result: URLRule) => void;
  }>;
</script>

<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { Label, Modal } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { id, pattern, onsave }: URLRuleEditorProps = $props();

  // the modal dialog instance
  let modal: Modal;
  export const showModal = () => modal.show();

  // the loading state and form schema
  const loading = createLoading();
  const schema = createFormSchema(({ text }) => ({
    pattern: text().maxlength(245)
  }));

  // the HTTP/HTTPS prefixes
  const HTTP = 'http://';
  const HTTPS = 'https://';
  let secure: boolean = $state(true);

  /**
   * Save or update the URL rule.
   *
   * @param form - The form element.
   * @param data - The form data.
   */
  function upsert(form: HTMLFormElement, data: FormData) {
    loading.start();
    const jsonData: Record<string, unknown> = Object.fromEntries(data);
    jsonData.id = id;
    jsonData.pattern = `${secure ? HTTPS : HTTP}${pattern}`;
    api
      .post('network/rule/upsert', { json: jsonData })
      .json<Resp<URLRule>>()
      .then((resp) => {
        modal.close();
        onsave?.(resp.data);
        setTimeout(() => form.reset(), 200);
      })
      .finally(() => {
        loading.end();
      });
  }

  /**
   * Standardize the URL.
   *
   * @param url - The URL to standardize.
   * @returns The standardized URL.
   */
  function standardize(url: string): string {
    url = url.trim();
    if (url.toLowerCase().startsWith(HTTP)) {
      secure = false;
      return url.slice(7);
    } else if (url.toLowerCase().startsWith(HTTPS)) {
      secure = true;
      return url.slice(8);
    }
    return url;
  }

  $effect(() => {
    pattern = standardize(pattern ?? '');
  });
</script>

<Modal icon={icons.globeDesktop} title={$_(id ? 'action.edit' : 'action.add', $_('entity.rule'))} bind:this={modal}>
  <form
    method="post"
    use:enhance={({ formElement, formData, cancel }) => {
      cancel();
      upsert(formElement, formData);
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('field.pattern')}</Label>
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <label class="input w-full gap-0" onclick={(event) => event.preventDefault()}>
        <button type="button" class="cursor-pointer opacity-80" onclick={() => (secure = !secure)}>
          {secure ? HTTPS : HTTP}
        </button>
        <input placeholder="*" class="grow truncate" bind:value={pattern} {...schema.pattern} />
      </label>
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>
        {$_('message.cancel')}
      </button>
      <button type="submit" class="btn btn-submit" disabled={$loading !== null}>
        {$_('message.confirm')}
        {#if $loading}
          <span class="loading loading-xs loading-dots"></span>
        {/if}
      </button>
    </div>
  </form>
</Modal>
