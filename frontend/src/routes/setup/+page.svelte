<script lang="ts">
  import { enhance } from '$app/forms';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import { Label, Logo, Navbar, alert } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _, headTitle } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { Resp } from '$lib/types';
  import { onMount } from 'svelte';

  // the loading state and form schema
  const loading = createLoading();
  const schema = createFormSchema(({ text, password }) => ({
    username: text().maxlength(32),
    password: password().minlength(6).maxlength(64).autocomplete('new-password'),
    confirm_pwd: password().minlength(6).maxlength(64).autocomplete('new-password')
  }));

  /**
   * Create the first admin user and redirect to login page.
   *
   * @param data - The form data.
   */
  function createAdmin(data: FormData) {
    if (data.get('password') !== data.get('confirm_pwd')) {
      alert({ level: 'error', message: 'passwords_mismatch' });
      return;
    }
    loading.start();
    api
      .post('user/create_admin', { body: data })
      .then(() => {
        goto('/login');
      })
      .finally(() => {
        loading.end();
      });
  }

  onMount(() => {
    // check if there are already users, if so redirect to login
    api
      .get('user/count')
      .json<Resp<number>>()
      .then((resp) => {
        if (resp.data > 0) {
          goto('/login');
        }
      });
  });
</script>

<svelte:head>
  <title>{$headTitle('app.setup')}</title>
</svelte:head>

<Navbar />
<main class="hero min-h-(--ks-lvh) bg-base-200">
  <div class="hero-content mb-20 w-full flex-col gap-6">
    <div class="flex-center flex-col gap-4">
      <div class="flex-center gap-2">
        <Logo size="2.5rem" />
        <span class="app-name text-4xl text-shadow-md">{$_('app.name', { locale: 'en-US' })}</span>
      </div>
      <div class="text-center">
        <h2 class="text-2xl font-bold text-content">{$_('app.setup_welcome')}</h2>
        <p class="mt-2 text-sm opacity-70">{$_('app.setup_description')}</p>
      </div>
    </div>
    <div class="card w-full max-w-96 border bg-base-100 shadow-2xl">
      <form
        method="post"
        class="card-body"
        use:enhance={({ formData, cancel }) => {
          cancel();
          createAdmin(formData);
        }}
      >
        <fieldset class="fieldset">
          <Label small>{$_('model.field.username')}</Label>
          <label class="input w-full">
            <iconify-icon icon={icons.user}></iconify-icon>
            <input class="grow" {...schema.username} />
          </label>
          <Label small>{$_('password.initial')}</Label>
          <label class="input w-full">
            <iconify-icon icon={icons.key}></iconify-icon>
            <input class="grow" {...schema.password} />
          </label>
          <Label small>{$_('password.confirm')}</Label>
          <label class="input w-full">
            <iconify-icon icon={icons.key}></iconify-icon>
            <input class="grow" {...schema.confirm_pwd} />
          </label>
          <button type="submit" class="btn mt-6 btn-primary" disabled={$loading !== null}>
            {$_('app.setup_create_admin')}
            {#if $loading}
              <span class="loading loading-xs loading-dots"></span>
            {/if}
          </button>
        </fieldset>
      </form>
    </div>
  </div>
</main>
