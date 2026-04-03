<script lang="ts">
  import { afterNavigate } from '$app/navigation';
  import { _ } from '$lib/i18n';
  import { useRegisterSW } from 'virtual:pwa-register/svelte';

  // https://vite-pwa-org.netlify.app/frameworks/sveltekit.html#prompt-for-update
  const { needRefresh, updateServiceWorker } = useRegisterSW();

  // the bottom navigation bar element
  let dockElement: Element | null = $state(document.querySelector('.dock'));
  let bottomClass = $derived(dockElement ? 'bottom-(--ks-dock-h) sm:bottom-0' : 'bottom-0');
  afterNavigate(() => (dockElement = document.querySelector('.dock')));
</script>

{#if $needRefresh}
  <div role="alert" class="fixed right-0 layer-4 m-4 rounded-box border bg-base-100 p-3 shadow-sm {bottomClass}">
    <p>{$_('app.pwa_prompt')}</p>
    <div class="mt-3 flex gap-2">
      <button class="btn btn-sm btn-submit" onclick={() => updateServiceWorker(true)}>
        {$_('action.reload')}
      </button>
      <button class="btn btn-sm" onclick={() => needRefresh.set(false)}>
        {$_('action.close')}
      </button>
    </div>
  </div>
{/if}
