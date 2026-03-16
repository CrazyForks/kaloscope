<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { Label, Modal, alert } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { username, oncreate }: { username?: string; oncreate?: () => void } = $props();

  // the modal dialog instance
  let modal: Modal;
  export const showModal = () => modal.show();

  // the loading state and form schema
  const loading = createLoading();
  const schema = createFormSchema(({ text, password }) => ({
    username: text().maxlength(32),
    password: password().minlength(6).maxlength(64).autocomplete('new-password'),
    confirm_pwd: password().minlength(6).maxlength(64).autocomplete('new-password')
  }));

  /**
   * Create a new user with given data.
   *
   * @param form - The form element.
   * @param data - The form data.
   */
  function create(form: HTMLFormElement, data: FormData) {
    if (data.get('password') !== data.get('confirm_pwd')) {
      alert({ level: 'error', message: 'passwords_mismatch' });
      return;
    }
    loading.start();
    api
      .post('user/create', { body: data })
      .then(() => {
        modal.close();
        oncreate?.();
        setTimeout(() => form.reset(), 200);
      })
      .finally(() => {
        loading.end();
      });
  }
</script>

<Modal icon={icons.personAdd} title={$_('action.add', $_('entity.user'))} bind:this={modal}>
  <form
    method="post"
    use:enhance={({ formElement, formData, cancel }) => {
      cancel();
      create(formElement, formData);
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('field.username')}</Label>
      <label class="input w-full">
        <iconify-icon icon={icons.user}></iconify-icon>
        <input placeholder={$_('field.username')} class="grow" bind:value={username} {...schema.username} />
      </label>
      <Label required>{$_('field.password')}</Label>
      <label class="input w-full">
        <iconify-icon icon={icons.key}></iconify-icon>
        <input placeholder={$_('password.initial')} class="grow" {...schema.password} />
      </label>
      <label class="input w-full">
        <iconify-icon icon={icons.key}></iconify-icon>
        <input placeholder={$_('password.confirm')} class="grow" {...schema.confirm_pwd} />
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
