<script lang="ts">
  import { api } from '$lib/api';
  import { Container, Label, Select, Setting } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { user } from '$lib/stores';
  import { onMount } from 'svelte';

  /**
   * Update the preference value.
   *
   * @param key - The preference key.
   */
  function update(key: string) {
    if ($user?.preferences) {
      api
        .post('user/update_pref', {
          json: { key: key, value: $user.preferences[key] }
        })
        .catch(() => {
          user.set(null);
        });
    }
  }

  // the loading state
  const loading = createLoading();

  onMount(() => {
    // refresh user info when mounted
    loading.start();
    user.set(null);
    $effect(() => {
      $user && loading.end();
    });
  });
</script>

<Container type="settings" loading={$loading}>
  {#if $user?.preferences}
    <Setting title={$_('preference.navigation.title')}>
      <fieldset class="fieldset">
        <Label>{$_('preference.navigation.homepage')}</Label>
        <Select
          options={[
            { value: '/dashboard', label: 'nav.dashboard.title' },
            { value: '/websearch', label: 'nav.websearch.title' },
            { value: '/medialibs', label: 'nav.medialibs.title' },
            { value: '/downloads', label: 'nav.downloads.title' },
            { value: '/settings', label: 'nav.settings.title' }
          ]}
          bind:value={$user.preferences.homepage}
          onchange={() => update('homepage')}
          class="w-full"
        />
      </fieldset>
      <fieldset class="fieldset">
        <Label tip={$_('preference.navigation.vibration.tip')}>{$_('preference.navigation.vibration.title')}</Label>
        <Select
          options={[
            { value: false, label: 'action.toggle_off' },
            { value: true, label: 'action.toggle_on' }
          ]}
          bind:value={$user.preferences.vibration}
          onchange={() => update('vibration')}
          class="w-full"
        />
      </fieldset>
    </Setting>
    <Setting title={$_('preference.dashboard.title')} tip={$_('preference.dashboard.tip')}>
      <div>
        <fieldset class="fieldset grid-cols-2">
          <Label class="my-2">{$_('preference.dashboard.search')}</Label>
          <input
            type="checkbox"
            class="toggle self-center justify-self-end"
            bind:checked={$user.preferences.recent_searches}
            onchange={() => update('recent_searches')}
          />
        </fieldset>
        <fieldset class="fieldset grid-cols-2">
          <Label class="my-2">{$_('preference.dashboard.watch')}</Label>
          <input
            type="checkbox"
            class="toggle self-center justify-self-end"
            bind:checked={$user.preferences.recent_watches}
            onchange={() => update('recent_watches')}
          />
        </fieldset>
      </div>
    </Setting>
    <Setting title={$_('preference.privacy.title')} tip={$_('preference.privacy.tip')}>
      <fieldset class="fieldset">
        <Label>{$_('preference.privacy.search')}</Label>
        <Select
          options={[
            { value: 0, label: 'preference.privacy.untrack' },
            { value: 1, label: `1 ${$_('duration.day').toLowerCase()}` },
            { value: 3, label: `3 ${$_('duration.days').toLowerCase()}` },
            { value: 7, label: `7 ${$_('duration.days').toLowerCase()}` }
          ]}
          bind:value={$user.preferences.search_records}
          onchange={() => update('search_records')}
          class="w-full"
        />
      </fieldset>
      <fieldset class="fieldset">
        <Label>{$_('preference.privacy.watch')}</Label>
        <Select
          options={[
            { value: 0, label: 'preference.privacy.untrack' },
            { value: 1, label: `1 ${$_('duration.day').toLowerCase()}` },
            { value: 3, label: `3 ${$_('duration.days').toLowerCase()}` },
            { value: 7, label: `7 ${$_('duration.days').toLowerCase()}` }
          ]}
          bind:value={$user.preferences.watch_records}
          onchange={() => update('watch_records')}
          class="w-full"
        />
      </fieldset>
    </Setting>
  {/if}
</Container>
