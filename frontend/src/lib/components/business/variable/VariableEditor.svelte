<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { Label, Modal } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { GlobalVariable, Resp } from '$lib/types';

  type VariableEditorProps = Partial<{
    id: number;
    key: string;
    value: string;
    encrypted: boolean;
    onsave: (result: GlobalVariable) => void;
  }>;
  let { id, key, value, encrypted = false, onsave }: VariableEditorProps = $props();

  // the modal dialog instance
  let modal: Modal;
  export const showModal = () => modal.show();

  // the loading state and form schema
  const loading = createLoading();
  const schema = createFormSchema(({ text }) => ({
    key: text().maxlength(64),
    value: text().maxlength(255)
  }));

  /**
   * Save or update the variable.
   *
   * @param form - The form element.
   * @param data - The form data.
   */
  function upsert(form: HTMLFormElement, data: FormData) {
    loading.start();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const jsonData: Record<string, any> = Object.fromEntries(data);
    jsonData.id = id;
    jsonData.encrypted = encrypted;
    api
      .post('variable/upsert', { json: jsonData })
      .json<Resp<GlobalVariable>>()
      .then((resp) => {
        modal.close();
        onsave?.(resp.data);
        setTimeout(() => form.reset(), 200);
      })
      .finally(() => {
        loading.end();
      });
  }
</script>

<Modal
  icon={icons.bracesVariable}
  title={$_(id ? 'action.edit' : 'action.add', $_('model.variable'))}
  bind:this={modal}
>
  <form
    method="post"
    use:enhance={({ formElement, formData, cancel }) => {
      cancel();
      upsert(formElement, formData);
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('model.field.name')}</Label>
      <input
        placeholder={$_('model.field.name')}
        class="input w-full"
        bind:value={key}
        {...schema.key}
        disabled={!!id}
      />
      <Label required>{$_('model.field.value')}</Label>
      <label class="input w-full">
        <input
          placeholder={$_('model.field.value')}
          class="grow"
          bind:value
          {...schema.value}
          type={encrypted ? 'password' : 'text'}
        />
        <button
          type="button"
          aria-label="Encrypted"
          class="flex-center {!!id ? 'cursor-not-allowed' : 'cursor-pointer'}"
          onclick={() => (encrypted = !encrypted)}
          disabled={!!id}
        >
          <iconify-icon
            icon={encrypted ? icons.lockClosed : icons.lockOpen}
            width="1.5rem"
            class="text-base-content/50 transition-colors duration-200"
            class:hover:text-base-content={!id}
          ></iconify-icon>
        </button>
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
